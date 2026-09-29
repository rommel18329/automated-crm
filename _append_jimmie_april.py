"""Add April 2026 continuation to Jimmie Stanley's thread and remove the
misplaced 'Yes' that was added after the Mar 26 email exchange."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db

DB_PATH = PROJECT_ROOT / "crm.db"
LEAD_ID = 34

NEW_TEXTS = [
    ("2026-04-01T14:08:00", "outbound",
     "Just sent that over, let me know if you have any questions."),

    ("2026-04-03T10:07:00", "inbound",
     "There's no monthly payment on that agreement"),

    ("2026-04-09T12:08:00", "outbound",
     "Hey Jimmie, I just got the payment schedule addendum back from counsel, should be in your inbox for review."),

    ("2026-04-17T07:49:00", "inbound",
     "I got your email. I'll get it printed off and review it this weekend."),

    ("2026-04-28T09:30:00", "outbound",
     "Hey Jimmie, i'm in a meeting right now can i call you later?"),

    ("2026-04-28T09:45:00", "inbound",
     "Yes"),
]

with db.get_conn(DB_PATH) as conn:
    removed = conn.execute(
        "DELETE FROM interactions WHERE lead_id=? AND type='text' AND timestamp=? AND direction='inbound' AND content='Yes'",
        (LEAD_ID, "2026-03-26T17:40:00"),
    ).rowcount

    for ts_str, direction, content in NEW_TEXTS:
        conn.execute(
            "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
            (LEAD_ID, "text", content, ts_str, direction),
        )

print(f"Removed misplaced 'Yes': {removed}")
print(f"Inserted {len(NEW_TEXTS)} April messages.")
print("Done. Restart dashboard to see the full thread through Apr 28.")
