"""Send West Englewood blast to all ALT numbers not yet texted."""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from crm_engine import db
from crm_engine.messaging_gateway import send_sms

DB_PATH = db.DB_PATH

# (primary_name, alt_phone, warm, spanish)
# Excludes numbers already sent in first blast, and confirmed bad/d/c numbers
ALT_NUMBERS = [
    ("Abal Zaidi",          "773-749-9577", False, False),
    ("Abal Zaidi",          "619-739-4779", False, False),
    ("Refugio Martinez",    "773-787-3098", True,  False),
    ("Refugio Martinez",    "773-434-4131", True,  False),
    ("Emmanuel Pore",       "773-629-7806", True,  False),
    ("Emmanuel Pore",       "773-636-5678", True,  False),
    ("Juan Venegas",        "773-719-1612", True,  False),
    ("Juan Venegas",        "773-633-4141", True,  False),
    ("Bisa Myles",          "219-513-6135", False, False),
    ("Bisa Myles",          "312-372-1066", False, False),
    ("Hayes Jackson",       "773-778-7440", False, False),
    ("Sekia Allen",         "815-683-5085", False, False),
    ("Sekia Allen",         "773-540-4924", False, False),
    ("Nichelle Ramsey",     "773-952-6621", False, False),
    ("Rafael Ortega",       "773-359-7634", False, False),
    ("Rafael Ortega",       "773-767-5153", False, False),
    ("Juan Rodriguez",      "773-319-6581", False, True),
    ("Juan Rodriguez",      "773-927-0506", False, True),
    ("Manuel Manzo",        "773-852-3951", False, False),
    ("Manuel Manzo",        "773-414-9393", False, False),
    ("John Brown",          "773-405-3364", False, False),
    ("Trumaine Charles",    "331-205-3784", False, False),
    ("Gilberto Franco",     "773-884-0059", False, False),
    ("Andy Garcia",         "773-247-5376", False, False),
    ("Andy Garcia",         "510-940-2138", False, False),
    ("Artilia Cunningham",  "773-874-4148", False, False),
    ("Isidro Sanchez",      "773-474-9294", False, False),
    ("Marvin Lancaster",    "773-759-5206", False, True),
    ("Marvin Lancaster",    "773-905-3843", False, True),
    ("Conrado Espinosa",    "224-456-9434", False, False),
    ("Robert Yates",        "773-434-0592", False, False),
    ("Rodrigo Garcia",      "773-949-0442", False, False),
    ("Jose Fierro",         "773-603-0527", True,  False),
    ("Jose Fierro",         "585-384-5919", True,  False),
    ("Nicole Boyd",         "773-709-6991", False, False),
    ("Nicole Boyd",         "773-556-4035", False, False),
    ("Sherry Sims",         "205-593-4396", False, False),
    ("Sherry Sims",         "205-593-4345", False, False),
    ("Paris Harris",        "773-434-3086", False, False),
    ("Paris Harris",        "773-256-6762", False, False),
    ("Luis Barron",         "708-705-3918", True,  False),
    ("Luis Barron",         "708-244-9116", True,  False),
]

WARM_MSG = {
    "Refugio Martinez": "Hey Refugio, Rommel here. You reached out a while back when I sent you that info on the Winchester deal. Got a property available now in West Englewood, thought of you first. Worth a quick look?",
    "Emmanuel Pore":    "Hey Emmanuel, Rommel here. We connected a while back on a Chicago deal. Got a property in West Englewood available now — wanted to loop you in before I put it out there. Still active?",
    "Juan Venegas":     "Hey Juan, Rommel here. Circling back — got a deal in West Englewood I think could be a fit for you. You still looking in that area?",
    "Jose Fierro":      "Hey Jose, Rommel here. We were in touch a while back. Got a property in West Englewood available now — figured you'd want to know. Worth a conversation?",
    "Luis Barron":      "Hey Luis, Rommel here. Wanted to reach back out — got a deal in West Englewood that just came together. Is that area still on your radar?",
}

SPANISH_MSG = {
    "Juan Rodriguez":   "Hola Juan, soy Rommel. Se que estabas trabajando en otra propiedad, pero tengo un deal disponible en West Englewood ahora mismo. Si te interesa echarle un vistazo, avisame.",
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


def ensure_lead(name: str, phone: str) -> int:
    leads = db.fetch_leads(DB_PATH, where="phone=?", params=[phone])
    if leads:
        return leads[0]["id"]
    lead_id = db.create_lead(name, phone, DB_PATH, status="investor")
    bb = {
        "states": ["IL"], "property_types": ["Residential"],
        "deal_types": ["Fix & Flip", "Buy & Hold"],
        "price_min": None, "price_max": None,
        "condition": "Any", "beds_min": None, "baths_min": None,
        "notes": f"West Englewood buyer list — alt number for {name}",
    }
    db.update_lead(lead_id, {"buy_box": json.dumps(bb)}, DB_PATH)
    return lead_id


def run():
    db.init_db(DB_PATH)
    sent = failed = 0

    for name, phone, warm, spanish in ALT_NUMBERS:
        lead_id = ensure_lead(name, phone)
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

    print(f"Done — {sent} sent, {failed} failed")


if __name__ == "__main__":
    run()
