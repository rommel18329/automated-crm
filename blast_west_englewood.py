"""
Import + text blast for West Englewood buyer list.
Skips bad/disconnected numbers. Uses phone 2/3 when phone 1 is noted bad.
Spanish message for Spanish-speaking contacts.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from crm_engine import db
from crm_engine.messaging_gateway import send_sms

DB_PATH = db.DB_PATH

# fmt: off
CONTACTS = [
    # (name, phone, notes, warm, spanish)
    ("Abal Zaidi",        "619-723-7463", "Left VM + text",           False, False),
    ("Refugio Martinez",  "773-654-7330", "Called back, asked for info", True, False),
    # Altha Williams — both phones d/c, skip
    ("Emmanuel Pore",     "773-432-5597", "Spoke with, sent info",    True,  False),
    ("Juan Venegas",      "773-430-4800", "Sent info to",             True,  False),
    ("Bisa Myles",        "312-342-1066", "Left VM x2",               False, False),
    ("Hayes Jackson",     "773-680-9250", "Left VM (phone 2)",        False, False),
    # Sekia Allen — already in CRM as lead 507, phone 815-823-9707
    ("Sekia Allen",       "815-823-9707", "Left VM",                  False, False),
    ("Nichelle Ramsey",   "773-370-9874", "Left VM",                  False, False),
    ("Rafael Ortega",     "773-490-4113", "Left VM + text",           False, False),
    # Mayra Cortez — bad, no alt, skip
    ("Juan Rodriguez",    "773-405-8860", "Left VM, speaks Spanish",  False, True),
    ("Manuel Manzo",      "773-852-3196", "Left VM",                  False, False),
    ("John Brown",        "773-738-4607", "Left VM (phone 2)",        False, False),
    ("Trumaine Charles",  "773-690-3823", "Left VM (phone 2)",        False, False),
    ("Kenneth Boubel",    "720-841-2097", "Left VM",                  False, False),
    ("Gilberto Franco",   "773-615-2592", "Left VM (phone 2)",        False, False),
    ("Jorge Benitez",     "708-824-8597", "Left VM",                  False, False),
    ("Andy Garcia",       "708-845-1346", "Left VM + text",           False, False),
    ("Artilia Cunningham","773-326-5977", "Left VM (phone 2)",        False, False),
    # Carlos Perez/Ricardo Valadez — bad, only one number, skip
    ("Isidro Sanchez",    "773-370-1567", "Left VM",                  False, False),
    ("Marvin Lancaster",  "312-734-9260", "Hung up, speaks Spanish",  False, True),
    ("Alma Lopez",        "773-284-6005", "Left VM (phone 2)",        False, False),
    ("Conrado Espinosa",  "847-946-4073", "Left VM (phone 2)",        False, False),
    ("Robert Yates",      "312-434-0592", "Left VM (phone 2)",        False, False),
    ("Rodrigo Garcia",    "773-470-7628", "Left VM",                  False, False),
    ("Jose Fierro",       "773-501-7702", "Sent info to",             True,  False),
    # Trinidad Alejandre — bad, only one number, skip
    ("Nicole Boyd",       "773-704-8443", "Left VM",                  False, False),
    ("Sherry Sims",       "205-541-0572", "Left VM",                  False, False),
    ("Paris Harris",      "708-996-8121", "Left VM + text",           False, False),
    ("Shirley Bell",      "773-955-7578", "Left VM (phone 2)",        False, False),
    # Jose Vargas — bad, only one number, skip
    ("Luis Barron",       "708-705-3948", "Sent info to",             True,  False),
]
# fmt: on

WARM_MSG = {
    "Refugio Martinez": "Hey Refugio, Rommel here. You reached out a while back when I sent you that info on the Winchester deal. Got a property available now in West Englewood, thought of you first. Worth a quick look?",
    "Emmanuel Pore":    "Hey Emmanuel, Rommel here. We connected a while back on a Chicago deal. Got a property in West Englewood available now — wanted to loop you in before I put it out there. Still active?",
    "Juan Venegas":     "Hey Juan, Rommel here. Circling back — got a deal in West Englewood I think could be a fit for you. You still looking in that area?",
    "Jose Fierro":      "Hey Jose, Rommel here. We were in touch a while back. Got a property in West Englewood available now — figured you'd want to know. Worth a conversation?",
    "Luis Barron":      "Hey Luis, Rommel here. Wanted to reach back out — got a deal in West Englewood that just came together. Is that area still on your radar?",
}

SPANISH_MSG = {
    "Juan Rodriguez":  "Hola Juan, soy Rommel. Se que estabas trabajando en otra propiedad, pero tengo un deal disponible en West Englewood ahora mismo. Si te interesa echarle un vistazo, avisame.",
    "Marvin Lancaster": "Hola Marvin, soy Rommel. Tengo una propiedad disponible en West Englewood y queria saber si esa zona te interesa. Cualquier cosa me avisas.",
}

STANDARD_MSG = "Hey {first}, this is Rommel. Got a property coming available in West Englewood — thought of you. Is that an area you're still looking in?"


def get_first(name: str) -> str:
    return name.split()[0].capitalize()


def build_message(name: str, warm: bool, spanish: bool) -> str:
    if spanish and name in SPANISH_MSG:
        return SPANISH_MSG[name]
    if warm and name in WARM_MSG:
        return WARM_MSG[name]
    return STANDARD_MSG.format(first=get_first(name))


def ensure_investor_lead(name: str, phone: str) -> int:
    """Return existing lead id or create a new investor lead."""
    leads = db.fetch_leads(DB_PATH, where="phone=?", params=[phone])
    if leads:
        return leads[0]["id"]
    lead_id = db.create_lead(name, phone, DB_PATH, status="investor")
    bb = {
        "states": ["IL"],
        "property_types": ["Residential"],
        "deal_types": ["Fix & Flip", "Buy & Hold"],
        "price_min": None, "price_max": None,
        "condition": "Any", "beds_min": None, "baths_min": None,
        "notes": "West Englewood buyer list",
    }
    db.update_lead(lead_id, {"buy_box": json.dumps(bb)}, DB_PATH)
    return lead_id


def run():
    db.init_db(DB_PATH)
    sent, failed, skipped = 0, 0, 0

    for name, phone, notes, warm, spanish in CONTACTS:
        lead_id = ensure_investor_lead(name, phone)
        msg = build_message(name, warm, spanish)
        result = send_sms(lead_id, msg, DB_PATH)

        status = "SENT" if result["sent"] else "FAIL"
        err = f" | {result['error']}" if result.get("error") else ""
        print(f"[{status}] {name} ({phone}){err}")
        print(f"       {msg[:80]}")
        print()

        if result["sent"]:
            sent += 1
        else:
            failed += 1

    print(f"Done — {sent} sent, {failed} failed, skipped ~5 bad-number contacts")


if __name__ == "__main__":
    run()
