"""Import investor leads from 9 Excel buyer list files into crm.db."""
from __future__ import annotations

import json
import re
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from crm_engine import db

DB_PATH = db.DB_PATH
TEMP = Path(r"C:\Users\jimen\AppData\Local\Temp")

# ── helpers ──────────────────────────────────────────────────────────────────

def norm_phone(raw) -> str | None:
    if pd.isna(raw) or not str(raw).strip():
        return None
    # pandas reads phone columns as float (e.g., 6197237463.0) — convert to int first
    if isinstance(raw, float):
        raw = int(raw)
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def clean(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


# State name → abbreviation
_STATE_ABBR = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
    "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS",
    "kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD","massachusetts":"MA",
    "michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT",
    "nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ","new mexico":"NM",
    "new york":"NY","north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK",
    "oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
    "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT",
    "virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY",
}
_ALL_ABBRS = set(_STATE_ABBR.values())

# City → state mappings for common mentions in these buyer lists
_CITY_TO_STATE = {
    "chicago":"IL","dolton":"IL","naperville":"IL","plainfield":"IL","aurora":"IL",
    "bolingbrook":"IL","oswego":"IL","monee":"IL","lockport":"IL","harvey":"IL",
    "markham":"IL","dekalb":"IL","centralia":"IL","macomb":"IL","ottawa":"IL",
    "jacksonville":"IL","woodstock":"IL","davenport":"IA","iowa city":"IA",
    "cedar rapids":"IA","des moines":"IA","ottumwa":"IA","waterloo":"IA",
    "marshalltown":"IA","ely":"IA","eldora":"IA","eagle grove":"IA",
    "west des moines":"IA","dayton":"OH","columbus":"OH","cincinnati":"OH",
    "cleveland":"OH","orlando":"FL","tampa":"FL","miami":"FL",
    "rogers park":"IL","lakeview":"IL","burnside":"IL","chatham":"IL",
    "south chicago":"IL","north side":"IL",
}


def parse_states(text1: str, text2: str = "") -> list[str]:
    combined = f"{text1} {text2}".lower()
    found: set[str] = set()

    # Check 2-letter codes (word-bounded, all caps in original before lower)
    for abbr in _ALL_ABBRS:
        if re.search(r"\b" + abbr.lower() + r"\b", combined):
            found.add(abbr)

    # Full state names
    for name, abbr in _STATE_ABBR.items():
        if name in combined:
            found.add(abbr)

    # City hints
    for city, abbr in _CITY_TO_STATE.items():
        if city in combined:
            found.add(abbr)

    return sorted(found) or []


def parse_property_types(text: str) -> list[str]:
    t = text.lower()
    res, com = False, False
    if any(k in t for k in ["sfh","single family","single-family","residential","ranch","bungalow","house"]):
        res = True
    if any(k in t for k in ["mfh","mf-","multi family","multi-family","multifamily","duplex",
                              "units","multi unit","commercial","day care","hotel","motel",
                              "apartment","building","high rise"]):
        com = True
    if not res and not com:
        res = True  # default to residential
    out = []
    if res: out.append("Residential")
    if com: out.append("Commercial")
    return out


def parse_deal_types(text: str) -> list[str]:
    t = text.lower()
    found: list[str] = []
    if any(k in t for k in ["flip","fix","arv","rehab","renovation","reno"]):
        found.append("Fix & Flip")
    if any(k in t for k in ["wholesale","assignment","assign"]):
        found.append("Wholesale")
    if any(k in t for k in ["rental","hold","brrr","cash flow","cash-flow","long term","rent","sub2","subto","subject to","seller financing","seller fin","wrap"]):
        found.append("Buy & Hold")
    if not found:
        found = ["Fix & Flip", "Buy & Hold"]  # default for cash buyers
    return list(dict.fromkeys(found))  # deduplicate preserving order


