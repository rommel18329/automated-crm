"""Import Dennis Bales text thread (lead 48) from iMessage screenshots."""
from datetime import datetime
from crm_engine import db

LEAD_ID = 48
DB_PATH = db.DB_PATH

messages = [
    # Mar 31 5:42 PM — outbound
    ("2026-03-31T17:42:00", "outbound",
     "Hey Dennis, it's Rommel, just tried calling you. We spoke a while back about your properties in Herrick and Westervelt and I wanted to reconnect. Are you still looking to sell one of them or did plans change?"),

    # Mar 31 — inbound reply (shortly after)
    ("2026-03-31T17:55:00", "inbound",
     "Both are for Sale. I is rented And the other is listed with a Realtor. Cash is king. One in Herrick appraised 15 months ago at 110000. The other one is listed at 129500! Thru BRINKOETTER realtors in Decatur, il!"),

    # Mar 31 — second inbound bubble
    ("2026-03-31T17:56:00", "inbound",
     "It's move in ready. 95% all new"),

    # Apr 10 4:51 PM — outbound follow-up
    ("2026-04-10T16:51:00", "outbound",
     "Hey Dennis, just wanted to follow up real quick. Were you leaning more toward moving the Herrick place or the one in Westervelt? Are they both listed?"),

    # Apr 10 — inbound reply
    ("2026-04-10T17:05:00", "inbound",
     "The Herrick property is rented and the one year lease option is set to expire 4-15 and the person has first right to purchase or continue renting money to month! I'd she decides not to exercise her option then I have a Real Estate Agent ready to list the property! The Westervelt property is listed for Sale by a Realtor from Decatur, Illinois. Brinkkoeter Realtors."),

    # Apr 13 4:34 PM — outbound
    ("2026-04-13T16:34:00", "outbound",
     "Got it, that helps a lot. Sounds like the Herrick one really depends on what she decides here in the next few days. If she doesn't end up buying it, would you be open to selling it as-is before putting it on the market?"),

    # Apr 13 — inbound reply
    ("2026-04-13T16:48:00", "inbound",
     "Money Talks but need cash not terms."),
]

for ts_str, direction, content in messages:
    ts = datetime.fromisoformat(ts_str)
    db.add_interaction(LEAD_ID, "text", content, direction, DB_PATH, ts=ts)
    print(f"  [{direction}] {ts_str[:16]}  {content[:60]}")

print(f"\nDone — {len(messages)} messages added to Dennis Bales (lead {LEAD_ID})")
