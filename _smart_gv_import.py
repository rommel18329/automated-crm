"""
Smart import for unmatched named-contact GV files.
Strategy 1: direct address/name match from filename to lead timeline
Strategy 2: known manual overrides for specific contacts
"""
from __future__ import annotations
import re, sys
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
from crm_engine import db
from import_google_voice import parse_text_file, parse_call_file, MY_NUMBERS

TAKEOUT = Path(r"C:\Users\jimen\Desktop\Takeout\Voice\Calls")
DB_PATH = PROJECT_ROOT / "crm.db"

def normalize(raw):
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d[0] == "1": d = d[1:]
    return d if len(d) == 10 else None

def extract_phone(soup):
    for a in soup.find_all("a", href=re.compile(r"^tel:")):
        raw = a["href"].replace("tel:", "")
        if raw not in MY_NUMBERS:
            d = normalize(raw)
            if d: return d
    return None

# ── Build primary+alt phone map ──────────────────────────────────────────────
leads = db.fetch_leads(DB_PATH)
phone_map: dict[str, int] = {}
for l in leads:
    d = normalize(l["phone"])
    if d: phone_map[d] = l["id"]
    if "Alt phones:" in (l["timeline"] or ""):
        alt_str = l["timeline"].split("Alt phones:")[-1].split(".")[0]
        for part in alt_str.split(","):
            d2 = normalize(part.strip())
            if d2: phone_map[d2] = l["id"]

# ── Build address keyword → lead_id map ──────────────────────────────────────
# Pull street number + street name from timeline Address field
addr_map: dict[str, int] = {}
for l in leads:
    tl = l["timeline"] or ""
    if "Address:" in tl:
        addr_raw = tl.split("Address:")[1].split(".")[0].strip().lower()
        # Use first two tokens (e.g. "303 1st", "1516 sheridan", "2150 chandler")
        tokens = addr_raw.split()
        if len(tokens) >= 2:
            key = f"{tokens[0]} {tokens[1]}"
            addr_map[key] = l["id"]

# ── Manual overrides: GV contact name fragment -> lead_id ────────────────────
# Add known cases where name-in-filename maps to a specific lead
MANUAL = {
    "303 1st": 44,   # Zachary Anderson
}

# ── Process unmatched named files ────────────────────────────────────────────
named_files = sorted([
    f for f in TAKEOUT.glob("*.html")
    if not re.match(r"^\+1\d{10}", f.name)
])

with db.get_conn(DB_PATH) as conn:
    existing = {
        (r["lead_id"], r["type"], r["timestamp"][:19])
        for r in conn.execute(
            "SELECT lead_id, type, timestamp FROM interactions"
        ).fetchall()
    }

    inserted = 0
    skipped = 0
    match_method: dict[str, int] = {}

    for fpath in named_files:
        m = re.search(r" - (Text|Placed|Received|Missed|Voicemail) - ", fpath.stem)
        if not m: continue
        call_type = m.group(1)
        if call_type == "Voicemail": continue

        html = fpath.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        phone = extract_phone(soup)

        lead_id = None
        method = None

        # 1. Phone match (primary/alt)
        if phone and phone in phone_map:
            lead_id = phone_map[phone]
            method = "phone"

        # 2. Manual override by address in filename
        if not lead_id:
            fname_lower = fpath.name.lower()
            for addr_key, lid in MANUAL.items():
                if addr_key.lower() in fname_lower:
                    lead_id = lid
                    method = "manual"
                    break

        # 3. Address keyword from filename vs lead timeline
        if not lead_id:
            fname_lower = fpath.name.lower()
            for addr_key, lid in addr_map.items():
                if addr_key in fname_lower:
                    lead_id = lid
                    method = "addr"
                    break

        if not lead_id:
            skipped += 1
            continue

        match_method[method] = match_method.get(method, 0) + 1
        itype = "text" if call_type == "Text" else "call"
        entries = (parse_text_file(html) if call_type == "Text"
                   else ([e] if (e := parse_call_file(html, call_type)) else []))

        for entry in entries:
            ts_str = entry["ts"].isoformat(timespec="seconds")
            key = (lead_id, itype, ts_str[:19])
            if key in existing: continue
            conn.execute(
                "INSERT INTO interactions (lead_id, type, content, timestamp, direction)"
                " VALUES (?,?,?,?,?)",
                (lead_id, itype, entry["content"], ts_str, entry["direction"]),
            )
            existing.add(key)
            inserted += 1

print(f"Inserted: {inserted}")
print(f"Match methods: {match_method}")
print(f"Still unmatched: {skipped}")

# Verify Zachary Anderson
ixns = db.fetch_interactions(44, DB_PATH)
texts = [i for i in ixns if i["type"] == "text"]
print(f"\nZachary Anderson texts now: {len(texts)}")
for t in sorted(texts, key=lambda x: x["timestamp"]):
    print(f"  [{t['direction']}] {t['timestamp'][:10]}  {t['content'][:80]}")
