from __future__ import annotations

import os
import threading
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


def place_call(lead_id: int, db_path: Path = db.DB_PATH, from_number: str = "",
               to_number: str = "") -> dict:
    """Click-to-call: rings MY_CELL_NUMBER first; once answered, Twilio
    dials the lead and bridges the two legs together. Logs an outbound
    call interaction on the lead immediately (call result itself isn't
    tracked here — that comes back through /voice/status if needed).
    from_number: override which Twilio number to call from; defaults to
    area-code routing (IL → 773, everything else → 515).
    to_number: dial this specific number instead of the lead's primary phone
    (used by the dialer to try a lead's alternate numbers). Defaults to the
    lead's stored phone."""
    lead = db.fetch_lead(lead_id, db_path)
    if not lead:
        return {"lead_id": lead_id, "called": False, "error": "Lead not found"}

    if not _MY_CELL or not _PUBLIC_BASE_URL:
        return {"lead_id": lead_id, "called": False, "error": "MY_CELL_NUMBER or PUBLIC_BASE_URL not set in .env"}

    caller_id = from_number or _pick_from_number(to_number or lead.get("phone", ""))
    error_msg = None
    try:
        client = Client(_ACCOUNT_SID, _AUTH_TOKEN)
        connect_url = f"{_PUBLIC_BASE_URL}/voice/connect/{lead_id}"
        to_digits = "".join(c for c in to_number if c.isdigit())
        if to_digits:
            connect_url += f"?to={to_digits}"
        client.calls.create(
            to=_to_e164(_MY_CELL),
            from_=caller_id,
            url=connect_url,
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


def place_call_number(to_number: str, from_number: str = "") -> dict:
    """Ad-hoc click-to-call to an arbitrary number (not tied to a lead).
    Rings MY_CELL_NUMBER first; once answered, bridges to to_number.
    Used by the Messages quick-dial box."""
    if not _MY_CELL or not _PUBLIC_BASE_URL:
        return {"called": False, "error": "MY_CELL_NUMBER or PUBLIC_BASE_URL not set in .env"}

    digits = "".join(c for c in to_number if c.isdigit())
    if len(digits) not in (10, 11):
        return {"called": False, "error": f"'{to_number}' is not a valid US phone number"}

    caller_id = from_number or _pick_from_number(to_number)
    to_e164 = _to_e164(to_number)
    try:
        client = Client(_ACCOUNT_SID, _AUTH_TOKEN)
        client.calls.create(
            to=_to_e164(_MY_CELL),
            from_=caller_id,
            url=f"{_PUBLIC_BASE_URL}/voice/connect-raw?to={to_e164}",
        )
        return {"called": True, "to": to_e164, "error": None}
    except Exception as exc:
        return {"called": False, "error": str(exc)}


def auto_note_after_text(lead_id: int, db_path: Path = db.DB_PATH) -> None:
    """AI auto-note on the latest text (either direction): what was said,
    what it changes, and the next move — logged as a 🤖 note on the lead.
    Called in a background thread after every inbound/outbound text; any
    failure is swallowed so note-taking can never break texting."""
    try:
        from .ai_message_generator import generate_interaction_note
        lead = db.fetch_lead(lead_id, db_path)
        if not lead:
            return
        ixns = db.fetch_interactions(lead_id, db_path)
        texts = [i for i in ixns if i["type"] == "text"]
        if not texts:
            return
        latest = max(texts, key=lambda x: x["timestamp"])
        note = generate_interaction_note(lead, ixns, latest["content"], latest["direction"])
        if note:
            db.add_interaction(lead_id, "note", "🤖 " + note, "outbound",
                               db_path, ts=datetime.utcnow())
    except Exception:
        pass


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
        # AI auto-note (what we said / what changed / next move) — background
        # thread so the send returns instantly.
        threading.Thread(target=auto_note_after_text, args=(lead_id, db_path),
                         daemon=True).start()

    return {"lead_id": lead_id, "sent": sent, "message": clean_message, "error": error_msg}
