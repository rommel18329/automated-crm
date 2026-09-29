# Feature: Gmail email review
**Started:** 2026-09-29
**Status:** awaiting plan and migration approval

## Confirmed request
Monitor contactus@silverlineinvestment.group, hosted by Gmail. Show a daily CRM list of conversations needing a reply, their context, and editable suggested replies. Approval sends the displayed reply; denial does not send.

## Proposed behavior for approval
- Connect the mailbox through Google OAuth with gmail.readonly and gmail.send. Store credentials locally outside Git. Verify the authenticated mailbox or authorized sender before enabling sending.
- Initially examine the last 30 days of mail addressed to this mailbox, including already-read messages. Fetch complete thread context and check sent mail so replies made in Gmail are recognized.
- Refresh every 15 minutes through a Windows scheduled task while this computer is running, with catch-up at next launch. Offer Sync now and display the last successful sync and errors. The daily list carries unanswered items forward.
- Show sender, subject, received time, why a reply is suggested, summary, actual thread messages, and an editable reply. Keep newsletters and automated notices in a separate no-reply-needed view; uncertain cases remain available for review.
- Use the existing configured AI provider through a separate email prompt to summarize and draft. This sends relevant email text to that provider. Treat email bodies as data, never instructions to operate the CRM. Do not invent deal terms. Show attachment names and a warning when attachment contents have not been analyzed.
- Show recipients and the exact text above Approve & send. Start with reply-to-sender only. Deny dismisses the current incoming message; a new incoming message returns the thread for review. Editing alone never sends.
- Recheck Gmail immediately before sending. If the conversation changed or was already answered, require fresh review. Use an atomic send claim to prevent double clicks or simultaneous sessions from sending twice.
- Record Gmail's returned message ID as provider acceptance, without claiming delivery. An uncertain network outcome enters an unknown state for reconciliation, with no blind automatic retry.
- Keep lead scoring, SMS, and existing interaction constraints unchanged. Email context initially comes from Gmail; do not guess lead matches from names or email addresses buried in notes.

## Exact file plan
| File | Change and purpose |
| --- | --- |
| dashboard.py | Add Email Review sidebar entry, pending count, and one renderer call. |
| crm_engine/email_ui.py (new) | Render connection/sync status, review list, context, editable replies, and explicit approval/denial controls. |
| crm_engine/gmail_client.py (new) | OAuth, mailbox validation, paginated thread reads, MIME parsing, correctly threaded sends, and sent-message reconciliation. |
| crm_engine/email_review.py (new) | Triage, email-specific AI summaries/drafts, dismissal/reopening rules, stale-review checks, and approval orchestration. |
| crm_engine/email_sync.py (new) | Standalone synchronization command; never sends replies. |
| crm_engine/db.py | Add explicit additive email migration and all email SQLite access, including atomic send claims; retain existing schemas and constraints. |
| pyproject.toml | Declare Google OAuth/API client dependencies and dependencies directly required by the new email modules. |
| .gitignore | Exclude local OAuth credentials/tokens, email runtime files, and database backups; preserve existing exclusions. |
| scripts/install_email_sync.ps1 (new) | Install a named, reversible Windows scheduled task for this checkout and its Python environment. |
| tests/test_email_review.py (new) | Test approval, dismissal/reopening, answered threads, stale drafts, duplicate submissions, uncertain sends, and AI failures with mocks. |
| tests/test_gmail_client.py (new) | Test MIME parsing, pagination, identity validation, reply headers and API failures with mocks. |
| tests/test_email_db.py (new) | Test migration idempotence, persistence and concurrency claims against temporary databases. |
| README.md | Document Google Cloud/OAuth setup, mailbox connection, permissions, sync behavior, and recovery. |
| dev/active/2026-09-29-email-review.md | Track this approved plan, implementation, validation and remaining connection steps. |

## Exact proposed SQL
Apply only after approval, using an explicit migration command rather than starting the dashboard to migrate. No existing tables, CHECK constraints, or foreign keys are altered.

```sql
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS email_sync_state (
    mailbox TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_error TEXT,
    sync_cursor TEXT
);
CREATE TABLE IF NOT EXISTS email_reviews (
    mailbox TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    latest_message_id TEXT NOT NULL,
    latest_received_at TEXT NOT NULL,
    thread_json TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    reply_reason TEXT NOT NULL DEFAULT '',
    draft_text TEXT NOT NULL DEFAULT '',
    draft_basis_id TEXT,
    review_state TEXT NOT NULL DEFAULT 'needs_review'
        CHECK (review_state IN ('needs_review','no_reply_needed','dismissed','answered')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (mailbox, thread_id)
);
CREATE TABLE IF NOT EXISTS email_send_attempts (
    attempt_id TEXT PRIMARY KEY,
    mailbox TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    basis_message_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    approved_text TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    rfc_message_id TEXT NOT NULL UNIQUE,
    send_state TEXT NOT NULL
        CHECK (send_state IN ('sending','accepted','failed','unknown')),
    gmail_message_id TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS email_send_active_basis
    ON email_send_attempts(mailbox, thread_id, basis_message_id)
    WHERE send_state IN ('sending','accepted','unknown');
COMMIT;
```

## Migration, checkpoint and validation
1. Review existing tracked changes and untracked files before a baseline commit. Preserve existing work; exclude credentials, database backups, logs and nested worktrees. Do not blindly stage the directory. Resolve unfinished work or obtain an explicit exception if a complete baseline cannot safely be committed.
2. Build and test the additive migration against temporary databases. Take a consistent SQLite backup before applying it to crm.db. Apply the exact SQL in a transaction; roll back on failure. Backups remain local and ignored.
3. On feature rollback, disable the scheduled task and remove the UI entry, retaining email records. Do not restore an old CRM backup over later lead activity or drop tables without separate approval.
4. Run pytest tests/ after code changes. Exercise the review UI using mocked Gmail data. Do not send a live test email without explicit authorization.
5. Connect Gmail through a browser OAuth consent flow. Live monitoring depends on Google Cloud OAuth configuration, consent and successful mailbox authentication; never request a password or token in chat.

## Open decisions
- Approve the proposed 30-day initial lookback, 15-minute sync, reply-to-sender behavior, denial semantics, AI processing, file plan and exact additive migration.
- Existing repository changes need an audited checkpoint before feature coding, per AGENTS.md.
