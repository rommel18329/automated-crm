"""Import Jenny Jensen conversation texts from screenshots into crm.db."""
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db

DB_PATH = PROJECT_ROOT / "crm.db"
LEAD_ID = 35  # Beau/Jenny Jensen

# All texts in chronological order from screenshots (year 2026)
# direction: "inbound" = from Jenny, "outbound" = from us
TEXTS = [
    # Feb 9
    ("2026-02-09T15:54:00", "outbound",
     "Hey Jenny, hope you're doing well and that things have been moving along on your end. Thanks for the update. When you're ready, just let me know and we can send out our agreement and take a look at next steps."),

    # Before Feb 9 - Jenny said nearly cleared out
    ("2026-02-07T12:00:00", "inbound",
     "I am nearly cleared out but not quite. I have one load left that I hope to have handled by the end of this weekend. My bank called me on Friday and said we should be able to close finally by the end of this week or possibly beginning of next. Hoping to hear from them today but I'll lyk as soon as I do"),

    # Feb 11
    ("2026-02-11T10:48:00", "inbound",
     "Closing finally on Friday. House is nearly empty, just a few random things left to grab. There are 2 cars on the property that my ex will come get as soon as possible but I think we're safe to move forward with paperwork and such"),

    # Feb 16
    ("2026-02-16T14:08:00", "outbound",
     "Sounds like good news, let me get everything together and sent over to my attorney for review. Once he's done with it i'll send it over for your review."),

    # Mar 17
    ("2026-03-17T10:29:00", "inbound",
     "I haven't received anything yet, are you still interested?"),

    # Mar 20 - two messages close together
    ("2026-03-20T13:50:00", "outbound",
     "Hey Jenny sorry we've been busy with another closing. We are still interested. I'll make sure it gets sent out asap. Sorry for the delay."),

    ("2026-03-20T15:48:00", "inbound",
     "No problem, I'll watch for it"),

    # Mar 25
    ("2026-03-25T16:34:00", "outbound",
     "Hey Jenny, thanks for your patience, we sent out the agreement, let me know if you have any questions."),

    # Mar 26
    ("2026-03-26T16:07:00", "outbound",
     "Thanks, if there are any questions on it or anything you want adjusted let me know!"),

    # After Mar 26 - Jenny replies about agreement
    ("2026-03-27T10:00:00", "inbound",
     "No worries, you guys were patient with me! I'll read it over and lyk"),

    ("2026-03-27T14:00:00", "inbound",
     "There's no mention of the shed, unless I missed it"),

    # Outbound - review shed clause
    ("2026-03-27T15:00:00", "outbound",
     "Let me review, I remember drafting it up but i'm not sure if the attorney included it in the final version.\n\nCan you send a brief note regarding your plans with it?"),

    # Jenny explains shed situation
    ("2026-03-28T10:00:00", "inbound",
     "There's a shed on the east side of the property that isn't included in the sale. You had offered to have it moved to my new place, I have the contract if you'd like me to send it. It's ok if that's changed, I'll just need access to the property at some point to have it removed"),

    # Apr 1
    ("2026-04-01T14:12:00", "outbound",
     "Hey Jenny, working on getting this settled, what's the size of the shed roughly? And where in town is your new place?"),

    # Jenny replies about size
    ("2026-04-01T18:00:00", "inbound",
     "As wide as a single car garage and atleast twice as long, I can measure it for you tomorrow. My new place is 3 blocks north of the old place"),

    # Apr 2 - size
    ("2026-04-02T07:51:00", "inbound",
     "16x40"),

    # Apr 2 - we sent updated copy
    ("2026-04-02T12:40:00", "outbound",
     "Hey Jenny we just sent over an updated copy of that, thanks for getting those details over."),

    # Apr 9
    ("2026-04-09T12:35:00", "outbound",
     "Hey Jenny, any questions on the agreement? Let me know if you need anything!"),

    # Apr 10 - Jenny asks about shed moving clause
    ("2026-04-10T10:44:00", "inbound",
     "Just wondering if you're still planning on moving the shed? The original agreement wording is buyer will move at buyers expense but the new agreement states seller will move. If I'm responsible for moving it I won't be able to make that happen by April 15th (current agreement date)"),

    # Apr 10 - we respond (two messages)
    ("2026-04-10T11:00:00", "outbound",
     "Hey Jenny, the agreement should be written to say we will move it at our expense, you'd just be responsible for finding the contractor who would do the job, we will cover the entire cost at closing."),

    ("2026-04-10T11:01:00", "outbound",
     "I'm out of the office but will review the clause as soon as i'm in."),

    # Apr 10 - Jenny ok
    ("2026-04-10T11:30:00", "inbound",
     "Ok, I'll make a call to the people we purchased it from and see if they'll move it. Thank you!"),

    # Apr 16
    ("2026-04-16T20:35:00", "inbound",
     "I got a quote for $600 to move the shed. I'm currently waiting on the rock pad getting poured for it, the link to the agreement is expired but all looks good except the date on getting the shed out. The rock delivery is weather pending, they can't come if the ground is too soft and the shed delivery guy said two weeks"),
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
print("Done. Restart dashboard to see Jenny's conversation.")
