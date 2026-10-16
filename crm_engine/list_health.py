"""Is the dial list you're working still worth dialing?

Alerts (thresholds agreed with the user 2026-10-16):
- low: fewer than LOW_NEVER_DIALED members have never been dialed
- dry: every member is finished or has had MAX_TRIES tries → pull a new list
- bad: over BAD_SHARE of the last BAD_WINDOW dial outcomes failed or were wrong numbers
"""
from __future__ import annotations

from pathlib import Path

from . import db

LOW_NEVER_DIALED = 100
MAX_TRIES = 3
BAD_SHARE = 0.40
BAD_WINDOW = 100
BAD_MIN_SAMPLE = 30          # don't call a list bad off a handful of dials
BAD_OUTCOMES = ("Failed (auto)", "Wrong Number")


def list_health(list_id: int, name: str = "", db_path: Path = db.DB_PATH) -> dict:
    members = db.dial_list_attempts(list_id, db_path)
    never = sum(1 for m in members if not m["attempts"] and not m["finished"])
    open_leads = sum(1 for m in members if not m["finished"] and m["attempts"] < MAX_TRIES)
    recent = db.recent_list_outcomes(list_id, BAD_WINDOW, db_path)
    bad_share = (sum(o in BAD_OUTCOMES for o in recent) / len(recent)
                 if len(recent) >= BAD_MIN_SAMPLE else 0.0)
    label = f"'{name}'" if name else "This list"
    alerts = []
    if members and open_leads == 0:
        alerts.append({"level": "dry", "message":
                       f"🛑 {label} is dry: every lead has had {MAX_TRIES}+ tries or is finished. "
                       "Pull a new list."})
    elif members and never < LOW_NEVER_DIALED:
        alerts.append({"level": "low", "message":
                       f"⚠️ {label} is running low: {never} never-dialed leads left "
                       f"({open_leads} still worth another try). Line up a new list."})
    if bad_share > BAD_SHARE:
        alerts.append({"level": "bad", "message":
                       f"⚠️ {label} looks bad: {round(bad_share * 100)}% of the last {len(recent)} "
                       "dials failed or were wrong numbers. Consider pulling a fresh list."})
    return {"list_id": list_id, "name": name, "contacts": len(members), "never_dialed": never,
            "open": open_leads, "bad_share": round(bad_share, 3), "alerts": alerts}
