"""Appointments set from the softphone, with reminder texts to the user's cell.

Reminders go out 1 day, 1 hour, 30, 10 and 5 minutes before. Replying OK (or
got it, k, 👍, yes…) from the cell stops the rest of that appointment's
reminders; "OK 12" targets appointment #12. If the server was down when a
reminder was due, only the most recent missed one is sent, never a burst.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db

CRM_TZ = ZoneInfo("America/Chicago")
OFFSETS_MIN = (1440, 60, 30, 10, 5)
KINDS = {"call": "call back", "walkthrough": "walkthrough", "meeting": "meeting"}
ACK_WINDOW_HOURS = 24          # a bare "OK" refers to a reminder sent in this window
LATE_GRACE_MIN = 5             # reminders for an appointment that started over 5 min ago are skipped

_ACK_RE = re.compile(
    r"^\s*(?:ok(?:ay)?|k|kk|got\s*it|gotcha|yes|yep|yeah|yup|sure|confirm(?:ed)?|will\s*do|thanks?|ty|"
    r"👍|👌|✅)[\s.!]*(?:#?\s*(\d{1,6}))?[\s.!]*$", re.I)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def local_to_utc(date_str: str, time_str: str) -> str:
    """'2026-10-19', '17:00' in Central time -> naive UTC ISO."""
    local = datetime.fromisoformat(f"{date_str}T{time_str}").replace(tzinfo=CRM_TZ)
    return _utc(local)


def reminder_plan(starts_at_utc: str, now: datetime | None = None) -> list[tuple[int, str, str]]:
    """(offset_min, send_at, status) for every offset; ones already in the past are skipped."""
    now = now or datetime.now(timezone.utc)
    start = _parse_utc(starts_at_utc)
    plan = []
    for off in OFFSETS_MIN:
        send_at = start - timedelta(minutes=off)
        plan.append((off, _utc(send_at), "pending" if send_at > now else "skipped"))
    return plan


def when_label(starts_at_utc: str) -> str:
    """'5:00 PM Mon Oct 19' in Central time."""
    local = _parse_utc(starts_at_utc).astimezone(CRM_TZ)
    return local.strftime("%I:%M %p %a %b %d").lstrip("0").replace(" 0", " ")


def _address(timeline: str | None) -> str:
    tl = timeline or ""
    if "Address:" not in tl:
        return ""
    return tl.split("Address:", 1)[1].split(". Situation")[0].split(". Notes")[0].strip()


def _time_left(start: datetime, now: datetime) -> str:
    mins = max(0, round((start - now).total_seconds() / 60))
    if mins >= 20 * 60:
        return "tomorrow"
    if mins >= 90:
        return f"in {round(mins / 60)} hours"
    if mins >= 55:
        return "in 1 hour"
    return "now" if mins == 0 else f"in {mins} min"


def reminder_text(appt: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    start = _parse_utc(appt["starts_at"])
    parts = [f"📅 Appt {_time_left(start, now)} ({when_label(appt['starts_at'])}): "
             f"{appt['lead_name']} {appt['lead_phone']} · {KINDS.get(appt['kind'], appt['kind'])}"]
    address = _address(appt.get("lead_timeline"))
    if address:
        parts.append(f" · {address}")
    if appt.get("note"):
        parts.append(f" · {appt['note'][:100]}")
    parts.append(f". Reply OK to stop reminders for this one (#{appt['id']}).")
    return "".join(parts)[:320]


def booked_text(appt: dict) -> str:
    return (f"📅 Booked: {appt['lead_name']} {appt['lead_phone']} · {KINDS.get(appt['kind'], appt['kind'])} "
            f"· {when_label(appt['starts_at'])}. Reminder texts 1 day, 1 hr, 30, 10 and 5 min before (#{appt['id']}). "
            "Reply OK to any reminder to stop the rest. Never reply STOP.")[:320]


def parse_ack(text: str) -> tuple[bool, int | None]:
    """(is an OK-style reply, appointment # if they named one)."""
    m = _ACK_RE.match(text or "")
    if not m:
        return False, None
    return True, int(m.group(1)) if m.group(1) else None


def book(lead_id: int, date_str: str, time_str: str, kind: str = "call", note: str = "",
         agent: str | None = None, db_path: Path = db.DB_PATH, now: datetime | None = None) -> dict:
    """Create the appointment + its reminders, log it on the lead and set the
    lead's next follow-up to that day. Returns the stored appointment."""
    if kind not in KINDS:
        raise ValueError("kind must be call, walkthrough or meeting")
    starts_at = local_to_utc(date_str, time_str)
    if _parse_utc(starts_at) <= (now or datetime.now(timezone.utc)):
        raise ValueError("That time has already passed.")
    appt_id = db.create_appointment(lead_id, starts_at, kind, (note or "").strip()[:200], agent,
                                    reminder_plan(starts_at, now), db_path)
    appt = db.fetch_appointment(appt_id, db_path)
    db.add_interaction(lead_id, "note",
                       f"📅 Appointment: {KINDS[kind]} {when_label(starts_at)}"
                       + (f" · {appt['note']}" if appt["note"] else "") + " (reminders on)",
                       "outbound", db_path)
    db.update_lead(lead_id, {"next_action_date": date_str}, db_path)
    return appt


def reschedule(appt_id: int, date_str: str, time_str: str, db_path: Path = db.DB_PATH,
               now: datetime | None = None) -> dict:
    """Move an appointment; all its reminders are rebuilt and turned back on."""
    appt = db.fetch_appointment(appt_id, db_path)
    if not appt:
        raise ValueError("Appointment not found.")
    starts_at = local_to_utc(date_str, time_str)
    if _parse_utc(starts_at) <= (now or datetime.now(timezone.utc)):
        raise ValueError("That time has already passed.")
    db.reschedule_appointment(appt_id, starts_at, reminder_plan(starts_at, now), db_path)
    db.add_interaction(appt["lead_id"], "note",
                       f"📅 Appointment moved to {when_label(starts_at)} (was {when_label(appt['starts_at'])})",
                       "outbound", db_path)
    db.update_lead(appt["lead_id"], {"next_action_date": date_str}, db_path)
    return db.fetch_appointment(appt_id, db_path)


def cancel(appt_id: int, db_path: Path = db.DB_PATH) -> None:
    appt = db.fetch_appointment(appt_id, db_path)
    if not appt:
        return
    db.set_appointment_status(appt_id, "cancelled", db_path)
    db.add_interaction(appt["lead_id"], "note",
                       f"📅 Appointment {when_label(appt['starts_at'])} cancelled", "outbound", db_path)


def send_due_reminders(send_text, db_path: Path = db.DB_PATH, now: datetime | None = None) -> int:
    """Text every reminder that's due. send_text(message) -> bool. Per
    appointment only the latest due reminder goes out (after downtime the
    older ones are skipped). Returns how many were sent."""
    now = now or datetime.now(timezone.utc)
    due: dict[int, list[dict]] = {}
    for row in db.pending_reminders_due(_utc(now), db_path):
        due.setdefault(row["appointment_id"], []).append(row)
    sent = 0
    for appt_id, rows in due.items():
        appt = db.fetch_appointment(appt_id, db_path)
        latest = min(rows, key=lambda r: r["offset_min"])        # closest to the appointment
        for r in rows:
            if r is not latest:
                db.mark_reminder(appt_id, r["offset_min"], "skipped", db_path)
        if not appt or _parse_utc(appt["starts_at"]) < now - timedelta(minutes=LATE_GRACE_MIN):
            db.mark_reminder(appt_id, latest["offset_min"], "skipped", db_path)
            continue
        if send_text(reminder_text(appt, now)):
            db.mark_reminder(appt_id, latest["offset_min"], "sent", db_path)
            sent += 1
        # On a failed send the reminder stays pending and is retried next loop.
    return sent


def acknowledge_reply(text: str, db_path: Path = db.DB_PATH, now: datetime | None = None) -> str | None:
    """Handle a text from the user's cell. Returns the confirmation to text
    back, or None when it isn't an OK-style reply (caller handles it normally)."""
    is_ack, appt_id = parse_ack(text)
    if not is_ack:
        return None
    now = now or datetime.now(timezone.utc)
    if appt_id is None:
        appt_id = db.last_reminded_appointment(_utc(now - timedelta(hours=ACK_WINDOW_HOURS)), db_path)
    appt = db.fetch_appointment(appt_id, db_path) if appt_id else None
    if not appt or appt["status"] != "scheduled":
        return None if appt_id is None else f"No active appointment #{appt_id}; nothing changed."
    db.set_appointment_status(appt["id"], "acknowledged", db_path)
    return f"👍 Reminders off for {appt['lead_name']}, {when_label(appt['starts_at'])} (#{appt['id']})."
