"""Google Voice sending: reply-address store, bounce detection, routing."""
from datetime import datetime, timedelta

import pytest

from crm_engine import gv_gmail_import as gv
from crm_engine.gmail_client import GmailClient


def test_reply_addr_from_header_accepts_issued_address():
    hit = gv.reply_addr_from_header('"kelly (SMS)" <17732178686.13093695663.lz3Zs1gQzF@txt.voice.google.com>')
    assert hit == ("309-369-5663", "17732178686.13093695663.lz3Zs1gQzF@txt.voice.google.com")


def test_reply_addr_from_header_rejects_bare_and_foreign():
    assert gv.reply_addr_from_header("17732178686.15157791438@txt.voice.google.com") is None
    assert gv.reply_addr_from_header("19998887777.13093695663.abc@txt.voice.google.com") is None
    assert gv.reply_addr_from_header("someone@example.com") is None


def test_remember_keeps_newest():
    store = {}
    assert gv.remember_reply_addr(store, "309-369-5663", "a", "2026-10-01T00:00:00")
    assert not gv.remember_reply_addr(store, "309-369-5663", "old", "2026-09-01T00:00:00")
    assert gv.remember_reply_addr(store, "309-369-5663", "new", "2026-10-09T00:00:00")
    assert store["309-369-5663"]["addr"] == "new"


def test_was_bounced_matches_address_and_window():
    t = datetime(2026, 10, 10, 17, 29, 27)
    bounced = {"17732178686.17732261355@txt.voice.google.com": [t + timedelta(seconds=3)]}
    to = "17732178686.17732261355@txt.voice.google.com"
    assert gv._was_bounced(bounced, to, t)
    assert not gv._was_bounced(bounced, to, t - timedelta(days=1))
    assert not gv._was_bounced(bounced, "17732178686.17732261355.tok@txt.voice.google.com", t)


def test_send_gv_reply_refuses_bare_address():
    with pytest.raises(ValueError):
        GmailClient(service=None).send_gv_reply("17732178686.15157791438@txt.voice.google.com", "hi")


def test_send_gv_without_thread_sends_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(gv.db, "fetch_lead", lambda lid, p=None: {"id": lid, "phone": "515-779-1438"})
    monkeypatch.setattr(GmailClient, "connect", classmethod(lambda cls, interactive=False: GmailClient(service=None)))
    monkeypatch.setattr(gv, "refresh_reply_addr", lambda client, phone: None)
    monkeypatch.setattr(GmailClient, "send_gv_reply", lambda self, a, b: calls.append(a))
    monkeypatch.setattr(gv.db, "add_interaction", lambda *a, **k: calls.append("logged"))
    res = gv.send_gv(1773, "hi")
    assert res["sent"] is False and res["no_gv_thread"] is True
    assert calls == []


def test_send_gv_bounce_is_not_logged(monkeypatch):
    logged = []
    monkeypatch.setattr(gv.db, "fetch_lead", lambda lid, p=None: {"id": lid, "phone": "773-226-1355"})
    monkeypatch.setattr(GmailClient, "connect", classmethod(lambda cls, interactive=False: GmailClient(service=None)))
    monkeypatch.setattr(gv, "refresh_reply_addr", lambda c, p: "17732178686.17732261355.tH5WV4NMhP@txt.voice.google.com")
    monkeypatch.setattr(GmailClient, "send_gv_reply", lambda self, a, b: {"id": "x"})
    monkeypatch.setattr(gv, "_wait_for_bounce", lambda *a, **k: True)
    monkeypatch.setattr(gv.db, "add_interaction", lambda *a, **k: logged.append(1))
    res = gv.send_gv(1771, "hi")
    assert res["sent"] is False and res.get("bounced")
    assert logged == []


def test_route_text_non_gv_uses_twilio(monkeypatch):
    import crm_engine.messaging_gateway as mg
    monkeypatch.setattr(gv.db, "fetch_lead", lambda lid, p=None: {"id": lid, "phone": "641-777-3299"})
    monkeypatch.setattr(gv, "load_gv_contacts", lambda: set())
    monkeypatch.setattr(mg, "send_sms", lambda lid, body, db_path=None: {"sent": True, "via": "twilio"})
    assert gv.route_text(34, "hi")["via"] == "twilio"
