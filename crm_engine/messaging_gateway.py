from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from twilio.rest import Client

from . import db

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")        # 515 — Iowa default
_FROM_IL_NUMBER = os.getenv("TWILIO_FROM_IL_NUMBER", "") # 773 — Chicago/IL
_MY_CELL = os.getenv("MY_CELL_NUMBER", "")
_PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Illinois area codes — route to 773 number
_IL_AREA_CODES = {
    "217", "224", "309", "312", "331", "618", "630", "708", "773", "779", "815", "847"
}


def _pick_from_number(to_phone: str) -> str:
    """Return the best FROM number based on the recipient's area code."""
    digits = "".join(c for c in to_phone if c.isdigit())
    area = digits[:3] if len(digits) == 10 else digits[1:4]
    if area in _IL_AREA_CODES and _FROM_IL_NUMBER:
        return _FROM_IL_NUMBER
    return _FROM_NUMBER


def _to_e164(raw: str) -> str:
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


def place_call(lead_id: int, db_path: Path = db.DB_PATH, from_number: str = "") -> dict:
    """Click-to-call: rings MY_CELL_NUMBER first; once answered, Twilio
    dials the lead and bridges the two legs together. Logs an outbound
    call interaction on the lead immediately (call result itself isn't
    tracked here — that comes back through /voice/status if needed).
    from_number: override which Twilio number to call from; defaults to
    area-code routing (IL → 773, everything else → 515)."""
    lead = db.fetch_lead(lead_id, db_path)
    if not lead:
        return {"lead_id": lead_id, "called": False, "error": "Lead not found"}

    if not _MY_CELL or not _PUBLIC_BASE_URL:
        return {"lead_id": lead_id, "called": False, "error": "MY_CELL_NUMBER or PUBLIC_BASE_URL not set in .env"}

    caller_id = from_number or _pick_from_number(lead.get("phone", ""))
    error_msg = None
    try:
        client = Client(_ACCOUNT_SID, _AUTH_TOKEN)
        client.calls.create(
            to=_to_e164(_MY_CELL),
            from_=caller_id,
            url=f"{_PUBLIC_BASE_URL}/voice/connect/{lead_id}",
        )
        called = True
        log_content = f"Outbound call placed (click-to-call) to {lead['name']}"
    except Exception as exc:
        called = False
        error_msg = str(exc)
        log_content = f"[CALL FAILED] click-to-call to {lead['name']} | Error: {exc}"

    db.add_interaction(
        lead_id=lead_id,
        type_="call",
        content=log_content,
        direction="outbound",
        db_path=db_path,
        ts=datetime.utcnow(),
    )
    return {"lead_id": lead_id, "called": called, "error": error_msg}


def send_sms(lead_id: int, message: str, db_path: Path = db.DB_PATH) -> dict:
    """Send an SMS via Twilio and log it as an outbound interaction."""
    clean_message = message.strip()

    lead = db.fetch_lead(lead_id, db_path)
    if not lead:
        return {"lead_id": lead_id, "sent": False, "error": "Lead not found"}

    to_number = lead["phone"]
    # Normalize to E.164 (+1XXXXXXXXXX)
    digits = "".join(c for c in to_number if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    e164 = f"+{digits}"

    from_number = _pick_from_number(to_number)
    error_msg = None
    try:
        client = Client(_ACCOUNT_SID, _AUTH_TOKEN)
        client.messages.create(
            body=clean_message,
            from_=from_number,
            to=e164,
        )
        sent = True
        log_content = clean_message
    except Exception as exc:
        sent = False
        error_msg = str(exc)
        log_content = f"[SEND FAILED] {clean_message} | Error: {exc}"

    db.add_interaction(
        lead_id=lead_id,
        type_="text",
        content=log_content,
        direction="outbound",
        db_path=db_path,
        ts=datetime.utcnow(),
    )
    if sent:
        db.update_lead(
            lead_id,
            {
                "touch_count": lead["touch_count"] + 1,
                "last_contact_date": datetime.utcnow().date().isoformat(),
            },
            db_path,
        )

    return {"lead_id": lead_id, "sent": sent, "message": clean_message, "error": error_msg}
