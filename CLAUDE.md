# Automated CRM — Claude Safety Rules

## Project Overview
Local-first real estate CRM. Python + SQLite + Streamlit. Evaluates leads daily,
escalates status (cold → warm → hot), queues follow-up approval, and builds call lists.
Hot leads NEVER receive auto-messages — manual intervention only.

## Core Architecture
- `crm_engine/engine.py` — intent detection, escalation, priority scoring (DO NOT TOUCH unless asked)
- `crm_engine/db.py` — all SQLite access; schema lives here
- `crm_engine/daily_run.py` — orchestrates evaluate → update → queue → call list
- `crm_engine/followup_engine.py` — strategy rotation for follow-up messages
- `crm_engine/ai_message_generator.py` — OpenAI gpt-4o-mini integration
- `dashboard.py` — 1200+ line Streamlit UI (monolithic; handle with care)
- `crm.db` — SQLite database (gitignored; do not delete)

## Safety Rules

### 1. Plan Before Code
Always show a plan and get approval before writing any code.
For any change touching more than one file, write out exactly what will change and why.

### 2. Edit, Don't Rewrite
Never delete or rewrite a whole file. Edit only the lines that need to change.
If a refactor is needed, propose it separately and get explicit approval.

### 3. Database Changes Need Approval
Never modify `crm.db`, any table schema, or any CHECK/FOREIGN KEY constraint
without showing the exact SQL change and getting explicit user approval first.
Schema changes require a migration plan.

### 4. Lead Scoring Logic Is Frozen
Do NOT modify the cold/warm/hot escalation logic, intent signal detection,
priority score formula, or follow-up timing unless explicitly asked.
File: `crm_engine/engine.py` — treat as read-only unless instructed otherwise.

### 5. Approval Mode Must Stay Intact
Never add any auto-send functionality. The `messaging_gateway.send_sms()` function
must remain a placeholder. Hot leads must never receive automated messages.
The approval queue exists for human review — do not bypass it.

### 6. Run Tests After Changes
After any code change, run `pytest tests/` and confirm it passes.
If a test breaks, fix the test failure before reporting success.

### 7. Ask, Don't Guess
If requirements are unclear, ask. Don't make assumptions about lead data,
business rules, or what the user "probably" wants. Confirm edge cases first.

### 8. Commit Before Starting
Before starting a new feature or fix, ensure the current state is committed.
Never leave half-finished changes uncommitted when starting something else.

## Lead Status States
- `cold` → `warm` → `hot` (escalation via intent signals)
- `dead` — terminal, no follow-up
- `contract` — terminal, won

## Intent Signals (engine.py — READ-ONLY)
Escalation to hot is triggered by ANY of:
1. timeline ≤ 30 days AND motivation ≥ 6
2. Price keywords in inbound message (price, offer, cash, how much)
3. Urgency language (asap, urgent, quick, deadline, this week, today)
4. ≥ 2 inbound messages
5. Same-day reply to outbound message

## Testing
```bash
pytest tests/          # run all tests
pytest tests/ -v       # verbose output
```

## Dev Tracking
Active feature work tracked in `dev/active/`. One file per feature.
