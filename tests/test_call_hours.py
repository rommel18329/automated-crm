"""Calls are only allowed 8am-9pm in the lead's local time."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from crm_engine import call_hours

CT = ZoneInfo("America/Chicago")


def at(h, m=0, tz=CT):
    return datetime(2026, 10, 20, h, m, tzinfo=tz)


def test_iowa_lead_window_edges():
    assert call_hours.can_call_now("5155550100", at(8, 0))[0]
    assert call_hours.can_call_now("5155550100", at(20, 59))[0]
    assert not call_hours.can_call_now("5155550100", at(21, 0))[0]
    assert not call_hours.can_call_now("5155550100", at(7, 59))[0]


def test_uses_the_leads_zone_not_the_callers():
    now = at(20, 30)                                   # 8:30pm Chicago = 9:30pm New York
    assert call_hours.can_call_now("+15155550100", now)[0]       # Central lead
    assert not call_hours.can_call_now("2125550100", now)[0]     # Eastern lead


def test_eastern_lead_closes_an_hour_earlier_than_central():
    assert call_hours.can_call_now("2125550100", at(19, 59))[0]  # 8:59pm ET
    assert not call_hours.can_call_now("2125550100", at(20, 0))[0]  # 9:00pm ET


def test_pacific_lead_not_called_before_8am_local():
    assert not call_hours.can_call_now("3105550100", at(9, 59))[0]  # 7:59am PT
    assert call_hours.can_call_now("3105550100", at(10, 0))[0]


def test_unknown_area_code_uses_safe_overlap():
    assert not call_hours.can_call_now("9995550100", at(9, 59))[0]   # 10:59am ET
    assert call_hours.can_call_now("9995550100", at(10, 0))[0]       # 11:00am ET
    assert call_hours.can_call_now("9995550100", at(19, 59))[0]
    assert not call_hours.can_call_now("9995550100", at(20, 0))[0]    # 9pm ET


def test_requires_timezone_aware_time():
    with pytest.raises(ValueError):
        call_hours.can_call_now("5155550100", datetime(2026, 10, 20, 12, 0))
