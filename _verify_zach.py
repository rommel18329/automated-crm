from crm_engine import db
from pathlib import Path

DB = Path("crm.db")
ixns = db.fetch_interactions(44, DB)
texts = [i for i in ixns if i["type"] == "text"]
print(f"Zachary Anderson (303 1st St) — texts: {len(texts)}")
for t in sorted(texts, key=lambda x: x["timestamp"]):
    print(f"  [{t['direction']}] {t['timestamp'][:10]}  {t['content'][:90]}")
