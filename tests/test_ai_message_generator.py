import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from crm_engine import ai_message_generator as ai


def test_contact_gap_handles_timezones_legacy_dates_and_internal_notes():
    now = datetime.now(timezone.utc)
    conversation = [
        {"type": "text", "timestamp": (now - timedelta(days=90)).replace(tzinfo=None).isoformat()},
        {"type": "call", "timestamp": (now - timedelta(days=65)).astimezone(timezone(timedelta(hours=-5))).isoformat()},
        {"type": "note", "timestamp": now.isoformat()},
        {"type": "text", "timestamp": "invalid"},
        {"type": "text"},
    ]
    assert ai._days_since_last_contact(conversation) == 65
    assert ai._days_since_last_contact([]) is None
    assert ai._days_since_last_contact([{"type": "note", "timestamp": now.isoformat()}]) is None
    assert ai._days_since_last_contact([{"timestamp": (now + timedelta(days=1)).isoformat()}]) == 0


def test_current_instructions_and_coaching_survive_followup_pipeline(monkeypatch):
    from crm_engine.followup_engine import generate_followup_message

    captured = {}
    def run_request(approval, request):
        assert approval == "approved-quote"
        captured.update(request)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({
            "message": "Hey Jimmie, it's been a while. Can we talk tomorrow?",
            "coaching": "A brief acknowledgment fits the long gap. You don't need a lengthy apology.",
        }))], usage=SimpleNamespace(input_tokens=20, output_tokens=30))

    monkeypatch.setattr(ai, "run_request", run_request)
    monkeypatch.setattr(ai, "load_email_context", lambda lead: {
        'messages': [], 'status': 'Gmail checked: no matched emails.', 'complete': True})
    request = "lets say we can talk tomorrow and should we say sorry for delay?"
    draft = generate_followup_message(
        lead={"id": 1, "name": "Jimmie", "status": "warm"},
        last_message="Any time", ignored_texts=0, strong_intent=False,
        conversation=[{"type": "text", "direction": "inbound", "content": "Any time",
                       "timestamp": (datetime.now(timezone.utc) - timedelta(days=70)).isoformat()}],
        extra_context=request,
        approval="approved-quote",
    )
    prompt = captured["messages"][0]["content"]
    assert prompt.index(request) > prompt.index("Any time")
    assert "70 days" in prompt
    assert "CURRENT AGENT REQUEST" in prompt
    assert "Never replace" in captured["system"]
    assert "tomorrow" in draft.message
    assert "A brief acknowledgment" in draft.coaching
    assert draft.cost_usd > 0


def test_no_approval_means_no_prompt_and_no_paid_call(monkeypatch):
    from crm_engine.followup_engine import generate_followup_message

    def boom(*args, **kwargs):
        raise AssertionError("must not run without an approved price")
    monkeypatch.setattr(ai, "run_request", boom)
    monkeypatch.setattr(ai, "load_email_context", boom)  # not even the free Gmail lookup
    draft = generate_followup_message(lead={"id": 1, "name": "Jimmie", "status": "warm"},
                                      last_message="", ignored_texts=0, strong_intent=False)
    assert draft.message == ""
    assert any("price approval" in f for f in draft.flags)
    assert ai.generate_interaction_note({}, [], "hi", "inbound") == ""
    assert ai.generate_call_note({}, "transcript") == ""
    assert ai.extract_call_facts({}, "transcript") == {}
    assert "price approval" in ai.generate_coaching_report(["a call"])[0]


