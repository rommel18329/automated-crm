"""Clean up duplicate and system messages for Joeseph Schneider (id=33)."""
from crm_engine import db
from pathlib import Path

DB = Path("crm.db")

ixns = db.fetch_interactions(33, DB)
texts = sorted([i for i in ixns if i["type"] == "text"], key=lambda x: x["timestamp"])

print("Current texts:")
for t in texts:
    print(f"  id={t['id']} [{t['direction']}] {t['timestamp'][:16]}  {t['content'][:80]}")

# IDs to delete:
# 1. Twilio system message
# 2. Duplicate June 8 texts (keep first, delete rest)
# 3. Duplicate May 20 manual entry (real one is May 14 from Twilio)

ids_to_delete = []

# Delete Twilio system message
for t in texts:
    if "Configure your number" in t["content"]:
        ids_to_delete.append(t["id"])
        print(f"\nMarking for delete (system msg): id={t['id']}")

# Find duplicate June 8 texts - keep first, delete rest
june8 = [t for t in texts if "06-08" in t["timestamp"][:10] or "2026-06-08" in t["timestamp"][:10]]
if len(june8) > 1:
    for t in june8[1:]:  # keep first, delete rest
        ids_to_delete.append(t["id"])
        print(f"Marking for delete (June 8 dup): id={t['id']}")

# Delete the manually-added May 20 entry (same content as May 14 Twilio one)
may20 = [t for t in texts if "2026-05-20" in t["timestamp"] and "electrician" in t["content"].lower()]
may14 = [t for t in texts if "2026-05-14" in t["timestamp"] and "electrician" in t["content"].lower()]
if may20 and may14:
    ids_to_delete.append(may20[0]["id"])
    print(f"Marking for delete (May 20 manual dup): id={may20[0]['id']}")

print(f"\nDeleting {len(ids_to_delete)} interactions: {ids_to_delete}")
with db.get_conn(DB) as conn:
    for id_ in ids_to_delete:
        conn.execute("DELETE FROM interactions WHERE id=?", (id_,))

print("\nClean texts remaining:")
ixns2 = db.fetch_interactions(33, DB)
texts2 = sorted([i for i in ixns2 if i["type"] == "text"], key=lambda x: x["timestamp"])
for t in texts2:
    print(f"  [{t['direction']}] {t['timestamp'][:16]}  {t['content'][:80]}")
