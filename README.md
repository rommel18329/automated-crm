# Automated CRM Decision + Escalation Engine

Local-first real-estate lead operating system built with Python, SQLite, CLI, and Streamlit.

## What it does
- Evaluates cold/warm/hot leads daily.
- Never auto-messages hot leads (manual call list only).
- Generates follow-up approvals for warm/cold leads.
- Escalates high-intent leads to hot.
- Produces call list + call briefs.
- Tracks touch performance and message effectiveness.

## Quickstart
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
crm --db crm.db init
crm --db crm.db reset-seed
crm --db crm.db import-csv leads.csv --clear-existing
crm --db crm.db daily-run
streamlit run dashboard.py
```

## CLI commands
```bash
crm --db crm.db init
crm --db crm.db reset-seed
crm --db crm.db import-csv leads.csv --clear-existing
crm --db crm.db daily-run
crm --db crm.db queue
crm --db crm.db call-list
crm --db crm.db add-interaction 1 --type text --direction outbound --content "Checking in" --message-type follow-up
```

## Daily workflow implementation (`crm_engine/daily_run.py`)
1. evaluate leads
2. update statuses
3. generate follow-ups (approval mode)
4. build call list
5. generate call briefs
6. output summary

## Approval mode
The system only proposes actions. It does **not** auto-send any text/call.

## Gmail Email Review

Email Review monitors `contactus@silverlineinvestment.group`. It displays context,
suggested replies, editable text and explicit **Approve & send**, **Save edit** and
**Deny** actions. No scheduled process sends email. Gmail acceptance is shown with
the provider message ID; it does not mean confirmed delivery.

### One-time setup

1. Install dependencies with `python -m pip install -e .`.
2. Apply the approved additive migration: `python -m crm_engine.email_sync --migrate`.
   This first creates a consistent local `crm.db.backup-email-*` backup. It adds
   only email tables and an index; existing lead tables and constraints stay intact.
3. In [Google Cloud](https://console.cloud.google.com/), enable the Gmail API,
   configure the OAuth consent screen, and create a **Desktop app** OAuth client.
   Use an Internal audience when available for your Workspace organization.
   Otherwise, add this mailbox as a test user. External apps in Testing may require
   renewed consent as their refresh tokens expire; configure production access
   appropriately for ongoing use.
4. Save the downloaded client JSON as `.email-local/gmail-client.json` inside this
   project. Never commit it or paste it in chat. This directory and token files
   are ignored by Git.
5. In **Email Review**, upload the client JSON (or save it at the path above)
   and click **Connect Gmail**. Alternatively run
   `python -m crm_engine.email_sync --connect`. Sign into Google as
   `contactus@silverlineinvestment.group` and grant Gmail read and send access.
   The current connection requires this to be the actual mailbox identity.
   Aliases, Google Groups and delegated/shared accounts require separate setup.
6. Install background sync in PowerShell:
   `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_email_sync.ps1`.
   The task runs every 15 minutes while your Windows account is signed in.
   It starts when available after a missed run; the computer must be awake.
7. Open **Email Review** in the CRM. You can also select **Sync now**.

The first sync examines the last 30 days of incoming email, including read mail,
and retrieves full thread text. Later syncs cover time since the last successful
sync and recheck tracked conversations so replies sent directly from Gmail are
recognized. Unanswered items carry forward. Spam and trash are not initially
imported. Opening Email Review catches up if the last sync is over 15 minutes old.

### Review behavior

- Suggested summaries and replies use the existing Anthropic account configured
  in `.env`. Relevant email text goes to that provider. `EMAIL_AI_MODEL` can
  override the default `claude-haiku-4-5` model. AI context is limited to the latest
  20 messages and 10,000 characters per message; full fetched thread bodies are
  available in the UI. Attachments are listed but not read or sent to the AI.
- AI classifications are suggestions. **No reply needed** remains visible and
  can be returned to review. If AI generation fails, the email stays in Needs review
  with a blank editable draft. **Suggest again** replaces the current draft.
- **Deny** dismisses that incoming message without sending. A new message reopens
  the conversation. **Save edit** does not send. Approval replies to the displayed
  sender/reply-to address only; no automatic CC, BCC or reply-all.
- Before sending, the app rechecks the thread and blocks outdated approvals.
  Concurrent sync/approval operations are serialized, and duplicate submissions
  are blocked by a database constraint. A new email can still arrive at Gmail in
  the small interval between the final check and send; Gmail has no atomic
  compare-and-send operation.
- **Send history** records approved text, CRM user label, recipient, time and
  Gmail's message ID. CRM user labels are attribution, not authenticated identities.
  Run the CRM only for trusted local users with access to this mailbox.
- If sending times out or a process crashes, the outcome stays **unknown** until
  Gmail Sent mail confirms the recorded Message-ID. Sync reconciles these cases.
  The app does not automatically retry an uncertain send. Check Gmail manually
  if it stays unknown. A definite API rejection is marked failed and can be
  retried by an explicit new approval after fixing the underlying problem.

Use `Get-ScheduledTaskInfo -TaskName 'Silverline CRM Gmail Sync'` to inspect task
execution. Remove background sync with the installer script's `-Remove` switch.
To disable the feature, remove the task and disconnect its Google OAuth grant;
retain email records. Do not restore an older database backup over later CRM work.

### Verification

Run `python -m pytest tests/`. Email tests use mocked Gmail/AI calls and temporary
databases; they do not send live messages. Live mailbox access requires OAuth setup.
