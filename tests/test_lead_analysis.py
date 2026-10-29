import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from crm_engine import lead_analysis as la

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _msg(kind, direction, content, days_ago, minutes=0):
    stamp = NOW - timedelta(days=days_ago) + timedelta(minutes=minutes)
    return {"type": kind, "direction": direction, "content": content, "timestamp": stamp.isoformat()}


def test_metrics_reply_rate_latency_and_initiation():
    convo = [
        _msg("text", "outbound", "hi", 10),
        _msg("text", "inbound", "hey who is this?", 10, minutes=30),   # reply after 30 min
        _msg("text", "outbound", "it's Rom", 9),
        _msg("text", "outbound", "following up", 5),                    # unanswered
        _msg("text", "inbound", "I want to sell asap, how much?", 1),  # seller-initiated (>72h gap)
        _msg("call", "outbound", "Connected, talked (3m 5s)", 2),
        _msg("call", "inbound", "Missed inbound call", 3),
        _msg("note", "outbound", "ignore me", 1),
    ]
    m = la.compute_metrics(convo, now=NOW)
    assert m["texts_sent"] == 3 and m["texts_received"] == 2
    assert m["replied_to"] == 2 and m["reply_rate_pct"] == 67
    assert m["median_reply_minutes"] == (30 + 4 * 24 * 60) / 2   # 30 min and 4 days
    assert m["seller_initiated_texts"] == 1
    assert m["unanswered_outbound_streak"] == 0       # seller replied last
    assert m["days_since_last_inbound"] == 1
    assert m["seller_questions"] == 2
    assert m["calls_total"] == 2 and m["calls_connected"] == 1
    assert m["inbound_calls_from_seller"] == 1
    assert m["total_talk_seconds"] == 185
    assert m["inbound_last_30d"] == 2


def test_metrics_empty_conversation_is_safe():
    m = la.compute_metrics([], now=NOW)
    assert m["texts_sent"] == 0 and m["reply_rate_pct"] is None
    assert m["median_reply_minutes"] is None and m["first_contact_days_ago"] is None


def test_unanswered_streak_counts_trailing_outbound_texts():
    convo = [_msg("text", "inbound", "ok", 9), _msg("text", "outbound", "a", 5), _msg("text", "outbound", "b", 2)]
    assert la.compute_metrics(convo, now=NOW)["unanswered_outbound_streak"] == 2


def test_verdict_is_computed_from_status_ladder_not_model_text():
    assert la.verdict_for("warm", "hot") == "upgrade"
    assert la.verdict_for("hot", "cold") == "downgrade"
    assert la.verdict_for("warm", "warm") == "correct"
    assert la.verdict_for("contract", "hot") == "n/a"


def test_prompt_has_metrics_thread_notes_and_current_status(monkeypatch):
    monkeypatch.setattr(la, "load_email_context", lambda lead: {"messages": [], "status": "Gmail checked.", "complete": True})
    convo = [_msg("text", "inbound", "needs a new roof " + "x" * 600 + " END", 3),
             _msg("note", "outbound", "price near 50k", 3)]
    request = la.prepare_analysis_request({"name": "Dondra", "status": "hot"}, convo)
    prompt = request["messages"][0]["content"]
    assert "Current status label: hot" in prompt and "COMPUTED ENGAGEMENT METRICS" in prompt
    assert prompt.count("END") == 1 and "price near 50k" in prompt.split("SAVED NOTES")[1]
    assert request["feature"] == "lead_analysis"


def _fake_run(payload):
    return lambda approval, request: SimpleNamespace(
        content=[SimpleNamespace(type="text", text=payload if isinstance(payload, str) else json.dumps(payload))],
        usage=SimpleNamespace(input_tokens=50_000, output_tokens=500))


def test_analyze_lead_parses_and_flags_mismatch(monkeypatch):
    monkeypatch.setattr(la, "load_email_context", lambda lead: {"messages": [], "status": "", "complete": True})
    monkeypatch.setattr(la, "run_request", _fake_run({
        "suggested_status": "HOT", "confidence": "high", "summary": "Asked about price twice.",
        "signals": [{"signal": "Price talk", "evidence": "\"how much?\"", "direction": "up"},
                    {"signal": "bad", "evidence": "x", "direction": "sideways"}],
        "risks": ["a", "b", "c", "d"], "next_move": "Call today."}))
    res = la.analyze_lead({"name": "D", "status": "cold"}, [], approval="ok")
    assert res["suggested_status"] == "hot" and res["verdict"] == "upgrade"
    assert res["signals"][1]["direction"] == "neutral" and len(res["risks"]) == 3
    assert res["cost_usd"] > 0


def test_analyze_lead_needs_approval_and_survives_bad_output(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not run without an approved price")
    monkeypatch.setattr(la, "run_request", boom)
    monkeypatch.setattr(la, "load_email_context", boom)
    assert "price approval" in la.analyze_lead({"status": "warm"}, [])["error"]

    monkeypatch.setattr(la, "load_email_context", lambda lead: {"messages": [], "status": "", "complete": True})
    monkeypatch.setattr(la, "run_request", _fake_run("not json at all"))
    assert "failed" in la.analyze_lead({"status": "warm"}, [], approval="ok")["error"]
    monkeypatch.setattr(la, "run_request", _fake_run({"suggested_status": "maybe"}))
    assert "unusable" in la.analyze_lead({"status": "warm"}, [], approval="ok")["error"]
