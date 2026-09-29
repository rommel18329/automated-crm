"""Check Zach Anderson's current texts + find his GV files."""
from crm_engine import db
from pathlib import Path
import re

DB = Path("crm.db")
TAKEOUT = Path(r"C:\Users\jimen\Desktop\Takeout\Voice\Calls")

# Find Zach in DB
leads = db.fetch_leads(DB)
zach = next((l for l in leads if "anderson" in l["name"].lower() and "zach" in l["name"].lower()), None)
print(f"DB lead: {zach['id']} | {zach['name']} | {zach['phone']}")

# Show existing texts
ixns = db.fetch_interactions(zach["id"], DB)
texts = [i for i in ixns if i["type"] == "text"]
print(f"Texts in DB: {len(texts)}")
for t in sorted(texts, key=lambda x: x["timestamp"]):
    print(f"  [{t['direction']}] {t['timestamp'][:10]}  {t['content'][:80]}")

# Look for any GV files matching his phone numbers (all variants)
print("\nSearching GV takeout for Zach's numbers...")
all_phones = ["2172023878", "3094482495", "2173754474", "2178417804", "9377501826"]
for f in sorted(TAKEOUT.glob("*.html")):
    fname = f.name
    for p in all_phones:
        if p in fname or p[1:] in fname:
            print(f"  MATCH: {fname}")

# Also search by name
print("\nGV files with 'zach' or 'anderson' in name:")
for f in sorted(TAKEOUT.glob("*.html")):
    if "zach" in f.name.lower() or "anderson" in f.name.lower():
        print(f"  {f.name}")
