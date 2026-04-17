from __future__ import annotations

from crm_engine import ai_message_generator
from crm_engine.followup_engine import generate_followup_message


class _FakeResponse:
    output_text = "Hey Sarah, just checking in—are you still thinking about selling this month?"


class _FakeResponsesAPI:
    def create(self, model: str, input: str):
        assert model == "gpt-5-mini"
        assert "Lead context" in input
        return _FakeResponse()


class _FakeClient:
    responses = _FakeResponsesAPI()


def test_ai_message(monkeypatch):
    monkeypatch.setattr(ai_message_generator, "client", _FakeClient())

    lead = {
        "name": "Sarah Lee",
        "status": "warm",
        "motivation_score": 7,
        "timeline": "30 days",
        "touch_count": 3,
        "last_strategy_used": "casual follow-up",
    }
    draft = generate_followup_message(
        lead=lead,
        last_message="Wanted to follow up on your plans.",
        ignored_texts=0,
        strong_intent=False,
    )
    print(draft.message)
    assert isinstance(draft.message, str)
    assert len(draft.message) > 0
