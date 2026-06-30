"""Voice Integrity / Trust Hub status + guided setup for the dialer.

Voice Integrity registers your numbers with the T-Mobile / Verizon / AT&T
analytics engines so they aren't labeled "Spam Likely". It requires an
APPROVED Business Profile in Twilio Trust Hub (EIN, US address, authorized
rep, https website) — which Twilio reviews manually, so the bundle itself
can't be fully auto-created blind.

This script is SAFE: it only READS your current Trust Hub state and prints
exactly what's done and what's left. It does not create or submit anything.

Usage:  python register_voice_integrity.py
"""
from __future__ import annotations

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


def main() -> None:
    client = Client(SID, TOKEN)

    print("=" * 60)
    print("TRUST HUB / VOICE INTEGRITY STATUS")
    print("=" * 60)

    # 1. Business profiles (Customer Profiles in Trust Hub)
    try:
        profiles = client.trusthub.v1.customer_profiles.list(limit=20)
        if profiles:
            print(f"\n[OK] Business Profile(s) found ({len(profiles)}):")
            for p in profiles:
                print(f"    {p.friendly_name}  status={p.status}  sid={p.sid}")
        else:
            print("\n[TODO] No Business Profile yet.")
            print("    -> console.twilio.com -> Trust Hub -> Business Profile")
            print("    Needs: EIN, US business address, authorized rep, https website.")
    except Exception as exc:
        print(f"\n[?] Could not read customer profiles: {exc}")

    # 2. Trust products (Voice Integrity registers as a Trust Hub trust product)
    try:
        products = client.trusthub.v1.trust_products.list(limit=50)
        vi = [b for b in products if "integrity" in (b.friendly_name or "").lower()
              or "voice" in (b.friendly_name or "").lower()]
        if vi:
            print(f"\n[OK] Voice Integrity / trust product(s):")
            for b in vi:
                print(f"    {b.friendly_name}  status={b.status}  sid={b.sid}")
        elif products:
            print(f"\n[INFO] Trust products exist but none look like Voice Integrity:")
            for b in products:
                print(f"    {b.friendly_name}  status={b.status}")
        else:
            print("\n[TODO] No Voice Integrity trust product yet.")
            print("    -> After Business Profile is APPROVED:")
            print("       console.twilio.com -> Trust Hub -> Voice Integrity")
    except Exception as exc:
        print(f"\n[?] Could not read trust products: {exc}")

    # 3. Numbers on account
    try:
        nums = client.incoming_phone_numbers.list(limit=50)
        print(f"\nNumbers on account ({len(nums)}):")
        for n in nums:
            print(f"    {n.phone_number}  '{n.friendly_name}'")
    except Exception as exc:
        print(f"\n[?] Could not read numbers: {exc}")

    print("\n" + "=" * 60)
    print("NEXT STEPS (in order):")
    print("  1. Trust Hub -> Business Profile  (submit EIN/address/rep/website)")
    print("  2. Wait for Twilio approval (usually 1-3 business days)")
    print("  3. Trust Hub -> Voice Integrity -> register your pool numbers")
    print("  4. Enable CNAM so your business name shows on caller ID")
    print("  5. Re-run this script to confirm everything is APPROVED")
    print("=" * 60)


if __name__ == "__main__":
    main()
