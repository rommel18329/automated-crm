# One-shot import script: import.xlsx -> crm.db
# Run: python import_xlsx.py
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── path setup ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db  # noqa: E402

XLSX_PATH = Path(r"C:\Users\jimen\Downloads\import.xlsx")
DB_PATH = PROJECT_ROOT / "crm.db"

# ── helpers ───────────────────────────────────────────────────────────────────

def normalize_phone(raw) -> str | None:
    if not raw or str(raw).strip().lower() in ("nan", ""):
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"


STATUS_MAP = {
    "hot lead": "hot",
    "warm lead": "warm",
    "cold lead": "cold",
    "not interested": "dead",
    "bad phone": "dead",
    "dnc": "dead",
    "opt-out": "dead",
    "dead": "dead",
    "contract": "contract",
    "cash buyer": "cold",  # cash buyers are potential leads
}

# Only skip rows that are literal header / section rows in the spreadsheet
SKIP_STATUS_VALUES = {"status"}


def map_status(raw) -> str | None:
    """Return CRM status string, or None to skip the row entirely (header rows only)."""
    s = str(raw or "").strip().lower()
    if s in SKIP_STATUS_VALUES:
        return None
    return STATUS_MAP.get(s, "cold")  # blank / unknown → cold


# After the date we often see "@HH:MM AM/PM TZ -- Name --"; capture the time.
_TIME_IN_LINE = re.compile(r"(\d{1,2}:\d{2})\s*(AM|PM)", re.IGNORECASE)

# Strips the date/time/name header from the start of a segment, leaving pure content.
_NAME_PREFIX = re.compile(
    r"^[-–—📌v\s]*"
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r"(?:\s*[@–—\-]+\s*\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[A-Z]{0,4})?"
    r"(?:\s*[-–—]+\s*[^-–—\n]{2,40}[-–—]+)?"
    r"[\s\-–—]*",
    re.IGNORECASE,
)

# A "date boundary" in the raw text — either at the start of a line,
# OR after a sentence-ending period+space (inline date starts).
# We also handle a leading "v" shorthand (e.g. "v04/02/2026").
_DATE_BOUNDARY = re.compile(
    r"(?m)"
    r"(?:"
        r"(?:(?:^|\n)[-–—📌v\s]*)"   # start of line with optional junk incl. 'v'
        r"|"
        r"(?:\.\s+v?)"                # after sentence-ending ". " (inline date)
    r")"
    r"(\d{1,2}/\d{1,2}/\d{2,4})"     # the date
    r"[^\n]*",                         # rest of that header line
)


def _parse_date(date_str: str, time_m=None) -> datetime:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            base = datetime.strptime(date_str, fmt)
            if time_m:
                h = int(time_m.group(1).split(":")[0])
                mi = int(time_m.group(1).split(":")[1])
                ampm = time_m.group(2).upper()
                if ampm == "PM" and h != 12:
                    h += 12
                elif ampm == "AM" and h == 12:
                    h = 0
                return base.replace(hour=h, minute=mi)
            return base
        except ValueError:
            continue
    return datetime.utcnow()


def _normalise_text(text: str) -> str:
    """Pre-process raw notes so every inline date start becomes its own line.

    Handles the common patterns found in dialer exports and manual notes:
      " -- DATE"          standard dialer separator (space-dash-dash-space-date)
      " -- --DATE"        double-separator variant
      ' "-- --DATE'       quoted separator (leading quote before dashes)
      ". -DATE"           manual note starting with dash after sentence
      ". DATE"            bare inline date after sentence
      "vDATE"             'v' shorthand at start of string
    """
    text = text.strip()
    # Strip leading 'v' shorthand at start of string: "v04/02/2026" → "04/02/2026"
    text = re.sub(r"^v(\d{1,2}/\d{1,2}/\d{2,4})", r"\1", text)

    # Move " -- DATE" and variants to new lines.
    # Captures: optional space/tabs/quotes, then one-or-more dashes, then
    # optional more dashes/spaces (e.g. "-- --"), then the date digits.
    text = re.sub(
        r'[ \t"\']+[-–—][-–—\s]*(\d{1,2}/\d{1,2}/\d{2,4})',
        r"\n\1",
        text,
    )

    # Move sentence-boundary inline dates: ". DATE" or ". -DATE" or ". vDATE"
    # (catches anything not already moved to a new line by the rule above)
    text = re.sub(
        r"\.\s+[-–—v]?(\d{1,2}/\d{1,2}/\d{2,4})",
        r".\n\1",
        text,
    )
    return text


