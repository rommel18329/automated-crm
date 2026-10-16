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


NOTE_REQUEST = {**REQUEST, "feature": "call_note"}


def test_dial_session_switch_is_the_only_auto_approval():
    client = FakeClient()
    assert budget.auto_approve(NOTE_REQUEST, client=client) is None  # off by default
    budget.set_autonotes(True)
    assert budget.autonotes_status()["active"]
    assert budget.auto_approve(REQUEST, client=client) is None  # only call notes/facts
    quote = budget.auto_approve(NOTE_REQUEST, client=client)
    budget.run_request(quote, NOTE_REQUEST, client=client)
    status = budget.autonotes_status()
    assert status["notes"] == 1
    assert status["cost_usd"] == pytest.approx(budget.usage_cost(budget.HAIKU, 1000, 50))
    budget.set_autonotes(False)
    assert budget.auto_approve(NOTE_REQUEST, client=client) is None
    assert budget.autonotes_status()["notes"] == 1  # last count stays visible


def test_dial_session_switch_expires_at_midnight(monkeypatch):
    budget.set_autonotes(True)
    monkeypatch.setattr(budget, "_today_local", lambda: "2099-01-01")
    assert not budget.autonotes_status()["active"]
    assert budget.auto_approve(NOTE_REQUEST, client=FakeClient()) is None


def test_turning_on_starts_a_fresh_count():
    client = FakeClient()
    budget.set_autonotes(True)
    budget.run_request(budget.auto_approve(NOTE_REQUEST, client=client), NOTE_REQUEST, client=client)
    assert budget.autonotes_status()["notes"] == 1
    budget.set_autonotes(False)
    import time
    time.sleep(1.1)  # ledger timestamps have one-second resolution
    budget.set_autonotes(True)
    assert budget.autonotes_status()["notes"] == 0


def _load_auto_call_notes(namespace):
    import ast
    import logging
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "webhook_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_auto_call_notes")
    namespace.setdefault("log", logging.getLogger(__name__))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_auto_call_notes"]


def test_transcript_gets_ai_notes_only_while_switch_is_on(tmp_path, monkeypatch):
    from crm_engine import db, ai_message_generator as ai
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead_id = db.create_lead("Seller", "5155550100", path)
    calls = []

    def fake_run(approval, request):
        calls.append(request["feature"])
        text = ("Call: Seller picked up.\nSaid: Wants to sell.\nChange: warmer.\nNext: call Friday."
                if request["feature"] == "call_note"
                else '{"motivation_score": 8, "facts": "Roof needs work"}')
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=60))

    monkeypatch.setattr(ai, "run_request", fake_run)
    monkeypatch.setattr(budget, "quote_request", lambda request, client=None: "auto-quote")
    from datetime import datetime
    auto_call_notes = _load_auto_call_notes({"db": db, "DB_PATH": path, "datetime": datetime})

    auto_call_notes(lead_id, "Them: yes I'd sell, the roof needs work")
    assert calls == []  # switch off: no AI at all
    budget.set_autonotes(True)
    auto_call_notes(lead_id, "Them: yes I'd sell, the roof needs work")
    assert calls == ["call_note", "call_facts"]
    notes = [i["content"] for i in db.fetch_interactions(lead_id, path)]
    assert any(n.startswith("🤖 Call: Seller picked up") for n in notes)
    assert any(n.startswith("📊 From the call:") and "Roof needs work" in n for n in notes)
    assert db.fetch_lead(lead_id, path)["motivation_score"] == 8


def test_daily_limit_blocks_the_call():
    client = FakeClient()
    budget.set_daily_limit(0)
    quote = budget.quote_request(REQUEST, client=client)
    with pytest.raises(budget.AIBudgetError):
        budget.run_request(quote, REQUEST, client=client)
    assert client.created == []
