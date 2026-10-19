"""Alert matching for the supervisor's Twilio-502 funnel watchdog (no network)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_crm  # noqa: E402

BASE = "https://atlas.taild66037.ts.net"
NOW = datetime(2026, 10, 19, 22, 10, 0, tzinfo=timezone.utc)


def alert(age=60, code="11200", text="Got HTTP 502 response to " + BASE + "/voice/browser-connect",
          url=BASE + "/voice/browser-connect"):
    return SimpleNamespace(error_code=code, alert_text=text, request_url=url,
                           date_created=NOW - timedelta(seconds=age))


def test_matches_recent_502_on_webhook():
    a = alert()
    assert run_crm.find_funnel_502([a], BASE, NOW) is a


def test_ignores_other_error_codes_and_statuses():
    assert run_crm.find_funnel_502([alert(code="11205")], BASE, NOW) is None
    assert run_crm.find_funnel_502([alert(text="Got HTTP 500 response")], BASE, NOW) is None


def test_ignores_other_hosts():
    a = alert(url="https://example.com/x", text="Got HTTP 502 response to https://example.com/x")
    assert run_crm.find_funnel_502([a], BASE, NOW) is None


def test_ignores_old_alerts():
    assert run_crm.find_funnel_502([alert(age=900)], BASE, NOW) is None


def test_ignores_alerts_before_last_repair():
    a = alert(age=120)
    since = NOW - timedelta(seconds=60)
    assert run_crm.find_funnel_502([a], BASE, NOW, since) is None
    assert run_crm.find_funnel_502([alert(age=30)], BASE, NOW, since) is not None


def test_returns_newest_and_handles_naive_and_bad_input():
    old, new = alert(age=200), alert(age=20)
    naive = SimpleNamespace(error_code="11200", alert_text="HTTP 502", request_url=BASE + "/x",
                            date_created=(NOW - timedelta(seconds=5)).replace(tzinfo=None))
    assert run_crm.find_funnel_502([old, new], BASE, NOW) is new
    assert run_crm.find_funnel_502([old, naive, object()], BASE, NOW) is naive
    assert run_crm.find_funnel_502([alert()], "", NOW) is None


def test_request_repair_shares_cooldown():
    fw = run_crm.FunnelWatch(cooldown=60)
    assert fw.request_repair(100.0) is True
    assert fw.request_repair(130.0) is False
    assert fw.request_repair(161.0) is True


def test_watch_polls_on_interval_and_swallows_errors():
    w = run_crm.TwilioAlertWatch(BASE, interval=30)
    calls = []

    def fetch(_now):
        calls.append(1)
        return [alert()]

    assert w.check(0.0, NOW, fetch) is not None
    assert w.check(10.0, NOW, fetch) is None      # not due yet
    assert len(calls) == 1
    w.mark_repaired(NOW)
    assert w.check(31.0, NOW + timedelta(seconds=1), fetch) is None  # alert predates repair

    def boom(_now):
        raise RuntimeError("api down")

    assert w.check(100.0, NOW, boom) is None
