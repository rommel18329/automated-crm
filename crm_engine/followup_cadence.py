"""Who belongs in the Follow-ups tab today, and why.

A lead is due when any of these is true (agreed with the user 2026-10-16):
1. waiting on you: their last call/text came after ours
2. scheduled callback: a next-action date set after our last touch has arrived
3. cadence: hot every day, warm every 7 days, cold every 180 days since our
   last call or text (cold leads we've never contacted belong to dial lists)

Display-only: reads leads and interactions, writes nothing. engine.py's
escalation and next-action logic is untouched.
"""
from __future__ import annotations

from datetime import date, datetime

CADENCE_DAYS = {"hot": 1, "warm": 7, "cold": 180}
PARKED = "2999-01-01"        # dialer imports are parked far in the future

# Lower sorts first: hot, then waiting on you, then callbacks, then quiet leads.
RANK = {"hot": 0, "reply": 1, "callback": 2, "cadence": 3, "new": 3}


def _ts(item: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(item["timestamp"]).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError):
        return None


def _ago(when: datetime, now: datetime) -> str:
    secs = max(0, (now - when).total_seconds())
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    days = int(secs // 86400)
    return "1 day ago" if days == 1 else f"{days} days ago"


def _quiet(days: int) -> str:
    if days >= 60:
        return f"{days // 30} months"
    return "1 day" if days == 1 else f"{days} days"


def followup_reason(lead: dict, interactions: list[dict], today: date | None = None,
                    now: datetime | None = None) -> dict | None:
    """None when the lead isn't due today; otherwise
    {"kind", "rank", "overdue_days", "label"} explaining why it's in the tab."""
    status = lead.get("status")
    if status not in CADENCE_DAYS:
        return None                      # dead / contract / investor never show
    today = today or date.today()
    now = now or datetime.utcnow()       # interaction timestamps are naive UTC
    touches = [i for i in interactions if i.get("type") in ("text", "call") and _ts(i)]
    ours = [_ts(i) for i in touches if i.get("direction") == "outbound"
            and not (i.get("content") or "").startswith("[SEND FAILED]")]
    theirs = [(_ts(i), i) for i in touches if i.get("direction") == "inbound"]
    last_ours = max(ours) if ours else None
    last_theirs, last_theirs_item = max(theirs, key=lambda t: t[0]) if theirs else (None, None)
    hot = status == "hot"
    prefix = "🔥 Hot · " if hot else ""

    if last_theirs and (last_ours is None or last_theirs > last_ours):
        how = "called" if last_theirs_item.get("type") == "call" else "texted"
        return {"kind": "reply", "rank": RANK["hot" if hot else "reply"], "overdue_days": 0,
                "label": f"{prefix}💬 Waiting on you · they {how} {_ago(last_theirs, now)}"}

    # Scheduled follow-up: a date booked after our last touch. Hot leads are due
    # daily anyway. For cold leads only a real callback request counts: the
    # dialer's voicemail / no-answer redial dates belong to the dial list.
    nad = lead.get("next_action_date") or ""
    latest_call = max((i for i in touches if i.get("direction") == "outbound" and i.get("type") == "call"),
                      key=_ts, default=None)
    asked_back = bool(latest_call) and "wants callback" in (latest_call.get("content") or "").lower()
    if (not hot and nad and nad < PARKED and nad <= today.isoformat()
            and last_ours is not None and nad > last_ours.date().isoformat()
            and (status != "cold" or asked_back)):
        overdue = (today - date.fromisoformat(nad[:10])).days
        when = "today" if overdue == 0 else f"since {nad[:10]}"
        return {"kind": "callback", "rank": RANK["hot" if hot else "callback"], "overdue_days": overdue,
                "label": f"{prefix}🗓 Scheduled follow-up due {when}"}

    if last_ours is None:
        if status == "cold":
            return None                  # never contacted: that's the dialer's job
        return {"kind": "new", "rank": RANK["hot" if hot else "new"], "overdue_days": 0,
                "label": f"{prefix}👋 Never contacted yet"}

    days = (today - last_ours.date()).days
    if days < CADENCE_DAYS[status]:
        return None
    label = {"hot": f"🔥 Hot · {_quiet(days)} since you reached out (daily)",
             "warm": f"🌤 Warm · {_quiet(days)} quiet (weekly)",
             "cold": f"❄️ Cold · {_quiet(days)} quiet (every 6 months)"}[status]
    return {"kind": "cadence", "rank": RANK["hot" if hot else "cadence"],
            "overdue_days": days - CADENCE_DAYS[status], "label": label}
