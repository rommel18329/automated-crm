# Email-aware SMS drafts

User approved the plan on September 30, 2026. Starting checkpoint: `67ae101`.

- Add `crm_engine/sms_context.py`: read saved email context, match confirmed contact
  email/phone identifiers, refresh Gmail on each draft, and label missing/partial
  coverage. Names alone and ambiguous contacts are not combined. No database writes.
- Update `crm_engine/ai_message_generator.py`: include labeled email history and
  its coverage alongside calls/texts, account for email in contact-gap calculations,
  avoid repeated topics, and allow an empty draft with coaching to wait.
- Update `dashboard.py`: explain the Gmail check in the spinner and Coach caption.
- Add matching, freshness, identity, prompt, and no-draft regression tests.

All shared SMS drafting callers receive this context, including Follow-ups.
Read limits: up to 40 initial threads and 40 newly discovered address threads;
latest 20 matched emails, up to 2,000 characters each. Limits and refresh failures
are shown to the user. Attachments are not read. No changes to send, scoring,
follow-up schedules, database schema, or source records.

Validation: `python -m pytest tests/ -q` — 74 passed, six existing UTC deprecation
warnings. Python compilation and `git diff --check` passed. Tests use synthetic
contacts and mocked transports; no private conversations sent for live AI testing.
Coverage includes inbound/outbound email, phone-signature discovery, ambiguous
identity rejection, freshness replacement, offline cached fallback, read limits,
Google Voice exclusion, prompt inclusion, and preservation of an empty wait draft.