def parse_notes(raw_notes: str) -> list[dict]:
    """Split compound notes field into individual timestamped entries.

    Handles:
      - "-- MM/DD/YYYY @ HH:MM PM TZ -- Name -- content"  (standard dialer format)
      - "MM/DD/YYYY – content" / "MM/DD/YY content"
      - "v04/02/2026- content"  (v-shorthand prefix)
      - Date buried after a sentence: "...note. 3/31/2026 – new note"
      - "-📌 MM/DD/YYYY\\ncontent"
      - Unstructured text (no date → single note, timestamp = import time)
    """
    if not raw_notes or str(raw_notes).strip().lower() in ("nan", ""):
        return []

    text = _normalise_text(str(raw_notes).strip())

    # Find all date boundary positions (each one starts a new note entry)
    matches = list(_DATE_BOUNDARY.finditer(text))

    if not matches:
        return [{"ts": datetime.utcnow(), "content": text}]

    entries = []

    # Any content before the first date → unstructured note
    pre = text[: matches[0].start()].strip().rstrip(".")
    if pre:
        entries.append({"ts": datetime.utcnow(), "content": pre})

    for i, m in enumerate(matches):
        seg_start = m.start()
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[seg_start:seg_end].strip()
        if not segment:
            continue

        date_str = m.group(1)
        header_line = m.group(0)
        time_m = _TIME_IN_LINE.search(header_line)
        ts = _parse_date(date_str, time_m)

        # Strip the date / time / "-- Name --" header, keep only the note body
        content = _NAME_PREFIX.sub("", segment, count=1).lstrip("–—-. \n").strip()
        if not content:
            content = segment

        entries.append({"ts": ts, "content": content})

    return entries


