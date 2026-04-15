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
crm --db crm.db import-csv leads.csv --clear-existing
crm --db crm.db daily-run
streamlit run dashboard.py
```

## CLI commands
```bash
crm --db crm.db init
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
