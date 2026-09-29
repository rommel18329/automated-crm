"""One-shot import: 'the rest.xlsx' -> crm.db as cold leads."""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db
from import_xlsx import parse_notes, normalize_phone

XLSX_PATH = Path(r"C:\Users\jimen\Downloads\the rest.xlsx")
DB_PATH = PROJECT_ROOT / "crm.db"

db.init_db(DB_PATH)

df = pd.read_excel(XLSX_PATH, dtype=str, header=None)
print(f"Rows in file: {len(df)}")


def cell(row: dict, idx: int) -> str:
    v = row.get(idx, "")
    return "" if str(v).strip().lower() in ("nan", "") else str(v).strip()


inserted = updated = skipped = 0

with db.get_conn(DB_PATH) as conn:
    phone_to_id = {
        r["phone"]: r["id"]
        for r in conn.execute("SELECT id, phone FROM leads").fetchall()
    }

    for _, row in df.iterrows():
        row = dict(row)

        first = cell(row, 0)
        last = cell(row, 1)
        name = f"{first} {last}".strip() or "Unknown"

        # Phones in columns 3-7
        phones = [normalize_phone(cell(row, i)) for i in range(3, 8)]
        primary = next((p for p in phones if p), None)
        if not primary:
            print(f"  SKIP (no phone): {name}")
            skipped += 1
            continue
        alt = [p for p in phones[1:] if p]

        # Address: col 8 may be full or just street; city/state/zip in 9-11
        addr8 = cell(row, 8).rstrip(",")
        city = cell(row, 9)
        state = cell(row, 10)
        zip_ = cell(row, 11)
        if city or state:
            addr_parts = [p for p in [addr8, city, state, zip_] if p]
        else:
            addr_parts = [addr8] if addr8 else []
        address_str = ", ".join(addr_parts)

        # Last call date (col 20) and touch count (col 21)
        last_call_raw = cell(row, 20)
        last_contact = None
        if last_call_raw:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%Y-%m-%d"):
                try:
                    last_contact = datetime.strptime(last_call_raw[:19], fmt).date().isoformat()
                    break
                except ValueError:
                    pass
        try:
            touch_count = int(float(cell(row, 21))) if cell(row, 21) else 0
        except ValueError:
            touch_count = 0

        # Build timeline string
        timeline_parts = []
        if address_str:
            timeline_parts.append(f"Address: {address_str}")
        if alt:
            timeline_parts.append(f"Notes: Alt phones: {', '.join(alt)}")
        timeline = ". ".join(timeline_parts) or "No address on file"

        notes_raw = cell(row, 12)
        existing_id = phone_to_id.get(primary)

        if existing_id:
            conn.execute(
                """UPDATE leads SET name=?, status=?, motivation_score=?,
                   timeline=?, last_contact_date=?, touch_count=?,
                   conversation_stage=?, deal_probability=? WHERE id=?""",
                (name, "cold", 3, timeline, last_contact, touch_count,
                 "initial_contact", 10, existing_id),
            )
            lead_id = existing_id
            updated += 1
        else:
            conn.execute(
                """INSERT INTO leads (name, phone, status, motivation_score, timeline,
                   timeline_days, last_contact_date, next_action_date, touch_count,
                   last_strategy_used, conversation_stage, deal_probability)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, primary, "cold", 3, timeline, None, last_contact, None,
                 touch_count, None, "initial_contact", 10),
            )
            lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            phone_to_id[primary] = lead_id
            inserted += 1

        # Import notes, skip duplicates
        existing_notes = {
            r["content"]
            for r in conn.execute(
                "SELECT content FROM interactions WHERE lead_id=? AND type='note'",
                (lead_id,),
            ).fetchall()
        }
        for entry in parse_notes(notes_raw):
            if entry["content"] not in existing_notes:
                conn.execute(
                    "INSERT INTO interactions (lead_id, type, content, timestamp, direction)"
                    " VALUES (?,?,?,?,?)",
                    (lead_id, "note", entry["content"],
                     entry["ts"].isoformat(timespec="seconds"), "inbound"),
                )
                existing_notes.add(entry["content"])

print(f"\nDone.")
print(f"  Inserted (new): {inserted}")
print(f"  Updated (existing phone match): {updated}")
print(f"  Skipped (no valid phone): {skipped}")
print("Restart dashboard to see changes.")
