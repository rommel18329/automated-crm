"""Import Steven Hebert Facebook Messenger conversation into crm.db."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db

DB_PATH = PROJECT_ROOT / "crm.db"
LEAD_ID = 38  # Steven B Hebert

TEXTS = [
    ("2026-01-28T21:22:00", "outbound",
     "Hey Steven, Rom here. We spoke a while back about the Fort Dodge properties and you'd mentioned the old school you were considering selling.\n\nSomething came up on my end related to that, and I also wanted to reconnect about the property list you were going to send when things slowed down. I figured I'd reach out here in case it's easier than the phone. No rush at all."),

    ("2026-01-29T09:43:00", "inbound",
     "I have a tendency to lose phone numbers so this works too. I'll contact you later when I have more information for you."),

    ("2026-04-14T17:13:00", "outbound",
     "Hey Steve, instead of trying to put together a full list, might be easier to just start with 1 or 2.\n\nWhich ones would you be most open to selling right now?"),

    ("2026-04-14T23:01:00", "inbound",
     "Hi Rom. Sounds good. It will have to be sometime after 5/10/26, if that works. I will let you know which houses might be a good starting point. Thanks."),

    ("2026-05-06T15:49:00", "outbound",
     "That sounds good Steve! Talk soon."),

    ("2026-05-09T10:50:00", "inbound",
     "👍"),

    ("2026-05-12T21:37:00", "inbound",
     "Any particular type you would prefer-number of bedrooms? Do you need to come see it first or how do price it. Could you give me an idea about range so we can see if we are on a compatible pricing area?"),

    ("2026-05-15T16:00:00", "inbound",
     "Hey Rom, I'm selling any one in my collection. Here's one you might be interested in 101 N 18th St.. Needs work."),

    ("2026-06-16T11:56:00", "outbound",
     "Hey Steve, sorry for the delay I don't check facebook very often, i'll take a look today, are you still selling that one?"),
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
print("Done. Restart dashboard to see Steven Hebert's conversation.")
