"""Assign local phone numbers to a Trust Hub Customer Profile so they can be
registered for Voice Integrity.

Voice Integrity's "Edit included phone numbers" only shows numbers already
attached to the selected Customer Profile. New numbers aren't auto-attached,
so this assigns them.

Skips toll-free numbers (they use a separate verification process).

Usage:
  python assign_numbers_to_profile.py            # preview what would be assigned
  python assign_numbers_to_profile.py --assign   # actually assign
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

SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

# INVI Equity Group LLC (the profile selectable for Voice Integrity)
PROFILE_SID = "BUe6d232fa87e16e5feecde7f6dca9c549"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assign", action="store_true", help="Actually assign (default: preview)")
    ap.add_argument("--profile", default=PROFILE_SID, help="Customer Profile SID")
    args = ap.parse_args()

    client = Client(SID, TOKEN)

    nums = client.incoming_phone_numbers.list(limit=100)
    # Local numbers only (skip toll-free 8xx)
    local = [n for n in nums if not n.phone_number[2:5] in ("800","833","844","855","866","877","888")]

    print(f"Profile: {args.profile}")
    print(f"Local numbers to assign ({len(local)}):")
    for n in local:
        print(f"  {n.phone_number}  SID={n.sid}")

    if not args.assign:
        print("\n[PREVIEW] Re-run with --assign to attach these to the profile.")
        return

    # Find already-assigned numbers to avoid duplicate errors
    existing = client.trusthub.v1.customer_profiles(args.profile) \
        .customer_profiles_channel_endpoint_assignment.list(limit=100)
    already = {a.channel_endpoint_sid for a in existing}

    print("\nAssigning...")
    done = 0
    for n in local:
        if n.sid in already:
            print(f"  - {n.phone_number} already assigned, skipping")
            continue
        try:
            client.trusthub.v1.customer_profiles(args.profile) \
                .customer_profiles_channel_endpoint_assignment.create(
                    channel_endpoint_type="phone-number",
                    channel_endpoint_sid=n.sid,
                )
            done += 1
            print(f"  + {n.phone_number} assigned")
        except Exception as exc:
            print(f"  ! {n.phone_number} failed: {exc}")

    print(f"\nDone. Assigned {done} numbers to the profile.")
    print("Now refresh the Voice Integrity 'Edit included phone numbers' modal.")


if __name__ == "__main__":
    main()