def parse_price(text: str) -> tuple[int | None, int | None]:
    t = text.lower()
    prices: list[int] = []

    def to_int(s: str) -> int:
        s = s.replace(",", "").strip()
        m = re.match(r"(\d+(?:\.\d+)?)(k?)", s)
        if not m:
            return 0
        val = float(m.group(1))
        if m.group(2) == "k":
            val *= 1000
        return int(val)

    # Pattern: $30k-$80k or 30k-80k or $30,000-$80,000
    for m in re.finditer(r"\$?([\d,]+k?)\s*[-–to]+\s*\$?([\d,]+k?)", t):
        prices.extend([to_int(m.group(1)), to_int(m.group(2))])

    # Pattern: under/less than $Xk
    for m in re.finditer(r"(?:under|less than|below|max|up to)\s*\$?([\d,]+k?)", t):
        prices.append(to_int(m.group(1)))

    # Pattern: in the $Xk range
    for m in re.finditer(r"(?:in the)?\s*\$?([\d,]+k?)\s*range", t):
        prices.append(to_int(m.group(1)))

    # Pattern: $30k or $50,000 standalone
    for m in re.finditer(r"\$\s*([\d,]+k?)\b", t):
        prices.append(to_int(m.group(1)))

    valid = [p for p in prices if 5000 <= p <= 10_000_000]
    if not valid:
        return None, None
    return min(valid), max(valid)


def parse_beds(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:/\d+)?\s*(?:bd|bed|bedroom|br)\b", text.lower())
    if m:
        return int(m.group(1))
    return None


def parse_phone_from_text(text: str) -> str | None:
    m = re.search(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text)
    if m:
        return norm_phone(m.group())
    return None


# ── per-file importers ────────────────────────────────────────────────────────

def make_propwire_record(row: pd.Series, default_states: list[str], source: str) -> dict | None:
    """Extract lead info from a Propwire-format row (Cedar Rapids, Des Moines, Ottumwa, Winchester)."""
    # Build name: prefer Name 1, fallback to Owner 1 First+Last
    name = clean(row.get("Name 1", ""))
    if not name:
        first = clean(row.get("Owner 1 First Name", ""))
        last = clean(row.get("Owner 1 Last Name", ""))
        name = f"{first} {last}".strip() if first or last else ""
    if not name:
        return None

    # Phone: try Mobile Phone → Phone → Phone 1
    phone = None
    for col in ["Mobile Phone", "Phone", "Phone 1", "Phone 2"]:
        if col in row and pd.notna(row.get(col)):
            phone = norm_phone(row[col])
            if phone:
                break
    if not phone:
        return None

    # State
    state_col = clean(row.get("State", ""))
    states = [state_col.upper()] if state_col.upper() in _ALL_ABBRS else default_states

    # Property type
    land_use = clean(row.get("Land Use", ""))
    prop_type_col = clean(row.get("Property Type", ""))
    prop_text = f"{land_use} {prop_type_col}"
    prop_types = ["Residential"] if "residential" in prop_text.lower() else ["Residential", "Commercial"]

    # Beds/baths
    beds = int(row["Bedrooms"]) if pd.notna(row.get("Bedrooms")) and row["Bedrooms"] > 0 else None
    baths = int(row["Bathrooms"]) if pd.notna(row.get("Bathrooms")) and row["Bathrooms"] > 0 else None

    # Price reference from the property they own (used as price ceiling indicator)
    est_val = row.get("Estimated Value") if pd.notna(row.get("Estimated Value")) else None
    last_sale = row.get("Last Sale Amount") if pd.notna(row.get("Last Sale Amount")) else None
    price_ref = int(est_val or last_sale or 0) or None
    price_max = price_ref if price_ref and 20000 <= price_ref <= 5_000_000 else None

    # Notes: status/comments column + source
    notes_col = ""
    for col in ["status", "Status ", "Comments", "Call Priority"]:
        val = clean(row.get(col, ""))
        if val:
            notes_col = val
            break

    notes = f"Source: {source}"
    if notes_col:
        notes += f" | {notes_col}"

    bb = {
        "states": states,
        "property_types": prop_types,
        "deal_types": ["Fix & Flip", "Buy & Hold"],
        "price_min": None,
        "price_max": price_max,
        "condition": "Any",
        "beds_min": beds,
        "baths_min": baths,
        "notes": notes,
    }
    return {"name": name, "phone": phone, "buy_box": bb}


def import_propwire(path: Path, sheet: str, default_states: list[str], source: str) -> list[dict]:
    df = pd.read_excel(path, sheet_name=sheet)
    results = []
    for _, row in df.iterrows():
        rec = make_propwire_record(row, default_states, source)
        if rec:
            results.append(rec)
    return results


