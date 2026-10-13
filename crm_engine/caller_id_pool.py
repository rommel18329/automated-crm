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
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from . import db

# Shared progress uses completed Chicago days with >=10 placed calls across
# both agents. Each number can only inherit days since it joined the pool.
WARMUP_SCHEDULE = [(2, 10), (4, 15), (7, 25), (10, 40)]
WARMUP_DAYS = 10
WARMUP_DAILY_CAP = 10
MIN_POOL_CALLS_PER_DAY = 10

# After a long silence, carrier reputation goes stale, so warm-up credit must
# NOT carry across a big gap. If two consecutive real-dialing days are more than
# GAP_RESET_DAYS apart - or the most recent real-dialing day is itself more than
# GAP_RESET_DAYS before today - warm-up restarts from zero. Dialing consistently
# every day is what actually warms numbers; a two-week break wipes the progress.
GAP_RESET_DAYS = 14

# Pool-wide HARD ceiling on total calls placed per day, ramped by how many real
# dialing days the pool has completed (gap-aware; see pool_warmup_days_completed).
# This sits ON TOP of the per-number caps: with ~20 numbers, per-number caps alone
# would allow far more total daily volume than is safe just after a restart, so
# the pool total is the real limiter early in the ramp.
#   (days completed below this  ->  max TOTAL calls allowed that day)
POOL_DAILY_RAMP = [(1, 80), (2, 120), (4, 175), (7, 275), (10, 400)]
POOL_DAILY_CAP_FULL = 500     # fully warmed: steady-state ceiling


def pool_daily_cap(days_completed: int) -> int:
    """Max total calls the whole pool may place today, for this warm-up stage."""
    for upto, cap in POOL_DAILY_RAMP:
        if days_completed < upto:
            return cap
    return POOL_DAILY_CAP_FULL


def _era_day_count(qdays: list, today: date) -> int:
    """Count real-dialing days in the CURRENT warm-up era. A gap of more than
    GAP_RESET_DAYS between consecutive dialing days - or between the last dialing
    day and today - invalidates older progress and restarts the count. `qdays`
    is the list of qualifying days (>= MIN_POOL_CALLS_PER_DAY), oldest first."""
    if not qdays:
        return 0
    # Silence right up to today wipes all prior credit - treat as a fresh start.
    if (today - qdays[-1]).days > GAP_RESET_DAYS:
        return 0
    count = 1
    for i in range(len(qdays) - 1, 0, -1):
        if (qdays[i] - qdays[i - 1]).days > GAP_RESET_DAYS:
            break
        count += 1
    return count


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


_stats_thread_lock = threading.RLock()


@contextmanager
def _stats_lock():
    # Serialize read/modify/write across both threads and server processes.
    with _stats_thread_lock:
        with _STATS_PATH.with_suffix(".lock").open("a+b") as lock:
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)


def _save_stats(stats: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                     dir=_STATS_PATH.parent, delete=False) as out:
        json.dump(stats, out, indent=2)
        tmp = out.name
    try:
        os.replace(tmp, _STATS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record_placed(phone_number: str, call_sid: str, agent: str = "",
                  db_path: Path = db.DB_PATH) -> None:
    """Persist every successful placement once, independent of disposition."""
    if not db.record_dialer_call(call_sid, phone_number, agent, db_path):
        return
    with _stats_lock():
        stats = _load_stats()
        s = stats.setdefault(phone_number, {"placed": 0, "connected": 0})
        s["placed"] += 1
        daily = s.setdefault("daily", {})
        day = db.crm_today().isoformat()
        daily[day] = daily.get(day, 0) + 1
        _save_stats(stats)


def record_connected(phone_number: str) -> None:
    with _stats_lock():
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

def _qualifying_days(db_path: Path) -> list[date]:
    today = db.crm_today()
    return [date.fromisoformat(row["call_day"]) for row in db.dialer_day_counts(db_path)
            if row["calls"] >= MIN_POOL_CALLS_PER_DAY
            and date.fromisoformat(row["call_day"]) < today]


def pool_warmup_days_completed(db_path: Path = db.DB_PATH) -> int:
    """Shared, gap-aware progress; today's calls advance tomorrow's stage.

    Read the durable ledger every time so imports and separate DBs cannot
    leave the process stuck with cached or another database's progress.
    """
    return _era_day_count(_qualifying_days(db_path), db.crm_today())


def _warmup_days_completed(phone_number: str = "", stats: dict | None = None,
                            db_path: Path = db.DB_PATH) -> int:
    days = _qualifying_days(db_path)
    if phone_number:
        num = next((n for n in db.fetch_caller_pool(db_path)
                    if n["phone_number"] == phone_number), None)
        if not num:
            return 0
        started = max(num.get("added_date") or "", num.get("warmup_started") or "")
        days = [day for day in days if day.isoformat() >= started]
    return _era_day_count(days, db.crm_today())


def _stage_cap(days_completed: int) -> int | None:
    """Hard per-day cap for a number at this warm-up stage; None = graduated."""
    for upto, cap in WARMUP_SCHEDULE:
        if days_completed < upto:
            return cap
    return None


def _effective_cap(num: dict, stats: dict | None = None,
                   db_path: Path = db.DB_PATH) -> int:
    """Per-number cap from shared qualifying days since the number joined."""
    stage = _stage_cap(_warmup_days_completed(num["phone_number"], db_path=db_path))
    if stage is None:
        return num["daily_cap"]
    return min(num["daily_cap"], stage)


def _sync_status(num: dict, db_path: Path = db.DB_PATH,
                 stats: dict | None = None) -> None:
    """Keep the status column truthful: 'warming' until the pool's warm-up
    completes, 'active' after."""
    if num["status"] in ("flagged", "retired"):
        return
    graduated = _stage_cap(_warmup_days_completed(num["phone_number"], db_path=db_path)) is None
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

    # HARD pool-wide daily ceiling (gap-aware ramp): once the whole pool has
    # placed its allotment for today, stop - even if individual numbers still
    # have per-number headroom. Right after a restart this is the real limiter.
    days = pool_warmup_days_completed(db_path)
    pool_used = sum(db.caller_calls_today(n["phone_number"], db_path)
                    for n in pool)
    if pool_used >= pool_daily_cap(days):
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
        cap = _effective_cap(num, stats, db_path=db_path)
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
           "pool_cap_today": None,
           "warmup_day": None, "warmup_total_days": WARMUP_DAYS,
           "warming_cap": None, "warming_cap_max": None, "warmup_days_left": None}
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
        out["used_today"] += used
        out["remaining_today"] += max(0, cap - used)
        if _stage_cap(_warmup_days_completed(num["phone_number"], db_path=db_path)) is None:
            out["active"] += 1
        else:
            out["warming"] += 1
            out["warming_cap"] = min(out["warming_cap"] or cap, cap)
            out["warming_cap_max"] = max(out["warming_cap_max"] or cap, cap)
    if not graduated:
        out["warmup_day"] = days + 1                 # humans count from day 1
        out["warmup_days_left"] = max(0, WARMUP_DAYS - days)
    # Clamp to the gap-aware pool-wide daily ceiling on top of per-number caps.
    pool_cap = pool_daily_cap(days)
    true_used = sum(db.caller_calls_today(num["phone_number"], db_path)
                    for num in pool)
    out["used_today"] = true_used
    out["pool_cap_today"] = pool_cap
    out["cap_today"] = min(out["cap_today"], pool_cap)
    out["remaining_today"] = max(0, min(out["remaining_today"],
                                        pool_cap - true_used))
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
