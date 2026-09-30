# Feature: One-time cross-channel catch-up review
**Started:** 2026-09-29
**Status:** separate batch/table proposal deferred; email opportunity/context work tracked in 2026-09-29-opportunity-review.md

## User request
Review every unanswered text, meaningful missed call and email. Exclude calls that
are merely callbacks to cold outreach with no substantive buying-home conversation.
Coordinate text and email to the same person, avoid repeating the same message,
and provide quick approval without requiring the user to read every conversation.
The user was in a car accident and is returning to work; a brief explanation of the
delay may be used where appropriate, with no invented medical details.

## Evidence and prerequisites
- Gmail review is implemented in commit 527e729, with 40 tests passing. The user
  confirmed direct sign-in as contactus@silverlineinvestment.group. OAuth client
  configuration and consent remain outstanding; mailbox content is not yet available.
- Read-only inspection found 3,514 local interactions from 2024-12-06 through
  2026-08-04: 552 inbound texts, 738 outbound texts, 84 inbound calls, 475 outbound
  calls and 1,665 notes. There are 21 leads whose latest text is inbound and 13
  unhandled unmatched inbox entries. These are candidates, not confirmed reply obligations.
- Existing messaging_gateway.send_sms is already a live Twilio sender, despite
  AGENTS.md describing it as a placeholder. This feature will not modify that file.
- Local history is not proof of current provider state. Verify coverage and reconcile
  available Twilio and Gmail records before enabling approval for each candidate.

## Proposed one-time workflow
1. Build one saved catch-up batch from all accessible local history, Twilio messages
   and call metadata, and Gmail conversations for this mailbox. The Gmail catch-up
   scan does not inherit the daily monitor's 30-day limit. Record which sources and
   date ranges were available; surface incomplete/expired history and connection failures.
   Provider reads are paginated. No imported item is written into existing CRM history
   merely because it was read; store the catch-up source snapshot in the new tables.
2. Reconcile failed versus accepted outbound messages and actual call outcomes.
   An outbound dial attempt does not prove the person was reached. Read existing
   transcripts/notes when available; flag calls without enough evidence for judgment.
3. Group only confirmed identities (normalized phone, verified email association or
   user-confirmed match). Never merge solely on similar names or a vague property reference.
   Present uncertain identity links for confirmation before coordinated outreach.
4. Determine whether there is a meaningful unanswered request. Exclude opt-outs,
   wrong numbers, terminal dead/contract leads, spam and obvious automated notices
   from sending, keeping their reasons visible in the audit. Skip a simple cold-call
   return when supported by history; unknown call intent is a review item, not a guess.
5. Produce one compact card per person: name, latest meaningful request/date, short
   context summary, why a response is still needed, recommended channel, and exact
   suggested response. Put time-sensitive requests first. Provide source excerpts
   and expandable full history for verification without requiring it on every card.
6. Prefer one useful response on the channel where the person is waiting. If a second
   channel is justified, make it complementary (for example, a short text referring to
   details already sent by email), never a duplicate. Re-evaluate the other channel
   immediately after any send and suppress or update its draft.
7. Use the accident explanation only for genuine delayed conversations, at most once
   per person across channels: "Sorry for the delay—I was in a car accident and am
   just getting back to work." Omit it for fresh contacts, acknowledgments and cases
   where it adds no value. The user can edit or remove it before approval.
8. Provide **Approve & next**, **Edit**, **Skip**, and **Needs my attention**. The full
   proposed text and recipient remain visible at approval time. No unattended batch
   sends. Hot leads require individual review. Call cards offer a callback action or
   mark-done control; no automatic dialing.
9. Recheck current provider messages, any cross-channel reply, opt-outs and source
   freshness immediately before every send. Claim sends atomically and retain provider
   message IDs. Uncertain outcomes block retries and remain visible for reconciliation.
10. Resume the same batch across sessions; show remaining, accepted, skipped and
    needs-attention counts. This is a one-time queue with no recurring catch-up schedule.

## Exact file plan
| File | Planned change and reason |
| --- | --- |
| dashboard.py | Small sidebar/navigation entry and delegated renderer for Catch-up Review. |
| crm_engine/catchup_sources.py (new) | Read-only Twilio/local/Gmail collection and coverage reporting; explicit manual-approved SMS transport with provider ID reporting. |
| crm_engine/catchup_review.py (new) | Evidence-based unanswered classification, identity grouping, cross-channel coordination, separate catch-up AI prompt, review transitions and send guards. |
| crm_engine/catchup_ui.py (new) | Compact review cards, progress, edits/skips, identity confirmation and manual approval controls. |
| crm_engine/db.py | Exact additive migration below, queue reads/writes, atomic claims and post-acceptance SMS history logging through existing helpers; no existing constraints altered. |
| crm_engine/gmail_client.py | Reuse existing paginated reads and explicit send transport; add small helper only if needed for whole-history coverage. |
| crm_engine/email_review.py | Coordinate accepted catch-up email replies with the daily email queue so a second approval cannot resend them. |
| tests/test_catchup_review.py (new) | Test cold callbacks, substantive calls, unanswered requests, opt-outs, uncertain identity, fresh replies, cross-channel duplication and accident-text placement. |
| tests/test_catchup_sources.py (new) | Mock provider pagination, stale/local/provider discrepancies, incomplete coverage, actual call outcomes and uncertain send handling. |
| tests/test_catchup_ui.py (new) | Verify visible recipient/text, edit/skip persistence and explicit approve-and-next behavior with mocked sends. |
| README.md | Document one-time review, coverage limitations and recovery. |
| dev/active/2026-09-29-catch-up-review.md | Track approval, implementation and validation. |

## Exact proposed SQL
No SQL has been applied for this feature. The migration is additive and separate
from the approved Gmail migration.

```sql
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS catchup_batches (
    batch_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source_coverage_json TEXT NOT NULL,
    batch_state TEXT NOT NULL CHECK (batch_state IN ('building','review','complete','blocked'))
);
CREATE TABLE IF NOT EXISTS catchup_reviews (
    review_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    contact_key TEXT NOT NULL,
    lead_id INTEGER,
    channel TEXT NOT NULL CHECK (channel IN ('sms','email','call','review')),
    recipient TEXT NOT NULL DEFAULT '',
    context_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    draft_text TEXT NOT NULL DEFAULT '',
    basis_hash TEXT NOT NULL,
    review_state TEXT NOT NULL CHECK (review_state IN
        ('needs_review','needs_attention','skipped','sending','accepted','unknown','failed','done')),
    provider_message_id TEXT,
    approved_by TEXT,
    approved_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS catchup_active_send
    ON catchup_reviews(contact_key, channel, basis_hash)
    WHERE review_state IN ('sending','accepted','unknown');
COMMIT;
```

## Migration and validation
- Gmail implementation is committed before any catch-up implementation starts.
- Test the migration against temporary databases, then create a consistent backup
  and apply the approved SQL transaction. Verify existing schemas and rows remain intact.
- Normal approval actions persist review state in the new tables. After a confirmed
  manual SMS send, use the existing db.add_interaction helper to insert the accepted
  outbound text; do not log failures as successful outbound texts or change lead scores.
  Gmail acceptance is coordinated with the existing email queue and send audit.
- Run pytest tests/ and mock all provider sends. No live test messages or calls.
- Rollback disables the catch-up page and leaves its records for recovery. No destructive
  rollback, history rewrite, or restoring an old database over newer CRM activity.

## Approval requested
Approve the workflow, file plan and exact additive SQL above. Confirmed mailbox
access is needed before this can be represented as a complete cross-channel catch-up.