def test_messages_coach_survives_rerun_and_clears_on_next_draft():
    """Exercise the actual shared UI function without executing dashboard DB startup."""
    from streamlit.testing.v1 import AppTest

    source = Path("dashboard.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "render_ai_draft_row")
    function = ast.get_source_segment(source, node)
    script = '''
import streamlit as st
from types import SimpleNamespace
db = SimpleNamespace(fetch_interactions=lambda lead_id: [])
def prepare_followup_request(lead, last_message, ignored, strong, conversation, extra_context):
    return {'extra_context': extra_context}
def priced_action(key, label, prepare, run, area=None, **kwargs):
    # Stand-in for the price step: one click approves and runs.
    if (area or st).button(label, key=key):
        return run('approved', prepare())
def generate_followup_message(**kwargs):
    assert kwargs['approval'] == 'approved'
    context = kwargs['prepared']['extra_context']
    st.session_state['received_context'] = context
    return SimpleNamespace(message='Can we talk tomorrow?', cost_usd=.001, flags=[],
        coaching='I suggested tomorrow because you requested it.' if context else '')
''' + function + '''
render_ai_draft_row({'id': 1}, 'staged', 'test_ai')
'''
    app = AppTest.from_string(script).run()
    app.text_input[0].set_value("Talk tomorrow").run()
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["received_context"] == "Talk tomorrow"
    assert app.session_state["staged"] == "Can we talk tomorrow?"
    assert any("because you requested it" in item.value for item in app.markdown)
    app.run()
    assert any("because you requested it" in item.value for item in app.markdown)
    app.text_input[0].set_value("").run()
    app.button[0].click().run()
    assert not app.exception
    assert not any("because you requested it" in item.value for item in app.markdown)


def _dondra_convo():
    now = datetime.now(timezone.utc)
    return [{"type": "text", "direction": "outbound", "timestamp": (now - timedelta(days=2)).isoformat(),
             "content": "Hi Dondra, it's Rommel, the investor who reached out a while back about your Sycamore St property. "
                        "This is my new office number. Wanted to check in and see if your plans for the house have changed?"}]


def test_prompt_lists_recent_texts_and_unanswered_count(monkeypatch):
    monkeypatch.setattr(ai, "load_email_context", lambda lead: {
        'messages': [], 'status': '', 'complete': True})
    request = ai.prepare_message_request({"name": "Dondra"}, _dondra_convo(), "obj", "strat")
    prompt = request["messages"][0]["content"]
    assert "YOUR RECENT TEXTS" in prompt and "new office number" in prompt
    assert "UNANSWERED TEXTS: 1" in prompt
    assert "FOLLOW-UP RULES" in request["system"]


def test_similarity_guard_flags_repeat_but_not_new_angle():
    recent = [m["content"] for m in ai._recent_outbound_texts(_dondra_convo())]
    repeat = ("Hi Dondra, it's Rommel, the investor who reached out about your Sycamore St property. "
              "Wanted to check in and see if anything has changed with your plans for the house.")
    fresh = "Dondra, would a quick cash close with no repairs on your end be worth a 5 minute call this week?"
    assert ai.similar_to_recent_text(repeat, recent)
    assert not ai.similar_to_recent_text(fresh, recent)
    assert not ai.similar_to_recent_text(repeat, [])


def test_guard_fires_through_prepared_request_without_conversation(monkeypatch):
    """The dashboard runs drafts from a prepared request and passes no conversation."""
    monkeypatch.setattr(ai, "load_email_context", lambda lead: {
        'messages': [], 'status': '', 'complete': True})
    prepared = ai.prepare_message_request({"name": "Dondra"}, _dondra_convo(), "obj", "strat")
    repeat = ("Hi Dondra, it's Rommel, the investor who reached out about your Sycamore St property. "
              "Wanted to check in and see if anything has changed with your plans for the house.")
    monkeypatch.setattr(ai, "run_request", lambda approval, request: SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps({"message": repeat, "coaching": "x"}))],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1)))
    result = ai.generate_message({"name": "Dondra"}, [], "obj", "strat", approval="ok", prepared=prepared)
    assert any("very similar" in f for f in result.flags)


def test_full_thread_and_notes_are_untruncated_and_separate(monkeypatch):
    monkeypatch.setattr(ai, "load_email_context", lambda lead: {
        'messages': [], 'status': '', 'complete': True})
    now = datetime.now(timezone.utc)
    long_text = "needs a new roof " + "x" * 600 + " END_OF_TEXT"
    convo = [{"type": "text", "direction": "inbound", "content": long_text,
              "timestamp": (now - timedelta(days=90 - i)).isoformat()} for i in range(40)]
    convo.append({"type": "note", "direction": "outbound", "content": "price near 50k " + "y" * 400 + " END_OF_NOTE",
                  "timestamp": (now - timedelta(days=100)).isoformat()})
    prompt = ai.prepare_message_request({"name": "D"}, convo, "o", "s")["messages"][0]["content"]
    assert prompt.count("END_OF_TEXT") == 40          # no 20-item cap, no 300-char cut
    assert "END_OF_NOTE" in prompt.split("SAVED NOTES ABOUT THIS SELLER")[1]
    assert "END_OF_NOTE" not in prompt.split("SAVED NOTES ABOUT THIS SELLER")[0]


def test_thread_cap_drops_oldest_first_and_keeps_notes():
    now = datetime.now(timezone.utc)
    convo = [{"type": "text", "direction": "inbound", "content": f"msg{i} " + "z" * 1000,
              "timestamp": (now - timedelta(days=200 - i)).isoformat()} for i in range(100)]
    text = ai._build_full_thread_text(convo)
    assert "earlier messages omitted" in text and "msg99" in text and "msg0 " not in text
    assert len(text) < ai.THREAD_CHAR_CAP + 200
