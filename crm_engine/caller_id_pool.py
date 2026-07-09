"""Caller ID pool: number rotation, daily caps, warm-up, and answer-rate
flag detection for the multi-line dialer.

Design follows 2026 carrier best practices:
- Match the lead's area code (local presence) — biggest answer-rate lever.
- Keep each number's daily volume under a cap (~50/day; warming numbers far less).
- Warm new numbers up gradually (low volume for the first WARMUP_DAYS).
- Rotate a number OUT only when its reputation actually degrades, never on a timer.
- Detect likely flags from a crater in answer rate (a $0 proxy that works today,
  before Twilio Voice Integrity's native monitoring ships).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from . import db

# Warming numbers ramp up slowly before carrying full load. Warm-up advances
# on REAL usage, not the calendar: a warm-up day only counts once the number
# actually placed MIN_CALLS_WARMUP_DAY calls that day. Idle days advance
# nothing — a number that never dials never graduates.
#   (completed dialing days below this, hard per-day cap)
WARMUP_SCHEDULE = [(2, 10), (4, 15), (7, 25), (10, 40)]
WARMUP_DAYS = 10          # real dialing days needed to graduate to full cap
WARMUP_DAILY_CAP = 10     # hard cap on a brand-new number (stage 1)
MIN_CALLS_WARMUP_DAY = 5  # a day advances warm-up only at >= this volume

# Answer-rate detection: if a number with enough volume drops below this fraction
# of the pool's median answer rate, treat it as a likely flag.
MIN_CALLS_FOR_RATE = 20          # need this many placed calls before judging
FLAG_RATIO = 0.4                  # < 40% of pool median answer rate => suspect

_STATS_PATH = Path(__file__).resolve().parents[1] / "caller_id_stats.json"

_IL_AREA_CODES = {
    "217","224","309","312","331","618","630","708","773","779","815","847"
}


# ── Stats file (rolling placed/connected per number) ────────────────────────

def _load_stats() -> dict:
    try:
        return json.loads(_STATS_PATH.read_text())
    except Exception:
        return {}


def _save_stats(stats: dict) -> None:
    try:
        _STATS_PATH.write_text(json.dumps(stats, indent=2))
    except Exception:
        pass


def record_placed(phone_number: str) -> None:
    """A call was fired from this number (counts toward answer-rate denominator
    and toward today's warm-up progress)."""
    stats = _load_stats()
    s = stats.setdefault(phone_number, {"placed": 0, "connected": 0})
    s["placed"] += 1
    daily = s.setdefault("daily", {})
    day = date.today().isoformat()
    daily[day] = daily.get(day, 0) + 1
    _save_stats(stats)


def record_connected(phone_number: str) -> None:
    """A call from this number reached a human (answer-rate numerator)."""
    stats = _load_stats()
    s = stats.setdefault(phone_number, {"placed": 0, "connected": 0})
    s["connected"] += 1
    _save_stats(stats)


def answer_rate(phone_number: str) -> float | None:
    stats = _load_stats().get(phone_number)
    if not stats or stats["placed"] < MIN_CALLS_FOR_RATE:
        return None
    return stats["connected"] / stats["placed"]


# ── Rotation ────────────────────────────────────────────────────────────────

def _warmup_days_completed(phone_number: str, stats: dict | None = None) -> int:
    """Count past days (before today) where this number placed enough calls
    (MIN_CALLS_WARMUP_DAY) to advance its warm-up. Idle days don't count."""
    s = (stats if stats is not None else _load_stats()).get(phone_number) or {}
    today = date.today().isoformat()
    return sum(1 for d, c in (s.get("daily") or {}).items()
               if d < today and c >= MIN_CALLS_WARMUP_DAY)


def _stage_cap(days_completed: int) -> int | None:
    """Hard per-day cap for a number at this warm-up stage; None = graduated."""
    for upto, cap in WARMUP_SCHEDULE:
        if days_completed < upto:
            return cap
    return None


def _effective_cap(num: dict, stats: dict | None = None) -> int:
    """Usage-based warm-up cap, computed from REAL dialing history for every
    number — the status column is display-only (synced separately), so a
    number mislabeled 'active' can never dial past its true warm-up stage."""
    stage = _stage_cap(_warmup_days_completed(num["phone_number"], stats))
    if stage is None:
        return num["daily_cap"]
    return min(num["daily_cap"], stage)


def _sync_status(num: dict, db_path: Path = db.DB_PATH,
                 stats: dict | None = None) -> None:
    """Keep the status column truthful: 'warming' until the usage-based ramp
    completes, 'active' after. Replaces the old calendar-based graduation
    (which wrongly promoted numbers that had barely dialed)."""
    if num["status"] in ("flagged", "retired"):
        return
    graduated = _stage_cap(_warmup_days_completed(num["phone_number"], stats)) is None
    want = "active" if graduated else "warming"
    if num["status"] != want:
        db.update_caller_number(num["id"], {"status": want}, db_path)
        num["status"] = want


def _area_of(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits[:3] if len(digits) >= 3 else ""


def pick_number(lead_phone: str, db_path: Path = db.DB_PATH,
                exclude: set | None = None) -> str | None:
    """Choose the best caller ID for a call to lead_phone.

    Priority:
      1. Same-area-code numbers (local presence), then IL/non-IL family match,
         then any usable number.
      2. Among candidates, the one with the most remaining daily headroom
         (spreads load evenly and keeps patterns steady).
    Skips flagged / retired numbers, any number already at its cap, and any
    number in `exclude` (used to give each line of a multi-dial its own number).
    Returns E.164 string, or None if the pool is empty / all capped.
    """
    exclude = exclude or set()
    pool = db.fetch_caller_pool(db_path)
    if not pool:
        return None

    usable = []
    stats = _load_stats()
    for num in pool:
        if num["status"] in ("flagged", "retired"):
            continue
        if num["phone_number"] in exclude:
            continue
        _sync_status(num, db_path, stats)
        used = db.caller_calls_today(num["phone_number"], db_path)
        cap = _effective_cap(num, stats)
        if used >= cap:
            continue
        usable.append((num, cap - used))
    if not usable:
        return None

    lead_area = _area_of(lead_phone)
    lead_is_il = lead_area in _IL_AREA_CODES

    def rank(item):
        num, headroom = item
        exact = num["area_code"] == lead_area
        family = (num["area_code"] in _IL_AREA_CODES) == lead_is_il
        # Higher tuple sorts first: exact area, then IL-family match, then headroom.
        return (exact, family, headroom)

    usable.sort(key=rank, reverse=True)
    return usable[0][0]["phone_number"]


def pool_status(db_path: Path = db.DB_PATH) -> dict:
    """Aggregate pool health for the dialer UI: how much dialing capacity
    remains today and where the numbers are in their (usage-based) warm-up.
    The dialer treats remaining_today as a HARD limit — when it hits 0, cold
    dialing stops for the day rather than overflow onto the texting numbers."""
    pool = db.fetch_caller_pool(db_path)
    out = {"numbers": 0, "active": 0, "warming": 0, "flagged": 0,
           "used_today": 0, "cap_today": 0, "remaining_today": 0,
           "warmup_day": None, "warmup_total_days": WARMUP_DAYS,
           "warming_cap": None, "warmup_days_left": None}
    stats = _load_stats()
    warm_days: list[int] = []
    warm_caps: list[int] = []
    for num in pool:
        if num["status"] in ("flagged", "retired"):
            out["flagged"] += 1
            continue
        _sync_status(num, db_path, stats)
        out["numbers"] += 1
        cap = _effective_cap(num, stats)
        used = db.caller_calls_today(num["phone_number"], db_path)
        out["cap_today"] += cap
        out["used_today"] += min(used, cap)
        out["remaining_today"] += max(0, cap - used)
        days = _warmup_days_completed(num["phone_number"], stats)
        if _stage_cap(days) is None:
            out["active"] += 1
        else:
            out["warming"] += 1
            warm_days.append(days)
            warm_caps.append(cap)
    if warm_days:
        out["warmup_day"] = min(warm_days) + 1          # humans count from day 1
        out["warmup_days_left"] = max(0, WARMUP_DAYS - min(warm_days))
        out["warming_cap"] = min(warm_caps)
    return out


def detect_flagged(db_path: Path = db.DB_PATH) -> list[dict]:
    """Return numbers whose answer rate has cratered vs. the pool median.
    These are likely carrier-flagged and should be pulled from rotation.
    Does NOT mutate state — caller decides whether to auto-flag."""
    pool = db.fetch_caller_pool(db_path)
    rates = []
    for num in pool:
        r = answer_rate(num["phone_number"])
        if r is not None:
            rates.append(r)
    if len(rates) < 2:
        return []
    rates.sort()
    median = rates[len(rates) // 2]
    threshold = median * FLAG_RATIO

    suspects = []
    for num in pool:
        if num["status"] in ("flagged", "retired"):
            continue
        r = answer_rate(num["phone_number"])
        if r is not None and r < threshold:
            suspects.append({
                "phone_number": num["phone_number"],
                "answer_rate": round(r, 3),
                "pool_median": round(median, 3),
                "id": num["id"],
            })
    return suspects
