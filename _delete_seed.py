from crm_engine import db
from pathlib import Path

DB = Path("crm.db")

SEED_PHRASES = [
    "can you share your target close date?",
    "I need to move quickly. What can you offer?",
    "Reviewed condition and fast-close options.",
    "still thinking about selling?",
    "Possibly, still reviewing options.",
    "checking in if timing has changed on your property.",
]

with db.get_conn(DB) as conn:
    rows = conn.execute("SELECT id, content FROM interactions").fetchall()
    ids_to_delete = [
        r["id"] for r in rows
        if any(phrase in r["content"] for phrase in SEED_PHRASES)
    ]
    conn.executemany(
        "DELETE FROM interactions WHERE id=?",
        [(i,) for i in ids_to_delete]
    )

print(f"Deleted {len(ids_to_delete)} seed interactions.")
