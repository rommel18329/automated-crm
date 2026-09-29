"""Import Fernando Suaste (Turlington) outbound follow-up texts into crm.db."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db

DB_PATH = PROJECT_ROOT / "crm.db"
LEAD_ID = 45  # Fernado Suaste

TEXTS = [
    ("2025-05-14T14:00:00", "outbound",
     "Hey Fernando, it's Gabby. Hope you're doing well! I was just calling to follow up with you about your house on Turlington Ave. Just wondering if you and your family are still interested in selling. Let me know either way when you get a chance. Thank you!"),

    ("2025-05-16T13:26:00", "outbound",
     "Hey Fernando — just checking in! I know you're probably busy with work, but wanted to see if you're still open to selling the house on Turlington. No pressure if not — just let me know either way when you get a chance. Happy to chat whenever it's convenient."),

    ("2025-07-24T17:50:00", "outbound",
     "Hey Fernando I hope you and your family are doing well. Totally understand if you're still figuring things out with the house on Turlington. If anything's changed or you've had a chance to think it over, feel free to reach out. We're still active in the area and happy to chat whenever the time's right."),

    ("2025-07-28T19:51:00", "outbound",
     "Hey Fernando, is it still a good time to talk? Totally get it's getting late, and I want you to be able to enjoy your night. If tomorrow's easier, I'm happy to give you a ring then. Just let me know what works best for you."),

    ("2026-03-31T15:57:00", "outbound",
     "Hey Fernando, just tried calling you, quick question, are you still considering selling Turlington or leaning toward holding for now?"),

    ("2026-04-13T17:30:00", "outbound",
     "Hey Fernando, quick one.\n\nAre you still thinking about selling the place on Turlington or did you decide to hold onto it?"),
]

with db.get_conn(DB_PATH) as conn:
    existing = {
        (r[0], r[1], r[2][:16])
        for r in conn.execute(
            "SELECT lead_id, direction, timestamp FROM interactions WHERE lead_id=? AND type='text'",
            (LEAD_ID,)
        ).fetchall()
    }

    inserted = skipped = 0
    for ts_str, direction, content in TEXTS:
        key = (LEAD_ID, direction, ts_str[:16])
        if key in existing:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
            (LEAD_ID, "text", content, ts_str, direction),
        )
        existing.add(key)
        inserted += 1

print(f"Inserted: {inserted}  Skipped (already exist): {skipped}")
print("Done. Restart dashboard to see Fernando's text history.")
