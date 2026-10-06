"""Warm-up ramp + gap-aware reset for the caller-ID pool (pure, DB-free)."""
from datetime import date

from crm_engine import caller_id_pool as cp


def test_pool_daily_cap_ramp():
    assert cp.pool_daily_cap(0) == 80      # first day back
    assert cp.pool_daily_cap(1) == 120
    assert cp.pool_daily_cap(2) == 175
    assert cp.pool_daily_cap(3) == 175
    assert cp.pool_daily_cap(4) == 275
    assert cp.pool_daily_cap(6) == 275
    assert cp.pool_daily_cap(7) == 400
    assert cp.pool_daily_cap(9) == 400
    assert cp.pool_daily_cap(10) == 500    # graduated
    assert cp.pool_daily_cap(99) == 500


def test_era_count_empty():
    assert cp._era_day_count([], date(2026, 1, 1)) == 0


def test_era_count_single_recent():
    assert cp._era_day_count([date(2026, 1, 9)], date(2026, 1, 10)) == 1


def test_era_count_consecutive():
    days = [date(2026, 1, 15), date(2026, 1, 16), date(2026, 1, 17)]
    assert cp._era_day_count(days, date(2026, 1, 20)) == 3


def test_era_count_resets_after_internal_gap():
    # Stale Nov cluster, >14d gap, then current Jan era of 2 days.
    days = [date(2025, 11, 1), date(2025, 11, 2),
            date(2026, 1, 18), date(2026, 1, 19)]
    assert cp._era_day_count(days, date(2026, 1, 20)) == 2


def test_era_count_stale_last_day_wipes_all():
    # Mirrors the real situation: dialed Jul/Aug, then ~2 months of silence.
    days = [date(2026, 7, 16), date(2026, 7, 17), date(2026, 8, 4)]
    assert cp._era_day_count(days, date(2026, 10, 6)) == 0


def test_era_count_boundary_exactly_14_days_holds():
    # Exactly GAP_RESET_DAYS apart is still the same era (only > resets).
    days = [date(2026, 1, 1), date(2026, 1, 15)]
    assert cp._era_day_count(days, date(2026, 1, 16)) == 2


def test_era_count_boundary_15_days_resets():
    days = [date(2026, 1, 1), date(2026, 1, 16)]
    assert cp._era_day_count(days, date(2026, 1, 17)) == 1