def import_cedar_rapids() -> list[dict]:
    path = TEMP / "Cedar Rapids Master Buyer List.xlsx"
    df = pd.read_excel(path, sheet_name="cedar_rapids_buyers_sorted_prio")
    results = []
    for _, row in df.iterrows():
        # Name from OPTIONAL columns or Owner 1
        first = clean(row.get("OPTIONAL_FIRST_NAME", ""))
        last = clean(row.get("OPTIONAL_LAST_NAME", ""))
        name = f"{first} {last}".strip() if first or last else ""
        if not name:
            first = clean(row.get("Owner 1 First Name", ""))
            last = clean(row.get("Owner 1 Last Name", ""))
            name = f"{first} {last}".strip()
        if not name:
            continue

        phone = None
        for col in ["Mobile Phone", "Phone", "Alt Phone 1"]:
            if pd.notna(row.get(col)):
                phone = norm_phone(row[col])
                if phone:
                    break
        if not phone:
            continue

        beds = int(row["Bedrooms"]) if pd.notna(row.get("Bedrooms")) and row["Bedrooms"] > 0 else None
        baths = int(row["Bathrooms"]) if pd.notna(row.get("Bathrooms")) and row["Bathrooms"] > 0 else None
        est_val = row.get("Estimated Value") if pd.notna(row.get("Estimated Value")) else None
        last_sale = row.get("Last Sale Amount") if pd.notna(row.get("Last Sale Amount")) else None
        price_ref = int(est_val or last_sale or 0) or None
        price_max = price_ref if price_ref and 20000 <= price_ref <= 5_000_000 else None

        priority = clean(row.get("Call Priority", ""))
        notes = "Source: Cedar Rapids Buyer List"
        if priority:
            notes += f" | Priority: {priority}"

        bb = {
            "states": ["IA"],
            "property_types": ["Residential"],
            "deal_types": ["Fix & Flip", "Buy & Hold"],
            "price_min": None,
            "price_max": price_max,
            "condition": "Any",
            "beds_min": beds,
            "baths_min": baths,
            "notes": notes,
        }
        results.append({"name": name, "phone": phone, "buy_box": bb})
    return results


def import_eldora_final() -> list[dict]:
    path = TEMP / "Final Eldora IA buyers skipped.xlsx"
    df = pd.read_excel(path, sheet_name=0)
    results = []
    for _, row in df.iterrows():
        first = clean(row.get("First Name", "")) or clean(row.get("OPTIONAL_FIRST_NAME", ""))
        last = clean(row.get("Last Name", "")) or clean(row.get("OPTIONAL_LAST_NAME", ""))
        name = f"{first} {last}".strip()
        if not name or name.lower() in ("nan nan", ""):
            continue

        phone = None
        for col in ["Phone", "Alt Phone 1", "Alt Phone 2"]:
            if pd.notna(row.get(col)):
                phone = norm_phone(row[col])
                if phone:
                    break
        if not phone:
            continue

        status_note = clean(row.get("Status", ""))
        notes = "Source: Eldora IA buyer list"
        if status_note:
            notes += f" | {status_note}"

        bb = {
            "states": ["IA"],
            "property_types": ["Residential"],
            "deal_types": ["Fix & Flip", "Buy & Hold"],
            "price_min": None,
            "price_max": None,
            "condition": "Any",
            "beds_min": None,
            "baths_min": None,
            "notes": notes,
        }
        results.append({"name": name, "phone": phone, "buy_box": bb})
    return results


