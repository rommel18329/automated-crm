from crm_engine import db
from pathlib import Path

DB = Path("crm.db")
for lid, name in [(33, "Joeseph Schneider"), (42, "Jonathan Kutas")]:
    ixns = db.fetch_interactions(lid, DB)
    print(f"--- {name} ---")
    for i in ixns[:6]:
        print(f"  [{i['type']}] {i['timestamp'][:10]}  {i['content'][:90]}")
    print()
