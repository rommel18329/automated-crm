# Finish the AI price-approval refactor

Status: IMPLEMENTED 2026-10-16 on `feature/ai-price-approval` (uncommitted). User decisions:
every paid AI call asks for approval (D1 = manual only, D3 = yes). D2: single priced call per
email analysis (chunking + reviewer pass not restored; tests rewritten). D4: not done.
pytest: 160 passed. Live end-to-end paid run not verified: the Anthropic account had no credit.

## Goal
Every paid Anthropic call goes through `crm_engine/ai_budget.py`: free token count →
price quote → approval → `run_anthropic()` → ledger row in `.ai-local/usage.jsonl`, all
under the daily limit. The approval queue and the hot-lead rule stay as they are. Nothing
here sends a message.

## Where things stand (uncommitted working tree, 2026-10-16)
- `ai_budget.py` is done: quotes, fingerprints, 30-min expiry, daily cap, ledger.
- `ai_message_generator.py`, `followup_engine.py` and `email_opportunities.py` were changed
  to `prepare_*()` + `run_anthropic(approval, ...)`.
- **None of the callers pass an approval.** `run_anthropic(None, ...)` raises
  `TypeError` inside `AIQuote.from_value`, and every caller swallows the error. So text
  drafts, the daily follow-up queue, coaching and email analysis all quietly return
  blank or failed results.
- The refactor also **removed** these, and they need your sign-off under the no-delete rule:
  - the auto-note after every text (`messaging_gateway.send_sms`, `webhook_server.inbound_sms`)
  - the AI call note and the call-fact extraction (motivation score update) after each transcript
  - email analysis chunking for long histories (over 65k characters) and the second
    "skeptical reviewer" pass on opportunities
  - the non-empty `DEFAULT_FALLBACK` text (now `""`, so a failed draft is empty rather than generic)
  - the email reviewer model moved from Sonnet to Haiku
- Still unmetered: `buyer_match.py` (uses `client`, restored today as a stopgap),
  `email_review.suggest`, `dialer_import._call_claude_batch`, and `update_timelines.py`
  (OpenAI, a manual script).

## ⚠️ Live damage, needs action before anything else
The scheduled task **"Silverline CRM Gmail Sync"** runs `python -m crm_engine.email_sync`
every 15 minutes. `email_review.store_thread()` calls `analyze(thread)` with no approval,
gets a failed result, and **overwrites the stored opportunity analysis and its draft**.
- crm.db right now: 125 threads, 64 complete, **13 failed today, all `TypeError`**.
  Opportunities are re-analyzed daily, so today's first sync replaced them.
- No money is spent; the call fails before any API request.
- `crm.db.backup-edin-investor-20261013-190141` (Oct 13) probably still holds the earlier
  analyses for those 13 threads.

Proposed immediate steps (each needs your OK):
1. Pause the task: `Disable-ScheduledTask -TaskName "Silverline CRM Gmail Sync"`
   (re-enable with `Enable-ScheduledTask`). Until then, don't open the Opportunities page
   either, because it runs `catch_up_if_due()`.
2. Optional: restore the 13 threads' `opportunity` blocks from the Oct 13 backup.
   Exact UPDATE statements and thread IDs will be shown before anything runs (database-safety rule).

## Decisions needed from you
- **D1. Background AI.** These have no human present: the daily follow-up queue
  (`engine.build_followup_queue`, frozen file), the scheduled email sync, and the auto-notes
  that were removed.
  - **A. Manual only.** Background jobs never call AI. Drafts and notes appear only after you
    click "Check price → Generate" in the dashboard. Queue rows show "Draft with AI (~$0.0004)".
  - **B. Budgeted auto-approval.** Background jobs auto-quote and run only if the call fits
    under a separate per-day auto allowance (e.g. $0.25), inside the overall daily limit.
    Every call is still ledgered as `approved_by: auto`. This can bring back auto-notes and
    call notes with a hard cost ceiling.
  - Mixed is possible, e.g. B for email sync and follow-up drafts, A for coaching and buyer match.
- **D2. Email analysis depth.** Restore chunking and the skeptical-review pass, priced as
  one bundled quote covering all calls? Or keep single-call and delete the two tests below?
- **D3. Scope.** Also move `buyer_match`, `email_review.suggest` and `dialer_import` onto
  `ai_budget`? Recommended: yes, so the daily limit covers everything. `update_timelines.py`
  is a manual OpenAI script: leave it out.
- **D4.** Restore the 13 overwritten analyses (step 2 above)?

## Planned changes (assumes D1 = A for the UI pieces, D3 = yes; adjusted after your answers)

### 1. `crm_engine/ai_budget.py` (small edits)
- `run_anthropic`: a `None` quote raises `AIApprovalError("No price approval for <feature>")`
  instead of `TypeError`.
- Add `usage_cost(model, input_tokens, output_tokens) -> float` so callers can report real
  cost again (they currently hard-code `cost_usd=0.0`).
