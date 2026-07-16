"""One-time setup: create a Twilio Voice Intelligence service for the CRM and
write its SID to .env as VOICE_INTELLIGENCE_SERVICE_SID.

After a recording finishes, webhook_server posts it to this service for
transcription; Twilio calls back /voice/transcript when the text is ready,
and the transcript is saved to the lead's history.

Cost: ~$0.02 per minute of recorded audio.

Run once:  python setup_transcription.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from twilio.rest import Client

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
load_dotenv(ENV)

SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TOK = os.getenv("TWILIO_AUTH_TOKEN", "")
BASE = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def _set_env(key: str, value: str) -> None:
    text = ENV.read_text() if ENV.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.MULTILINE):
        text = re.sub(rf"^{key}=.*$", line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    ENV.write_text(text)


def main() -> None:
    if not SID or not TOK:
        raise SystemExit("Missing TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN in .env")
    c = Client(SID, TOK)

    existing = c.intelligence.v2.services.list(limit=20)
    svc = next((s for s in existing if s.unique_name == "crm-transcription"), None)

    webhook = f"{BASE}/voice/transcript" if BASE else None
    if svc:
        print(f"Reusing Voice Intelligence service {svc.sid}")
        if webhook:
            c.intelligence.v2.services(svc.sid).update(
                webhook_url=webhook, webhook_http_method="POST")
            print(f"  webhook -> {webhook}")
    else:
        kwargs = dict(
            unique_name="crm-transcription",
            friendly_name="CRM Call Transcription",
            auto_transcribe=True,       # transcribe every recording sent in
            language_code="en-US",
        )
        if webhook:
            kwargs["webhook_url"] = webhook
            kwargs["webhook_http_method"] = "POST"
        svc = c.intelligence.v2.services.create(**kwargs)
        print(f"Created Voice Intelligence service {svc.sid}")
        if webhook:
            print(f"  webhook -> {webhook}")

    _set_env("VOICE_INTELLIGENCE_SERVICE_SID", svc.sid)
    print(f"Wrote VOICE_INTELLIGENCE_SERVICE_SID={svc.sid} to .env")
    print("Restart the webhook server (or Launch CRM) to pick it up.")


if __name__ == "__main__":
    main()
