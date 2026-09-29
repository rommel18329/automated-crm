"""Import named-contact Google Voice files that were missed in the first pass.
Matches by phone extracted from HTML, using both primary + alt phones.
Safe to re-run — deduplicates by (lead_id, type, timestamp).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db
from import_google_voice import parse_text_file, parse_call_file, MY_NUMBERS

TAKEOUT_DIR = Path(r"C:\Users\jimen\Desktop\Takeout\Voice\Calls")
DB_PATH = PROJECT_ROOT / "crm.db"


def normalize(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def extract_phone(soup: BeautifulSoup) -> str | None:
    for a in soup.find_all("a", href=re.compile(r"^tel:")):
        raw = a["href"].replace("tel:", "")
        if raw not in MY_NUMBERS:
            d = normalize(raw)
            if d:
                return d
    return None


def build_phone_map(leads: list[dict]) -> dict[str, int]:
    """Map every known phone (primary + alt) to lead_id."""
    phone_map: dict[str, int] = {}
    for l in leads:
        d = normalize(l["phone"])
        if d:
            phone_map[d] = l["id"]
        # Alt phones stored in timeline as "Alt phones: xxx, yyy"
        if "Alt phones:" in (l["timeline"] or ""):
            alt_str = l["timeline"].split("Alt phones:")[-1].split(".")[0]
            for part in alt_str.split(","):
                d2 = normalize(part.strip())
                if d2:
                    phone_map[d2] = l["id"]
    return phone_map


def main() -> None:
    db.init_db(DB_PATH)
    leads = db.fetch_leads(DB_PATH)
    phone_map = build_phone_map(leads)
    print(f"Phone lookup entries: {len(phone_map)}")

    # Only process named-contact files (not starting with +1XXXXXXXXXX)
    named_files = sorted([
        f for f in TAKEOUT_DIR.glob("*.html")
        if not re.match(r"^\+1\d{10}", f.name)
    ])
    print(f"Named-contact HTML files: {len(named_files)}")

    with db.get_conn(DB_PATH) as conn:
        existing = {
            (r["lead_id"], r["type"], r["timestamp"][:19])
            for r in conn.execute(
                "SELECT lead_id, type, timestamp FROM interactions"
            ).fetchall()
        }

        inserted = skipped_no_match = skipped_dup = 0

        for fpath in named_files:
            m = re.search(r" - (Text|Placed|Received|Missed|Voicemail) - ", fpath.stem)
            if not m:
                continue
            call_type = m.group(1)
            if call_type == "Voicemail":
                continue

            html = fpath.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            phone = extract_phone(soup)

            if not phone or phone not in phone_map:
                skipped_no_match += 1
                continue

            lead_id = phone_map[phone]
            itype = "text" if call_type == "Text" else "call"
            entries = parse_text_file(html) if call_type == "Text" else (
                [e] if (e := parse_call_file(html, call_type)) else []
            )

            for entry in entries:
                ts_str = entry["ts"].isoformat(timespec="seconds")
                key = (lead_id, itype, ts_str[:19])
                if key in existing:
                    skipped_dup += 1
                    continue
                conn.execute(
                    "INSERT INTO interactions (lead_id, type, content, timestamp, direction)"
                    " VALUES (?,?,?,?,?)",
                    (lead_id, itype, entry["content"], ts_str, entry["direction"]),
                )
                existing.add(key)
                inserted += 1

    print(f"\nInserted:           {inserted}")
    print(f"Duplicates skipped: {skipped_dup}")
    print(f"No match (skipped): {skipped_no_match}")
    print("\nDone. Restart dashboard.")


if __name__ == "__main__":
    main()