- If D1 includes B: add `auto_approve(request) -> AIQuote | None`, which returns a quote only
  when it fits the auto allowance; add `auto_daily_limit_usd` to `.ai-local/settings.json`.

### 2. `crm_engine/ai_message_generator.py`
- Return `cost_usd = usage_cost(...)` from `generate_message` and `generate_coaching_report`.
- Remove the stopgap `client` line once `buyer_match` no longer imports it (asks first, per no-delete).
- Remove the unused `quote_anthropic` import only with your OK.

### 3. `crm_engine/email_review.py` + `email_opportunities.py`
- `store_thread`: if analysis failed because there was no approval, **keep the previous
  opportunity block** and mark the thread `analysis_state: "pending_price"`. Never overwrite
  a complete analysis with a failed one.
- `suggest()`: move to `prepare` + `run_anthropic` (D3).
- D2 = restore: bring back the chunk + reviewer calls; `prepare_analysis_request` returns a
  list of requests and the quote sums them.

### 4. `crm_engine/buyer_match.py`, `crm_engine/dialer_import.py` (D3)
- Split each into `prepare_*()` + `run_*(approval)` like `ai_message_generator`.

### 5. New `crm_engine/ai_ui.py` (keeps `dashboard.py` edits small)
- `priced_action(key, label, prepare_fn, run_fn)`: Streamlit helper. "Check price" calls
  `prepare_fn()` + `quote_anthropic()` (free) and shows `quote_lines()`. "Generate ($x)" calls
  `run_fn(approval=quote, prepared=request)`. Quote and request live in `st.session_state`, so
  the same prompt is priced and run.
- `budget_panel()`: today/month spend, remaining, per-feature breakdown, daily limit input.

### 6. `dashboard.py` (edit only these call sites)
- Lines ~650, ~1121, ~2217: follow-up drafts → `priced_action`.
- ~1455 / ~1485: buyer match + buyer text → `priced_action`.
- ~1916: coaching report → `priced_action`.
- ~2868 / ~2881: contact parse → `priced_action` (AI mode only; table parse stays free).
- Sidebar: `budget_panel()`.
- `dashboard_v2.py` and `legacy_ui/` are not touched. They'll show blank drafts. Flagging only.

### 7. Not touched
`engine.py` (frozen). With D1 = A the queue gets blank drafts plus a "Draft with AI" button
added in the dashboard. With D1 = B, `followup_engine` auto-approves when `approval is None`.
Neither needs an `engine.py` edit. Nothing in `messaging_gateway.send_sms` or the approval
queue changes, and there are no schema changes (quotes live in session state and the JSON ledger).

## Tests
Shared fixture: monkeypatch `ai_budget.LEDGER` / `SETTINGS` to `tmp_path` so tests never
read or write the real ledger.

| Test | Change |
|---|---|
| `test_ai_message.py::test_ai_message` | Stale: fakes the OpenAI `responses` API (`gpt-5-mini`) and only passed because of the old fallback text. Rewrite to fake `ai.run_anthropic` and assert a real draft comes back. |
| `test_ai_message_generator.py::test_current_instructions_...` | Replace `monkeypatch.setattr(ai, "client", ...)` with a fake `ai.run_anthropic(approval, **kw)` that calls the same `create`. `cost_usd > 0` passes again via `usage_cost`. |
| `test_sms_context.py::test_generator_includes_email_...` | Same one-line fake swap. |
| `test_email_opportunities.py::test_large_history_...` | D2 = restore: unchanged. D2 = drop: delete (with your OK) and replace with a test that a >160k history is truncated the same way in pricing and in execution. |
| `test_email_opportunities.py::test_independent_audit_...` | D2 = restore: unchanged. D2 = drop: delete (with your OK). |
| New `tests/test_ai_budget.py` | None approval → `AIApprovalError`; changed prompt → rejected; expired quote → rejected; over the daily cap → `AIBudgetError`; success writes one ledger row with the right cost. |
| New, in `test_email_opportunities.py` | `store_thread` with no approval keeps the previous complete analysis and draft. |

Done = `pytest tests/` fully green, the dashboard loads, and one priced draft has been
generated end to end in the running dashboard with its ledger row checked.

## Order of work
0. Pause the Gmail sync task (needs your OK). Commit or stash the unrelated changes
   (`catchup_sources.py`, `gv_gmail_import.py`, `.gitignore`) separately. Create the branch.
1. `ai_budget` fixes + `test_ai_budget.py`.
2. `store_thread` guard + test. **This stops the data loss; re-enable the sync after this.**
3. Generator cost reporting + fix the 3 generator tests.
4. Email D2 work + its 2 tests.
5. `ai_ui.py` + dashboard call sites + budget panel.
6. buyer_match / email suggest / dialer_import (D3).
7. Full pytest, a manual dashboard check, then commit.
