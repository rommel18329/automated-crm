"""Add missing Joe text + audit notes bleeding into conversations."""
from crm_engine import db
from datetime import datetime
from pathlib import Path

DB = Path("crm.db")

# 1. Add the missing outbound text to Joeseph Schneider (id=33)
msg = (
    "Hey Joe, thanks for the update and for being willing to meet them out there. "
    "I'll get an electrician scheduled this week and send you a couple morning options "
    "to pick from. Quick question, do you know which utility company services the property "
    "and if there's a specific form or work order you've already started with them? "
    "Just want to make sure the electrician shows up with the right paperwork so we don't "
    "have to do this twice"
)
ts = datetime(2026, 5, 20, 12, 0, 0)  # today, noon — adjust if you know the exact time
db.add_interaction(33, "text", msg, "outbound", DB, ts=ts)
print("Added missing text to Joeseph Schneider")

# 2. Show how many leads have notes in their interaction set (so we know scope)
leads = db.fetch_leads(DB)
affected = []
for lead in leads:
    ixns = db.fetch_interactions(lead["id"], DB)
    has_texts_or_calls = any(i["type"] in ("text", "call") for i in ixns)
    has_notes = any(i["type"] == "note" for i in ixns)
    if has_texts_or_calls and has_notes:
        affected.append(lead["name"])

print(f"\nLeads with both real conversations AND notes: {len(affected)}")
print("(Notes will no longer show in conversation view after dashboard fix)")
