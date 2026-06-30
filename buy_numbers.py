"""Buy local numbers for the multi-line dialer caller-ID pool.

SAFE BY DEFAULT: running with no flags only PREVIEWS available numbers and
costs nothing. Pass --buy to actually purchase (this spends money, ~$1.15/mo
each). Purchased numbers are saved to the caller_id_pool table (warming).

Usage:
  python buy_numbers.py                      # preview 10 available 515 numbers
  python buy_numbers.py --area 515 --count 10
  python buy_numbers.py --area 515 --count 10 --buy   # actually purchase
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=True)

from twilio.rest import Client
from crm_engine import db

SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
BASE = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="515", help="Area code to search (default 515)")
    ap.add_argument("--count", type=int, default=10, help="How many numbers")
    ap.add_argument("--buy", action="store_true", help="Actually purchase (spends money)")
    args = ap.parse_args()

    client = Client(SID, TOKEN)
    db.init_db(PROJECT_ROOT / "crm.db")

    print(f"Searching for {args.count} available numbers in area code {args.area}...")
    available = client.available_phone_numbers("US").local.list(
        area_code=args.area, voice_enabled=True, sms_enabled=True, limit=args.count
    )

    if not available:
        print(f"No numbers available in {args.area}. Try a nearby Iowa area code "
              f"(515, 641, 319, 712, 563).")
        return

    print(f"\nFound {len(available)} numbers:")
    for n in available:
        print(f"  {n.phone_number}  {n.friendly_name}")

    if not args.buy:
        print(f"\n[PREVIEW ONLY] No numbers purchased. "
              f"Re-run with --buy to purchase these {len(available)} numbers "
              f"(~${len(available) * 1.15:.2f}/month).")
        return

    print(f"\nPurchasing {len(available)} numbers...")
    bought = 0
    for n in available:
        try:
            purchased = client.incoming_phone_numbers.create(
                phone_number=n.phone_number,
                friendly_name=f"CRM Dialer {args.area}",
                voice_url=f"{BASE}/voice/incoming",
                voice_method="POST",
                sms_url=f"{BASE}/twilio/inbound",
                sms_method="POST",
            )
            db.add_caller_number(
                phone_number=purchased.phone_number,
                area_code=args.area,
                twilio_sid=purchased.sid,
                warming=True,
            )
            bought += 1
            print(f"  ✓ {purchased.phone_number}  SID={purchased.sid}")
        except Exception as exc:
            print(f"  ✗ {n.phone_number} failed: {exc}")

    print(f"\nDone. Purchased {bought} numbers, added to caller_id_pool (warming).")
    print(f"Monthly cost: ~${bought * 1.15:.2f}")


if __name__ == "__main__":
    main()
