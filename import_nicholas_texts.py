"""Import Nicholas Hemann text thread (lead 54) from iMessage screenshots."""
from datetime import datetime
from crm_engine import db

LEAD_ID = 54
DB_PATH = db.DB_PATH

messages = [
    # Wed Apr 1, 1:31 PM — outbound
    ("2026-04-01T13:31:00", "outbound",
     "Hey Nicholas, this is Rom, we spoke a while back about your Iowa Ave property. Wanted to circle back, is that still something you're looking to offload or did you end up keeping it?"),

    # Apr 1 — inbound reply
    ("2026-04-01T13:45:00", "inbound",
     "Remind me which address or what we talked about"),

    # Thu Apr 2, 3:58 PM — outbound
    ("2026-04-02T15:58:00", "outbound",
     "It was the place on 708 E Iowa Ave in Iowa City, the 3 bed you mentioned wasn't really a core property for you, tied to that partnership. Last time you were considering offloading it. Curious if that's still something you're looking to do or if you ended up holding it?"),

    # Apr 2 — inbound reply 1
    ("2026-04-02T16:10:00", "inbound",
     "I would be open to parting with it."),

    # Apr 2 — inbound reply 2
    ("2026-04-02T16:11:00", "inbound",
     "Would it strictly be a rental or do you plan on having one of your kids live in it"),

    # Fri Apr 10, 4:05 PM — outbound
    ("2026-04-10T16:05:00", "outbound",
     "Most likely we'd keep it as a rental. I've spent some time out in Iowa City, so it's an area we've been looking to invest in. For us the main focus is just structuring something that works well on both sides. Has anything changed with the property since we last talked or is it pretty much the same condition wise?"),

    # Apr 10 — inbound reply
    ("2026-04-10T16:20:00", "inbound",
     "Pretty much same condition I put a new standing seam metal roof on the front porch and new gutter on the front porch. That's about it."),

    # Mon Apr 13, 5:25 PM — outbound
    ("2026-04-13T17:25:00", "outbound",
     "Got it, that's helpful, appreciate you sharing that. I'm gonna take a look at everything on my end with that in mind and see what makes sense. I should have a better idea shortly and I'll circle back with you."),
]

for ts_str, direction, content in messages:
    ts = datetime.fromisoformat(ts_str)
    db.add_interaction(LEAD_ID, "text", content, direction, DB_PATH, ts=ts)
    print(f"  [{direction}] {ts_str[:16]}  {content[:60]}")

print(f"\nDone — {len(messages)} messages added to Nicholas Hemann (lead {LEAD_ID})")
