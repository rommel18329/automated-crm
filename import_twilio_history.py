"""
Pull full Twilio message history and import into crm.db.
Matches by phone number to existing leads.
Safe to re-run — deduplicates by (lead_id, type, timestamp).
Run: python import_twilio_history.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=True)

from twilio.rest import Client
from crm_engine import db

import os
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")

DB_PATH = PROJECT_ROOT / "crm.db"


def normalize(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def fmt_phone(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def main() -> None:
    db.init_db(DB_PATH)

    # Build phone -> lead_id (primary + alt phones)
    leads = db.fetch_leads(DB_PATH)
    phone_map: dict[str, int] = {}
    for l in leads:
        d = normalize(l["phone"])
        if d:
            phone_map[d] = l["id"]
        if "Alt phones:" in (l["timeline"] or ""):
            alt_str = l["timeline"].split("Alt phones:")[-1].split(".")[0]
            for part in alt_str.split(","):
                d2 = normalize(part.strip())
                if d2:
                    phone_map[d2] = l["id"]

    print(f"Leads in DB: {len(leads)} | Phone entries: {len(phone_map)}")

    client = Client(ACCOUNT_SID, AUTH_TOKEN)

    print("Fetching Twilio message history...")
    messages = client.messages.list()  # fetches all pages
    print(f"Total Twilio messages: {len(messages)}")

    with db.get_conn(DB_PATH) as conn:
        existing = {
            (r["lead_id"], r["type"], r["timestamp"][:19])
            for r in conn.execute(
                "SELECT lead_id, type, timestamp FROM interactions"
            ).fetchall()
        }

        inserted = skipped_no_match = skipped_dup = 0

        for msg in messages:
            # Determine the lead's phone (the non-Twilio side)
            from_digits = normalize(msg.from_)
            to_digits   = normalize(msg.to)
            twilio_digits = normalize(FROM_NUMBER or "")

            if from_digits == twilio_digits:
                # Outbound: we sent it, lead is the recipient
                lead_digits = to_digits
                direction   = "outbound"
            else:
                # Inbound: lead sent it to us
                lead_digits = from_digits
                direction   = "inbound"

            if not lead_digits or lead_digits not in phone_map:
                skipped_no_match += 1
                continue

            lead_id = phone_map[lead_digits]
            body    = (msg.body or "").strip()
            if not body:
                skipped_no_match += 1
                continue

            # Convert Twilio datetime (UTC) to ISO string
            ts = msg.date_sent or msg.date_created
            if ts:
                if hasattr(ts, "astimezone"):
                    ts_str = ts.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
                else:
                    ts_str = str(ts)[:19]
            else:
                skipped_no_match += 1
                continue

            dedup_key = (lead_id, "text", ts_str[:19])
            if dedup_key in existing:
                skipped_dup += 1
                continue

            conn.execute(
                "INSERT INTO interactions (lead_id, type, content, timestamp, direction)"
                " VALUES (?,?,?,?,?)",
                (lead_id, "text", body, ts_str, direction),
            )
            existing.add(dedup_key)
            inserted += 1

    print(f"\nInserted:           {inserted}")
    print(f"Duplicates skipped: {skipped_dup}")
    print(f"No match / skipped: {skipped_no_match}")
    print("\nDone. Restart dashboard to see updated conversations.")


if __name__ == "__main__":
    main()
