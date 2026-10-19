from datetime import date, datetime, timedelta

from crm_engine.followup_cadence import followup_reason

TODAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 18, 0)


def ix(days_ago, direction="outbound", type_="text", content="hi", hours=0):
    ts = NOW - timedelta(days=days_ago, hours=hours)
    return {"type": type_, "direction": direction, "content": content, "timestamp": ts.isoformat()}


def reason(status, interactions, next_action_date=None):
    lead = {"status": status, "next_action_date": next_action_date}
    return followup_reason(lead, interactions, today=TODAY, now=NOW)


def test_cadence_hot_daily_warm_weekly_cold_six_months():
    assert reason("hot", [ix(0)]) is None                       # touched today
    assert reason("hot", [ix(1)])["label"].startswith("🔥 Hot · 1 day since")
    assert reason("warm", [ix(6)]) is None
    assert reason("warm", [ix(7)])["label"] == "🌤 Warm · 7 days quiet (weekly)"
    assert reason("cold", [ix(179)]) is None
    r = reason("cold", [ix(200)])
    assert r["label"] == "❄️ Cold · 6 months quiet (every 6 months)" and r["overdue_days"] == 20


def test_waiting_on_you_beats_cadence():
    r = reason("warm", [ix(3), ix(0, "inbound", hours=2)])
    assert r["kind"] == "reply" and r["label"] == "💬 Waiting on you · they texted 2h ago"
    r = reason("cold", [ix(1), ix(0, "inbound", "call", hours=5)])
    assert r["label"] == "💬 Waiting on you · they called 5h ago"   # cold replies show right away
    assert reason("warm", [ix(1, "inbound"), ix(0)]) is None          # we already answered


def test_scheduled_callback_only_counts_if_set_after_last_touch():
    assert reason("warm", [ix(5)], "2026-10-15")["kind"] == "callback"   # booked after the touch
    assert reason("warm", [ix(5)], "2026-10-01") is None                  # stale date, ignored
    assert reason("warm", [ix(5)], "2999-12-31") is None                  # parked dialer import
    assert reason("warm", [ix(5)], "2026-10-20") is None                  # not due yet


def test_cold_redial_dates_stay_with_the_dialer_unless_they_asked_back():
    voicemail = ix(5, type_="call", content="Call - left voicemail")
    assert reason("cold", [voicemail], "2026-10-14") is None              # dialer redial date
    asked = ix(5, type_="call", content="Call - wants callback")
    assert reason("cold", [asked], "2026-10-14")["kind"] == "callback"


def test_hot_always_shows_days_since_you_reached_out():
    r = reason("hot", [ix(150)], "2026-06-01")
    assert r["label"] == "🔥 Hot · 5 months since you reached out (daily)"


def test_never_contacted_and_closed_leads():
    assert reason("cold", []) is None                       # dial lists handle these
    assert reason("warm", [])["label"] == "👋 Never contacted yet"
    for status in ("dead", "contract", "investor"):
        assert reason(status, [ix(400)]) is None
    failed = ix(10, content="[SEND FAILED] hi | Error: x")
    assert reason("warm", [failed])["kind"] == "new"         # a failed send isn't a touch


def mark(status, minutes_ago, where="dialer"):
    return {"type": "note", "direction": "outbound", "content": f"Marked {status.upper()} from {where}",
            "timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_just_marked_lead_goes_straight_into_the_queue():
    # marked WARM from the softphone right after a call: not due for 7 days by cadence, but queued now
    call = ix(0, type_="call", hours=1)          # the call happened an hour ago, the mark just now
    r = reason("warm", [call, mark("warm", 3)])
    assert r["kind"] == "marked" and r["label"] == "📌 Marked WARM 3m ago · plan the next touch"
    assert reason("hot", [call, mark("hot", 3)])["label"].startswith("🔥 Hot · 📌 Marked HOT")
    assert reason("cold", [call, mark("cold", 3)])["kind"] == "marked"     # cold too, not just after 180 days
    # marked from lead edit with no calls or texts on file (cold used to be invisible here)
    assert reason("cold", [mark("cold", 10, "lead edit")])["kind"] == "marked"


def test_marked_lead_leaves_the_queue_after_your_next_touch_and_old_marks_dont_count():
    assert reason("warm", [mark("warm", 120), ix(0, type_="call")]) is None       # called them since the mark
    assert reason("warm", [mark("cold", 3), ix(1)]) is None                        # mark was for a different status
    assert reason("dead", [mark("dead", 3)]) is None


def test_hot_sorts_first_whatever_the_reason():
    hot = reason("hot", [ix(2)])
    reply = reason("warm", [ix(3), ix(0, "inbound")])
    quiet = reason("cold", [ix(300)])
    assert hot["rank"] < reply["rank"] < quiet["rank"]
