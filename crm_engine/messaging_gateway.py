from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import db


def send_sms(lead_id: int, message: str, db_path: Path = db.DB_PATH) -> dict:
    """Placeholder send interface for future Twilio integration.

    For now, this logs an outbound text interaction only.
    """
    clean_message = message.strip()
    db.add_interaction(
        lead_id=lead_id,
        type_="text",
        content=f"[SENT PLACEHOLDER] {clean_message}",
        direction="outbound",
        db_path=db_path,
        ts=datetime.utcnow(),
    )
    lead = db.fetch_lead(lead_id, db_path)
    if lead:
        db.update_lead(
            lead_id,
            {
                "touch_count": lead["touch_count"] + 1,
                "last_contact_date": datetime.utcnow().date().isoformat(),
            },
            db_path,
        )
    return {"lead_id": lead_id, "sent": True, "message": clean_message}
