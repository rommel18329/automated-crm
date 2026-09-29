"""Add earlier Jimmie Stanley conversation history (Nov 2025 - Jan 2026)
and fix the misdated 'Sorry I haven't gotten back' message."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db

DB_PATH = PROJECT_ROOT / "crm.db"
LEAD_ID = 34

NEW_TEXTS = [
    ("2025-11-26T17:13:00", "outbound",
     "Hey Jim, appreciate your patience, I've got that referral for you. You can reach out to Christy at 773-217-8686. We've done a deal before and she'd be happy to chat about her experience. She did say she might be a bit tied up the next couple days with the holiday. I should be free in about an hour as well if you want to hop on a call."),

    ("2025-12-03T15:15:00", "outbound",
     "Hey Jim hope you had a good Thanksgiving.\n\nI saw a call come through to our office number, looks like I accidentally sent you that one instead of Christy's, sorry about that. Her direct line is 515-599-0137"),

    ("2025-12-10T18:52:00", "outbound",
     "Thanks for those, that's helpful. Do you have a couple minutes to chat today or would tomorrow work best, I think we can get pretty close to where you wanted to be."),

    ("2025-12-10T19:00:00", "inbound",
     "Tomorrow"),

    ("2025-12-11T14:01:00", "outbound",
     "Hey Jim can I call you in about an hour?"),

    ("2025-12-11T14:05:00", "inbound",
     "Sorry, no I had about a 15 minute window here but I could talk today"),

    ("2025-12-12T10:24:00", "outbound",
     "Hey Jim are you free this afternoon?"),

    ("2025-12-12T10:30:00", "inbound",
     "What time?"),

    ("2025-12-12T13:22:00", "outbound",
     "3:30?"),

    ("2025-12-12T17:09:00", "outbound",
     "I'll be available for the rest of the afternoon too if you find a window."),

    ("2025-12-18T16:24:00", "outbound",
     "Hey Jim, checking back in to see where you landed after our last call. If it makes sense, I'm still open to working toward a structure that fits both sides."),

    ("2025-12-29T10:26:00", "inbound",
     "Sorry, things have been kind of hectic at work and everything. I'm thinking im stuck on my 60,000. If you get close to that we can make something happen ."),

    ("2026-01-12T16:06:00", "outbound",
     "Hey Jim, no worries that's how my holiday season went as well."),

    ("2026-01-12T16:08:00", "outbound",
     "I've been running this a few different ways and I've talked through rehab costs with contractors. With that scope, the deal really starts to work around 45 with terms.\n\nWhen you say close, how close do you think we could get to that?"),

    ("2026-01-12T16:20:00", "inbound",
     "What if we split the 15000 difference and meet at 52500 with terms?"),

    ("2026-01-15T15:34:00", "outbound",
     "Sorry I haven't gotten back Jim I've been giving this some thought, we're close, let me review tonight and i'll give you a clear answer here today."),
]

with db.get_conn(DB_PATH) as conn:
    # Remove the misdated duplicate of the "Sorry I haven't gotten back" message
    removed = conn.execute(
        "DELETE FROM interactions WHERE lead_id=? AND type='text' AND timestamp=? AND content LIKE 'Sorry I haven%'",
        (LEAD_ID, "2026-01-28T10:00:00"),
    ).rowcount

    inserted = 0
    for ts_str, direction, content in NEW_TEXTS:
        conn.execute(
            "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
            (LEAD_ID, "text", content, ts_str, direction),
        )
        inserted += 1

print(f"Removed misdated duplicate: {removed}")
print(f"Inserted {inserted} earlier history messages (Nov 2025 - Jan 2026).")
print("Done. Restart dashboard to see full conversation history.")
