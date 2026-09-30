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
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
            "message": "Hey Jimmie, it's been a while. Can we talk tomorrow?",
            "coaching": "A brief acknowledgment fits the long gap. You don't need a lengthy apology.",
        }))], usage=SimpleNamespace(input_tokens=20, output_tokens=30))

    monkeypatch.setattr(ai, "client", SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(ai, "load_email_context", lambda lead: {
        'messages': [], 'status': 'Gmail checked: no matched emails.', 'complete': True})
    request = "lets say we can talk tomorrow and should we say sorry for delay?"
    draft = generate_followup_message(
        lead={"id": 1, "name": "Jimmie", "status": "warm"},
        last_message="Any time", ignored_texts=0, strong_intent=False,
        conversation=[{"type": "text", "direction": "inbound", "content": "Any time",
                       "timestamp": (datetime.now(timezone.utc) - timedelta(days=70)).isoformat()}],
        extra_context=request,
    )
    prompt = captured["messages"][0]["content"]
    assert prompt.index(request) > prompt.index("Any time")
    assert "70 days" in prompt
    assert "CURRENT AGENT REQUEST" in prompt
    assert "Never replace" in captured["system"]
    assert "tomorrow" in draft.message
    assert "A brief acknowledgment" in draft.coaching
    assert draft.cost_usd > 0


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
def generate_followup_message(**kwargs):
    st.session_state['received_context'] = kwargs['extra_context']
    return SimpleNamespace(message='Can we talk tomorrow?', cost_usd=.001,
        coaching='I suggested tomorrow because you requested it.' if kwargs['extra_context'] else '')
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