def cell(row, *keys):
    """Return first non-empty value from row[key] for multiple possible column names."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip().lower() not in ("nan", ""):
            return str(v).strip()
    return ""


# ── load Excel ────────────────────────────────────────────────────────────────

print(f"Reading {XLSX_PATH} ...")
df = pd.read_excel(XLSX_PATH, dtype=str)
df.columns = [str(c).strip() for c in df.columns]

print(f"Columns: {list(df.columns)}")
print(f"Rows: {len(df)}")

# ── ensure DB is initialised ──────────────────────────────────────────────────
db.init_db(DB_PATH)

# ── load existing phones for dedup ────────────────────────────────────────────
with db.get_conn(DB_PATH) as conn:
    existing_phones = {
        r["phone"] for r in conn.execute("SELECT phone FROM leads").fetchall()
    }

# ── process rows ──────────────────────────────────────────────────────────────
inserted = 0
skipped_junk = 0
updated = 0
skipped_no_phone = 0
interaction_rows: list[tuple] = []  # (lead_id, type, content, timestamp, direction)

new_leads: list[dict] = []

for _, row in df.iterrows():
    row = dict(row)

    # ── status ────────────────────────────────────────────────────────────────
    status = map_status(cell(row, "Status"))
    if status is None:
        skipped_junk += 1
        continue

    # ── name ──────────────────────────────────────────────────────────────────
    first = cell(row, "First Name", "First")
    last = cell(row, "Last Name", "Last")
    name = f"{first} {last}".strip() or "Unknown"

    # ── phones — try all phone columns, take first valid ──────────────────────
    phone_cols = ["Phone", "Phone 2", "Phone 3", "Phone 4", "Phone 5"]
    primary_phone = None
    alt_phones = []
    for col in phone_cols:
        p = normalize_phone(cell(row, col))
        if p:
            if primary_phone is None:
                primary_phone = p
            else:
                alt_phones.append(p)

    if primary_phone is None:
        skipped_no_phone += 1
        print(f"  SKIP (no valid phone): {name}")
        continue

    # mark phone as seen so we don't double-insert within the same batch
    existing_phones.add(primary_phone)

    # ── timeline compound string ──────────────────────────────────────────────
    addr = cell(row, "Address1", "Address 1", "Address")
    city = cell(row, "City")
    state = cell(row, "State")
    zip_ = cell(row, "Zip", "ZIP")
    tags = cell(row, "Tags", "Tag")
    folder = cell(row, "Folder")

    address_parts = [p for p in [addr, city, state, zip_] if p]
    address_str = ", ".join(address_parts)

    timeline_parts: list[str] = []
    if address_str:
        timeline_parts.append(f"Address: {address_str}")
    if tags:
        timeline_parts.append(f"Situation: {tags}")
    if folder:
        timeline_parts.append(f"Notes: Folder={folder}")
    if alt_phones:
        timeline_parts.append(f"Notes: Alt phones: {', '.join(alt_phones)}")

    timeline = ". ".join(timeline_parts) or "No address on file"

    # ── motivation score — derive from status ─────────────────────────────────
    motivation = {"hot": 8, "warm": 5, "cold": 3, "dead": 2, "contract": 9}.get(status, 5)

    # ── conversation stage ────────────────────────────────────────────────────
    total_calls_raw = cell(row, "Total Calls", "Total calls")
    try:
        total_calls = int(float(total_calls_raw)) if total_calls_raw else 0
    except ValueError:
        total_calls = 0

    if status in ("hot", "contract"):
        stage = "negotiating"
    elif status == "warm" or total_calls >= 2:
        stage = "qualifying"
    elif total_calls == 1:
        stage = "initial_contact"
    else:
        stage = "not_contacted"

    # ── last contact date ─────────────────────────────────────────────────────
    last_call_raw = cell(row, "Last Call", "Last call")
    last_contact = None
    if last_call_raw:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
            try:
                last_contact = datetime.strptime(last_call_raw, fmt).date().isoformat()
                break
            except ValueError:
                pass

    # ── deal probability ──────────────────────────────────────────────────────
    deal_prob = {"hot": 60, "warm": 30, "cold": 10, "dead": 0, "contract": 90}.get(status, 10)

    new_leads.append({
        "name": name,
        "phone": primary_phone,
        "status": status,
        "motivation_score": motivation,
        "timeline": timeline,
        "timeline_days": None,
        "last_contact_date": last_contact,
        "next_action_date": None,
        "touch_count": total_calls,
        "last_strategy_used": None,
        "conversation_stage": stage,
        "deal_probability": deal_prob,
        "_notes_raw": cell(row, "Notes", "Note"),
    })

# ── insert leads + interactions ───────────────────────────────────────────────
print(f"\nUpserting {len(new_leads)} leads ...")

with db.get_conn(DB_PATH) as conn:
    # build phone -> id map for existing leads
    phone_to_id = {
        r["phone"]: r["id"]
        for r in conn.execute("SELECT id, phone FROM leads").fetchall()
    }

    for lead in new_leads:
        notes_raw = lead.pop("_notes_raw")
        existing_id = phone_to_id.get(lead["phone"])

        if existing_id:
            # UPDATE — overwrite with real data from Excel
            conn.execute(
                """
                UPDATE leads SET
                    name=?, status=?, motivation_score=?, timeline=?,
                    last_contact_date=?, touch_count=?,
                    conversation_stage=?, deal_probability=?
                WHERE id=?
                """,
                (
                    lead["name"],
                    lead["status"],
                    lead["motivation_score"],
                    lead["timeline"],
                    lead["last_contact_date"],
                    lead["touch_count"],
                    lead["conversation_stage"],
                    lead["deal_probability"],
                    existing_id,
                ),
            )
            lead_id = existing_id
            updated += 1
        else:
            conn.execute(
                """
                INSERT INTO leads (
                    name, phone, status, motivation_score, timeline, timeline_days,
                    last_contact_date, next_action_date, touch_count, last_strategy_used,
                    conversation_stage, deal_probability
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    lead["name"],
                    lead["phone"],
                    lead["status"],
                    lead["motivation_score"],
                    lead["timeline"],
                    lead["timeline_days"],
                    lead["last_contact_date"],
                    lead["next_action_date"],
                    lead["touch_count"],
                    lead["last_strategy_used"],
                    lead["conversation_stage"],
                    lead["deal_probability"],
                ),
            )
            lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            inserted += 1

        # parse notes into interaction rows (only add new ones — avoid dupes)
        existing_note_contents = {
            r["content"]
            for r in conn.execute(
                "SELECT content FROM interactions WHERE lead_id=? AND type='note'",
                (lead_id,),
            ).fetchall()
        }
        note_entries = parse_notes(notes_raw)
        for entry in note_entries:
            if entry["content"] not in existing_note_contents:
                conn.execute(
                    "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
                    (
                        lead_id,
                        "note",
                        entry["content"],
                        entry["ts"].isoformat(timespec="seconds"),
                        "inbound",
                    ),
                )
                existing_note_contents.add(entry["content"])

# ── summary ───────────────────────────────────────────────────────────────────
print("\n--- Import Results ---")
print(f"  Inserted (new leads) : {inserted}")
print(f"  Updated (existing)   : {updated}")
print(f"  Skipped (no phone)   : {skipped_no_phone}")
print(f"  Skipped (junk status): {skipped_junk}")
print("---------------------")
print("Done. Restart the dashboard to see imported leads.")
