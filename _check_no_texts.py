from crm_engine import db
from pathlib import Path

DB_PATH = Path("crm.db")
leads = db.fetch_leads(DB_PATH)

leads_with_texts = set()
for lead in leads:
    ixns = db.fetch_interactions(lead["id"], DB_PATH)
    if any(i["type"] == "text" for i in ixns):
        leads_with_texts.add(lead["id"])

no_texts = [l for l in leads if l["id"] not in leads_with_texts and l["status"] != "dead"]
order = ["hot", "warm", "cold", "contract"]
no_texts.sort(key=lambda x: order.index(x["status"]) if x["status"] in order else 99)

print(f"Leads with no text thread: {len(no_texts)}\n")
for l in no_texts:
    print(f"  [{l['status'].upper():<8}]  {l['name']:<30}  {l['phone']}")
