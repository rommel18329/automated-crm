# Feature: Deal and relationship opportunities with cross-channel context
**Started:** 2026-09-29
**Status:** in-progress

## Authorized scope
The user asked to check Twilio and other text data, recheck email context to avoid
duplicate/repetitive contact, then narrowed the review to real human deal and
relationship opportunities from the last year, with a separate last-six-month view.
This continues the email/context work rather than creating the separately proposed
catch-up batch tables. No new SQL schema, constraint or lead scoring changes apply.

## Implementation plan and files
- `crm_engine/catchup_sources.py`: read Twilio and Google Voice export history,
  match only explicit phone/email evidence, combine related Gmail threads, retain
  provenance and source failures, and compute a context fingerprint.
- `crm_engine/email_opportunities.py`: screen automated mail, classify substantive
  opportunities using all supplied conversation evidence (chunking large histories),
  explain the reason to reconnect, draft a specific response, and calculate calendar
  six-/twelve-month periods. Failed analyses remain identifiable and retryable.
- `crm_engine/opportunity_scan.py`: resumable year-wide fetch/analysis with ignored
  local checkpoints, progress reporting and no message sending.
- `crm_engine/gmail_client.py`: batch reads with quota pacing and bounded read retries;
  correctly identify recipients when reopening a thread whose latest message is ours.
- `crm_engine/email_review.py`: persist opportunity/context JSON in existing email
  rows, refresh on changed context, preserve saved edits, and recheck related sources
  before an approved email send. Google Voice relays require reply in their source app.
- `crm_engine/email_ui.py` and `dashboard.py`: Opportunities page, six-month/older
  six-month/full-year filters, priority, evidence, suggested next step, editable draft,
  source limitations and separate waiting/excluded/analysis-pending views.
- `crm_engine/db.py`: read-only existing-history helper and opportunity count query.
  Existing email persistence statements are reused; no new migration.
- `crm_engine/email_sync.py`: explicit `--review-year` command; ordinary sync remains
  incremental and refreshes current opportunity threads rather than rereading the full archive.
- `pyproject.toml`: declare directly used BeautifulSoup and Twilio dependencies.
- Tests and README: source matching, quote handling, date boundaries, large-history
  coverage, source evidence validation, safe approvals and repeatable operation.

## Evidence and limitations
- Gmail connected successfully; initial 30-day sync read 59 conversations and sent none.
- Year window enumerated 1,803 Gmail conversations. The scan retains downloaded
  results through Gmail quota errors and resumes from the remaining identifiers.
- Twilio API returned HTTP 401: the configured account is not active. Gmail also
  contains an account-suspension/low-balance notice; account status is not repaired
  or billed automatically. Twilio message history is not verified.
- Google Voice Takeout is available at the path already used by the legacy importer.
  The new reader extracted 4,273 historical events for 1,719 phone contacts. Its latest
  record is 2026-05-20; 484 files/messages could not be safely resolved. This is not
  live Google Voice access. Existing imported CRM data and Gmail relay notifications
  provide additional context but do not prove complete live outbound text coverage.
- Incomplete identity/source checks block the send control; drafts remain reviewable.
- New source snapshots and AI checkpoints stay under ignored `.email-local/`.
- No messages sent, no contacts auto-merged, no lead scores/statuses changed.

## Validation
- Initial expanded suite: 55 tests passed, including identity ambiguity, merged
  text threads, notification exclusions and stale-context send blocking.
- Final suite and completed scan counts to be recorded after validation.
