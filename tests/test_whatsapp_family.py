"""WhatsApp copy of appointment confirmations to the family number (fake Twilio only)."""
import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "webhook_server.py"


def _load(monkeypatch, **cfg):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_whatsapp_family"]
    created = []

    class Messages:
        def create(self, **kw):
            created.append(kw)

    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", lambda *a, **k: SimpleNamespace(messages=Messages()))
    ns = {"json": json, "log": logging.getLogger(__name__), "ACCOUNT_SID": "AC", "AUTH_TOKEN": "t",
          "to_e164": lambda raw: "+" + ("".join(c for c in raw if c.isdigit()) if len(
              "".join(c for c in raw if c.isdigit())) > 10 else "1" + "".join(c for c in raw if c.isdigit())),
          "WIFE_WHATSAPP_NUMBER": "", "WHATSAPP_FROM": "", "WHATSAPP_TEMPLATE_SID": "", **cfg}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns["_whatsapp_family"], created


VARS = {"1": "Booked", "2": "Trent Holliday 641-344-7109", "3": "5:00 PM Mon Oct 19, call back"}


def test_nothing_is_sent_until_a_number_and_sender_are_configured(monkeypatch):
    send, created = _load(monkeypatch, WIFE_WHATSAPP_NUMBER="+18297644394")
    assert send(VARS) is False and created == []


def test_template_message_to_her_whatsapp(monkeypatch):
    send, created = _load(monkeypatch, WIFE_WHATSAPP_NUMBER="8297644394", WHATSAPP_FROM="+15550001111",
                          WHATSAPP_TEMPLATE_SID="HXabc")
    assert send(VARS) is True
    kw = created[0]
    assert kw["to"] == "whatsapp:+18297644394" and kw["from_"] == "whatsapp:+15550001111"
    assert kw["content_sid"] == "HXabc" and json.loads(kw["content_variables"]) == VARS
    assert "body" not in kw


def test_plain_body_without_a_template_for_sandbox_testing(monkeypatch):
    send, created = _load(monkeypatch, WIFE_WHATSAPP_NUMBER="+18297644394", WHATSAPP_FROM="+14155238886")
    assert send(VARS) is True
    assert created[0]["body"] == "📅 Booked: Trent Holliday 641-344-7109 · 5:00 PM Mon Oct 19, call back"
    assert "content_sid" not in created[0]


def test_whatsapp_failure_returns_false_instead_of_raising(monkeypatch):
    send, created = _load(monkeypatch, WIFE_WHATSAPP_NUMBER="+18297644394", WHATSAPP_FROM="+14155238886")
    import twilio.rest

    def explode(*a, **k):
        raise RuntimeError("not opted in")
    monkeypatch.setattr(twilio.rest, "Client", explode)
    assert send(VARS) is False
