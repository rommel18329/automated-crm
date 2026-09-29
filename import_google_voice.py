"""Import Google Voice Takeout -> crm.db
Matches each HTML file to a lead by phone number.
Imports texts (inbound + outbound) and calls (placed/received/missed).
Safe to re-run — skips exact duplicates by (lead_id, type, timestamp).
Run: python import_google_voice.py
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

TAKEOUT_DIR = Path(r"C:\Users\jimen\Desktop\Takeout\Voice\Calls")
DB_PATH = PROJECT_ROOT / "crm.db"

MY_NUMBERS = {"+17732178686", "+15154618235"}  # your Google Voice / Twilio numbers


# ── helpers ──────────────────────────────────────────────────────────────────

def normalize_e164(raw: str) -> str | None:
    """Strip +1 country code, return 10-digit string or None."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def fmt_phone(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def parse_iso(title: str) -> datetime | None:
    """Parse the title attr of <abbr class='dt'> or <abbr class='published'>."""
    # e.g. "2025-04-30T16:11:20.477-05:00"
    try:
        # Python 3.11+ handles this natively; strip sub-seconds for 3.10
        clean = re.sub(r"\.\d+", "", title)
        return datetime.fromisoformat(clean).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def extract_phone_from_html(soup: BeautifulSoup) -> str | None:
    """Find the non-Me phone number from any tel: link in the file."""
    for a in soup.find_all("a", href=re.compile(r"^tel:")):
        raw = a["href"].replace("tel:", "")
        if raw not in MY_NUMBERS:
            digits = normalize_e164(raw)
            if digits:
                return digits
    return None


def parse_text_file(html: str) -> list[dict]:
    """Return list of {ts, direction, content} from a Text HTML file."""
    soup = BeautifulSoup(html, "html.parser")
    messages = []
    for div in soup.find_all("div", class_="message"):
        abbr = div.find("abbr", class_="dt")
        if not abbr:
            continue
        ts = parse_iso(abbr.get("title", ""))
        if not ts:
            continue

        # Determine sender: "Me" = outbound
        sender_a = div.find("a", class_="tel")
        sender_phone = sender_a["href"].replace("tel:", "") if sender_a else ""
        direction = "outbound" if sender_phone in MY_NUMBERS else "inbound"

        q = div.find("q")
        content = q.get_text(" ", strip=True) if q else ""
        if content:
            messages.append({"ts": ts, "direction": direction, "content": content})
    return messages


def parse_call_file(html: str, call_type: str) -> dict | None:
    """Return {ts, direction, content} for a Placed/Received/Missed call."""
    soup = BeautifulSoup(html, "html.parser")
    abbr = soup.find("abbr", class_="published")
    if not abbr:
        return None
    ts = parse_iso(abbr.get("title", ""))
    if not ts:
        return None

    duration_abbr = soup.find("abbr", class_="duration")
    duration = ""
    if duration_abbr:
        # title="PT57S" -> "0:57"
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_abbr.get("title", ""))
        if m:
            h, mn, s = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
            duration = f" ({h}h {mn}m {s}s)" if h else f" ({mn}m {s}s)" if mn else f" ({s}s)"

    if call_type == "Placed":
        direction, label = "outbound", f"Outbound call{duration}"
    elif call_type == "Received":
        direction, label = "inbound", f"Inbound call{duration}"
    else:  # Missed
        direction, label = "inbound", "Missed call"

    return {"ts": ts, "direction": direction, "content": label}


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    db.init_db(DB_PATH)

    # Build phone -> lead_id lookup (10-digit keys)
    with db.get_conn(DB_PATH) as conn:
        leads_by_phone = {}
        for row in conn.execute("SELECT id, phone FROM leads").fetchall():
            digits = normalize_e164(row["phone"])
            if digits:
                leads_by_phone[digits] = row["id"]

    print(f"Leads in DB: {len(leads_by_phone)}")

    html_files = list(TAKEOUT_DIR.glob("*.html"))
    print(f"HTML files in Takeout: {len(html_files)}\n")

    inserted = skipped_no_match = skipped_dup = 0
    matched_leads: set[int] = set()

    with db.get_conn(DB_PATH) as conn:
        # Load existing (lead_id, type, timestamp) to detect duplicates
        existing = {
            (r["lead_id"], r["type"], r["timestamp"][:19])
            for r in conn.execute("SELECT lead_id, type, timestamp FROM interactions").fetchall()
        }

        for fpath in sorted(html_files):
            fname = fpath.stem  # e.g. "+12096170832 - Text - 2025-04-30T21_11_20Z"

            # Determine call type from filename
            type_match = re.search(r" - (Text|Placed|Received|Missed|Voicemail) - ", fname)
            if not type_match:
                continue
            call_type = type_match.group(1)
            if call_type == "Voicemail":
                continue  # skip voicemails

            # Try to get phone from filename first
            phone_match = re.match(r"^\+1(\d{10})", fname)
            lead_phone_digits = phone_match.group(1) if phone_match else None

            # If not in filename, parse HTML to find it
            html = fpath.read_text(encoding="utf-8", errors="replace")
            if not lead_phone_digits:
                soup = BeautifulSoup(html, "html.parser")
                lead_phone_digits = extract_phone_from_html(soup)

            if not lead_phone_digits or lead_phone_digits not in leads_by_phone:
                skipped_no_match += 1
                continue

            lead_id = leads_by_phone[lead_phone_digits]
            matched_leads.add(lead_id)

            # Parse interactions from this file
            if call_type == "Text":
                entries = parse_text_file(html)
                itype = "text"
            else:
                entry = parse_call_file(html, call_type)
                entries = [entry] if entry else []
                itype = "call"

            for entry in entries:
                ts_str = entry["ts"].isoformat(timespec="seconds")
                dedup_key = (lead_id, itype, ts_str[:19])
                if dedup_key in existing:
                    skipped_dup += 1
                    continue
                conn.execute(
                    "INSERT INTO interactions (lead_id, type, content, timestamp, direction)"
                    " VALUES (?,?,?,?,?)",
                    (lead_id, itype, entry["content"], ts_str, entry["direction"]),
                )
                existing.add(dedup_key)
                inserted += 1

    print(f"Matched leads: {len(matched_leads)}")
    print(f"Interactions inserted: {inserted}")
    print(f"Duplicates skipped: {skipped_dup}")
    print(f"Files with no matching lead: {skipped_no_match}")
    print("\nDone. Restart dashboard to see conversations.")


if __name__ == "__main__":
    main()
