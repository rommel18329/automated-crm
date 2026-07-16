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
#   (completed dialing days below this, hard per-day cap PER NUMBER)
WARMUP_SCHEDULE = [(2, 10), (4, 15), (7, 25), (10, 40)]
WARMUP_DAYS = 10          # real dialing days needed to graduate to full cap
WARMUP_DAILY_CAP = 10     # hard cap on a brand-new number (stage 1)
# Warm-up is POOL-level and counted from the database (dispositions), NOT the
# stats JSON. Rationale: the pool spreads each day's calls across 20 numbers,
# so no single number ever hits a meaningful per-number daily count — a
# per-number warm-up can never advance. What actually warms reputation is the
# operation dialing consistently over days, so a "warm-up day" is any past
# calendar day the pool placed >= MIN_POOL_CALLS_PER_DAY calls (real dialing,
# not a handful of test calls). The per-number caps below still gate volume.
MIN_POOL_CALLS_PER_DAY = 10

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

_warmup_cache: dict = {}   # {date_str: days_completed} — recompute once/day


def pool_warmup_days_completed(db_path: Path = db.DB_PATH) -> int:
    """Count PAST calendar days on which the pool did real dialing (>=
    MIN_POOL_CALLS_PER_DAY calls), read from the dispositions table — the
    authoritative record, immune to caller_id_stats.json resets. This is the
    pool's shared warm-up progress; every number's cap derives from it."""
    today = date.today().isoformat()
    if _warmup_cache.get("_day") == today and "n" in _warmup_cache:
        return _warmup_cache["n"]
    try:
        with db.get_conn(db_path) as conn:
            rows = conn.execute(
                """SELECT date(created_at) d, COUNT(*) c FROM dispositions
                   WHERE from_number IS NOT NULL AND date(created_at) < ?
                   GROUP BY d""",
                (today,),
            ).fetchall()
        n = sum(1 for r in rows if r["c"] >= MIN_POOL_CALLS_PER_DAY)
    except Exception:
        n = 0
    _warmup_cache.clear()
    _warmup_cache.update({"_day": today, "n": n})
    return n


def _warmup_days_completed(phone_number: str = "", stats: dict | None = None,
                           db_path: Path = db.DB_PATH) -> int:
    """Pool-level warm-up days completed (the phone_number arg is ignored now;
    kept so existing callers/tests keep working)."""
    return pool_warmup_days_completed(db_path)


def _stage_cap(days_completed: int) -> int | None:
    """Hard per-day cap for a number at this warm-up stage; None = graduated."""
    for upto, cap in WARMUP_SCHEDULE:
        if days_completed < upto:
            return cap
    return None


def _effective_cap(num: dict, stats: dict | None = None,
                   db_path: Path = db.DB_PATH) -> int:
    """Per-number hard cap for today, from the pool's shared warm-up stage
    (pool_warmup_days_completed). Every number ramps together: 10→15→25→40→50
    as the pool logs more real dialing days."""
    stage = _stage_cap(pool_warmup_days_completed(db_path))
    if stage is None:
        return num["daily_cap"]
    return min(num["daily_cap"], stage)


def _sync_status(num: dict, db_path: Path = db.DB_PATH,
                 stats: dict | None = None) -> None:
    """Keep the status column truthful: 'warming' until the pool's warm-up
    completes, 'active' after."""
    if num["status"] in ("flagged", "retired"):
        return
    graduated = _stage_cap(pool_warmup_days_completed(db_path)) is None
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
    days = pool_warmup_days_completed(db_path)       # pool-shared warm-up stage
    graduated = _stage_cap(days) is None
    for num in pool:
        if num["status"] in ("flagged", "retired"):
            out["flagged"] += 1
            continue
        _sync_status(num, db_path)
        out["numbers"] += 1
        cap = _effective_cap(num, db_path=db_path)
        used = db.caller_calls_today(num["phone_number"], db_path)
        out["cap_today"] += cap
        out["used_today"] += min(used, cap)
        out["remaining_today"] += max(0, cap - used)
        if graduated:
            out["active"] += 1
        else:
            out["warming"] += 1
            out["warming_cap"] = cap
    if not graduated:
        out["warmup_day"] = days + 1                 # humans count from day 1
        out["warmup_days_left"] = max(0, WARMUP_DAYS - days)
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
