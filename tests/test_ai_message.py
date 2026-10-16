from __future__ import annotations

from types import SimpleNamespace

from crm_engine import ai_message_generator
from crm_engine.followup_engine import generate_followup_message, prepare_followup_request


def test_ai_message(monkeypatch):
    """An approved draft runs the exact request that was priced."""
    monkeypatch.setattr(ai_message_generator, "load_email_context", lambda lead: {
        "messages": [], "status": "Gmail checked: no matched emails.", "complete": True})
    lead = {
        "name": "Sarah Lee",
        "status": "warm",
        "motivation_score": 7,
        "timeline": "30 days",
        "touch_count": 3,
        "last_strategy_used": "casual follow-up",
    }
    prepared = prepare_followup_request(lead, "Wanted to follow up on your plans.", 0, False)
    seen = {}

    def run_request(approval, request):
        seen["approval"], seen["request"] = approval, request
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text='{"message": "Hey Sarah, still thinking about selling this month?"}')],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20))

    monkeypatch.setattr(ai_message_generator, "run_request", run_request)
    draft = generate_followup_message(
        lead=lead,
        last_message="Wanted to follow up on your plans.",
        ignored_texts=0,
        strong_intent=False,
        approval="approved-quote",
        prepared=prepared,
    )
    assert seen["approval"] == "approved-quote"
    assert seen["request"] is prepared
    assert draft.message.startswith("Hey Sarah")  # text block found after a thinking block
    assert draft.cost_usd > 0
