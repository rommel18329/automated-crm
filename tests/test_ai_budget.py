import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from crm_engine import ai_budget as budget


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    # Never read or write the real .ai-local ledger or limit.
    monkeypatch.setattr(budget, "LOCAL", tmp_path)
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "usage.jsonl")
    monkeypatch.setattr(budget, "SETTINGS", tmp_path / "settings.json")


REQUEST = {"feature": "text_draft", "system": "You write texts.",
           "messages": [{"role": "user", "content": "Draft a follow-up."}],
           "model": budget.HAIKU, "max_tokens": 200}


class FakeClient:
    def __init__(self):
        self.created = []
        self.messages = SimpleNamespace(count_tokens=self.count_tokens, create=self.create)

    def count_tokens(self, **kwargs):
        return SimpleNamespace(input_tokens=1000)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=50))


def test_no_approval_is_refused_before_any_call():
    client = FakeClient()
    with pytest.raises(budget.AIApprovalError):
        budget.run_request(None, REQUEST, client=client)
    assert client.created == []


def test_approved_call_runs_once_and_is_ledgered_at_real_cost():
    client = FakeClient()
    quote = budget.quote_request(REQUEST, client=client)
    response = budget.run_request(quote, REQUEST, client=client)
    assert budget.response_text(response) == "ok"
    assert len(client.created) == 1
    assert client.created[0]["thinking"] == {"type": "disabled"}  # Haiku 5.5 thinks by default
    rows = [json.loads(line) for line in budget.LEDGER.read_text().splitlines()]
    assert rows[0]["status"] == "success"
    assert rows[0]["cost_usd"] == pytest.approx(budget.usage_cost(budget.HAIKU, 1000, 50))
    assert budget.usage_summary()["today_usd"] == pytest.approx(rows[0]["cost_usd"])


def test_changed_prompt_after_pricing_is_refused():
    client = FakeClient()
    quote = budget.quote_request(REQUEST, client=client)
    changed = {**REQUEST, "messages": [{"role": "user", "content": "Something else."}]}
    with pytest.raises(budget.AIApprovalError):
        budget.run_request(quote, changed, client=client)
    assert client.created == []


def test_expired_quote_is_refused():
    client = FakeClient()
    quote = budget.quote_request(REQUEST, client=client).to_dict()
    quote["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with pytest.raises(budget.AIApprovalError):
        budget.run_request(quote, REQUEST, client=client)


def test_daily_limit_blocks_the_call():
    client = FakeClient()
    budget.set_daily_limit(0)
    quote = budget.quote_request(REQUEST, client=client)
    with pytest.raises(budget.AIBudgetError):
        budget.run_request(quote, REQUEST, client=client)
    assert client.created == []
