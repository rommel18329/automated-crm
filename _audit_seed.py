"""Identify all seed data interactions for review before deletion."""
from crm_engine import db
from pathlib import Path

DB = Path("crm.db")

# Exact content strings injected by seed_sample_data() in db.py
SEED_PHRASES = [
    "can you share your target close date?",
    "I need to move quickly. What can you offer?",
    "Reviewed condition and fast-close options.",
    "still thinking about selling?",
    "Possibly, still reviewing options.",
    "checking in if timing has changed on your property.",
]

leads = {l["id"]: l["name"] for l in db.fetch_leads(DB)}

with db.get_conn(DB) as conn:
    rows = conn.execute(
        "SELECT id, lead_id, type, content, timestamp FROM interactions"
    ).fetchall()

seed_rows = []
for row in rows:
    if any(phrase in row["content"] for phrase in SEED_PHRASES):
        seed_rows.append(row)

print(f"Seed interactions to delete: {len(seed_rows)}\n")
for r in seed_rows:
    name = leads.get(r["lead_id"], "?")
    print(f"  id={r['id']}  [{r['type']}]  {r['timestamp'][:10]}  {name}  —  {r['content'][:70]}")
