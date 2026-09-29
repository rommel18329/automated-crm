"""Import Jimmie Stanley conversation texts from screenshots into crm.db."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db

DB_PATH = PROJECT_ROOT / "crm.db"
LEAD_ID = 34  # Jimmie Stanley

TEXTS = [
    ("2026-01-27T16:00:00", "outbound",
     "Sorry I haven't gotten back Jim I've been giving this some thought, we're close, let me review tonight and i'll give you a clear answer here today."),

    ("2026-01-28T14:18:00", "outbound",
     "Hey Jim, sorry for the delay getting back to you, I wanted to be sure before responding.\n\nI understand why splitting the difference feels fair, and I appreciate you meeting me in the middle. I've gone through the numbers again with the rehab scope and risk in mind, and at 52.5 it just doesn't work for me.\n\nIf we can land at 45, I'm comfortable moving forward and getting this wrapped up.\nIf that's not workable on your side, I completely understand, just let me know so I can plan accordingly."),

    ("2026-02-04T14:20:00", "outbound",
     "Hey just wanted to check in and see where you landed on the numbers. I'm solid at 45 with terms and can move forward if that works. If not I understand just let me know."),

    ("2026-02-04T15:00:00", "inbound",
     "What would the terms need to be?"),

    ("2026-02-05T17:19:00", "outbound",
     "Same terms we discussed on the phone before, just adjusted to 45k.\n\n3.5% interest, about $200/month, 3 year balloon, $1k down, and we could still do the first 6 months with no payments.\n\nThat structure is what makes 45 workable on my end."),

    ("2026-02-07T09:37:00", "inbound",
     "If you would please give me a call at your earliest convenience. I think we're close and we can come to terms."),

    ("2026-02-08T08:11:00", "outbound",
     "That sounds good, does tomorrow morning work okay?"),

    ("2026-02-08T08:20:00", "inbound",
     "Yes"),

    ("2026-02-08T08:25:00", "inbound",
     "About what time?"),

    ("2026-02-08T08:30:00", "outbound",
     "Does around 10:30 work?"),

    ("2026-02-08T08:35:00", "inbound",
     "Yes"),

    ("2026-02-09T10:42:00", "inbound",
     "That works for us."),

    ("2026-02-09T14:59:00", "outbound",
     "Happy to hear that, i'll get the agreement going and circulate it as soon as I have it back from my attorney."),

    ("2026-03-03T12:45:00", "inbound",
     "Everything's still in the works just haven't heard anything for a while"),

    ("2026-03-04T12:37:00", "outbound",
     "Hey Jim, thanks for checking in. Things slowed down a bit on our end while we wrapped up another purchase. I'm meeting with my attorney this week to finalize the agreement and should be able to send it over shortly after that. Appreciate your patience."),

    ("2026-03-20T16:35:00", "outbound",
     "Hey Jim sorry for the delay I have been swamped with closings, still very interested. I will have documents for you to review by Monday."),

    ("2026-03-20T17:00:00", "inbound",
     "Ok"),

    ("2026-03-23T14:49:00", "outbound",
     "Hey Jim, my attorney and I are wrapping up the agreement to send over, are these the final terms we ended up on?\n\n\"Same terms we discussed on the phone before, just adjusted to 45k.\n\n3.5% interest, about $200/month, 3 year balloon, $1k down, and we could still do the first 6 months with no payments.\"\n\nI know we had a call after that but I can't seem to find notes from that call."),

    ("2026-03-25T13:40:00", "outbound",
     "Hey Jim, I'm getting everything put together based on what we discussed at 45 with those terms. I'll send it over shortly, if you want anything adjusted we can tweak it."),

    ("2026-03-25T14:00:00", "inbound",
     "Ok"),

    ("2026-03-26T17:32:00", "outbound",
     "Good afternoon, what's a good email for you?"),

    ("2026-03-26T17:40:00", "inbound",
     "Jbstanley1975@gmail.com"),
]

db.init_db(DB_PATH)

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
print("Done. Restart dashboard to see Jimmie's conversation.")
