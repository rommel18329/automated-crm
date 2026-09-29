from crm_engine import db
from pathlib import Path

DB = Path("crm.db")
ixns = db.fetch_interactions(33, DB)
texts = [i for i in ixns if i["type"] == "text"]
print(f"Joeseph Schneider — {len(texts)} texts:\n")
for t in sorted(texts, key=lambda x: x["timestamp"]):
    print(f"  [{t['direction']}] {t['timestamp'][:16]}  {t['content'][:100]}")