def import_preliminary() -> list[dict]:
    path = TEMP / "Preliminary Buyer List.xlsx"
    df = pd.read_excel(path, sheet_name=0)
    results = []
    for _, row in df.iterrows():
        name_raw = clean(row.get("Name", ""))
        if not name_raw:
            continue
        # Clean up multi-line names (take first line)
        name = name_raw.split("\n")[0].strip()

        # Phone: Cell column, or embedded in name
        cell_raw = clean(row.get("Cell", ""))
        phone = norm_phone(cell_raw)
        if not phone:
            # Try to extract from cell text (e.g., "Phone: +1-312-725-9737" or "7089917101 call only")
            phone = parse_phone_from_text(cell_raw)
        if not phone:
            # Try phone embedded in name field (e.g., "Pawel Sternlicht 773-742-8844")
            phone = parse_phone_from_text(name_raw)
        if not phone:
            continue

        # Clean name: remove embedded phone from name
        name = re.sub(r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", "", name).strip()
        name = re.sub(r"\s{2,}", " ", name).strip(",").strip()

        bb_text = clean(row.get("Buy Box Criteria", ""))
        target_areas = clean(row.get("Current Target Areas", ""))
        email = clean(row.get("Email ", ""))

        states = parse_states(bb_text, target_areas)
        prop_types = parse_property_types(bb_text) if bb_text else ["Residential"]
        deal_types = parse_deal_types(bb_text) if bb_text else ["Fix & Flip", "Buy & Hold"]
        price_min, price_max = parse_price(bb_text) if bb_text else (None, None)
        beds = parse_beds(bb_text) if bb_text else None

        notes_parts = ["Source: Preliminary Buyer List"]
        if email:
            notes_parts.append(f"Email: {email}")
        if bb_text and bb_text.lower() not in ("open to market deals", ""):
            notes_parts.append(bb_text[:300])

        bb = {
            "states": states,
            "property_types": prop_types,
            "deal_types": deal_types,
            "price_min": price_min,
            "price_max": price_max,
            "condition": "Any",
            "beds_min": beds,
            "baths_min": None,
            "notes": " | ".join(notes_parts),
        }
        results.append({"name": name, "phone": phone, "buy_box": bb})
    return results


# ── main ──────────────────────────────────────────────────────────────────────

def run_import():
    db.init_db(DB_PATH)

    # Existing phones in CRM
    with db.get_conn(DB_PATH) as conn:
        existing_phones = {r["phone"] for r in conn.execute("SELECT phone FROM leads").fetchall()}

    print(f"Existing leads in CRM: {len(existing_phones)}")

    all_records: list[dict] = []

    # Collect from each file
    print("\nReading files...")

    all_records += import_preliminary()
    print(f"  Preliminary Buyer List: {len([r for r in all_records])} (running total)")

    cr = import_cedar_rapids()
    all_records += cr
    print(f"  Cedar Rapids: +{len(cr)}")

    dm = import_propwire(
        TEMP / "Des Moines Buyers.xlsx",
        "Propwire cash buyers", ["IA"], "Des Moines Buyers"
    )
    all_records += dm
    print(f"  Des Moines: +{len(dm)}")

    ot = import_propwire(
        TEMP / "Ottumwa buyer sheet.xlsx",
        "propwire Cash Buyers", ["IA"], "Ottumwa Buyers"
    )
    all_records += ot
    print(f"  Ottumwa: +{len(ot)}")

    wi = import_propwire(
        TEMP / "potential buyers 6021 Winchester.xlsx",
        "prop cashb", ["IL"], "Winchester/Chicago Buyers"
    )
    all_records += wi
    print(f"  Winchester/Chicago: +{len(wi)}")

    el = import_eldora_final()
    all_records += el
    print(f"  Eldora Final: +{len(el)}")

    # Lithuanian and Eldora pt2 have no phone column — skip
    # Waterloo is empty — skip

    print(f"\nTotal raw records: {len(all_records)}")

    # Deduplicate: within batch AND against existing CRM phones
    seen: set[str] = set()
    inserted = 0
    skipped_dup = 0
    skipped_existing = 0

    for rec in all_records:
        phone = rec["phone"]
        if phone in existing_phones:
            skipped_existing += 1
            continue
        if phone in seen:
            skipped_dup += 1
            continue
        seen.add(phone)

        lead_id = db.create_lead(rec["name"], phone, DB_PATH, status="investor")
        db.update_lead(lead_id, {"buy_box": json.dumps(rec["buy_box"])}, DB_PATH)
        inserted += 1

    print(f"\nResults:")
    print(f"  Inserted:          {inserted}")
    print(f"  Skipped (existing in CRM):  {skipped_existing}")
    print(f"  Skipped (duplicates within batch): {skipped_dup}")
    print(f"  New total leads: {len(existing_phones) + inserted}")


if __name__ == "__main__":
    run_import()
