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

# Warming numbers ramp up slowly before carrying full load.
WARMUP_DAYS = 7
WARMUP_DAILY_CAP = 15

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
    """A call was fired from this number (counts toward answer-rate denominator)."""
    stats = _load_stats()
    s = stats.setdefault(phone_number, {"placed": 0, "connected": 0})
    s["placed"] += 1
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

def _effective_cap(num: dict) -> int:
    """Warming numbers carry a reduced cap until they graduate."""
    if num["status"] == "warming":
        return min(num["daily_cap"], WARMUP_DAILY_CAP)
    return num["daily_cap"]


def _maybe_graduate(num: dict, db_path: Path = db.DB_PATH) -> None:
    """Promote a warming number to active once it's past WARMUP_DAYS."""
    if num["status"] != "warming" or not num["warmup_started"]:
        return
    try:
        started = date.fromisoformat(num["warmup_started"])
    except ValueError:
        return
    if (date.today() - started).days >= WARMUP_DAYS:
        db.update_caller_number(num["id"], {"status": "active"}, db_path)
        num["status"] = "active"


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
    for num in pool:
        if num["status"] in ("flagged", "retired"):
            continue
        if num["phone_number"] in exclude:
            continue
        _maybe_graduate(num, db_path)
        used = db.caller_calls_today(num["phone_number"], db_path)
        if used >= _effective_cap(num):
            continue
        usable.append((num, _effective_cap(num) - used))
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
