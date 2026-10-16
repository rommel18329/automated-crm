"""Moving Google Voice sellers onto Twilio, and receiving MMS photos."""
import ast
import re
from pathlib import Path

import pytest

from crm_engine import db, gv_gmail_import as gv, messaging_gateway

ROOT = Path(__file__).resolve().parents[1]
MSG = "MM" + "a" * 32
MEDIA = "ME" + "b" * 32


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead_id = db.create_lead("Kelly Seller", "309-369-5663", path)
    contacts = tmp_path / "gv_contacts.json"
    contacts.write_text('["309-369-5663"]')
    monkeypatch.setattr(gv, "_CONTACTS", contacts)
    monkeypatch.setattr(gv, "_MOVED", tmp_path / "gv_moved.json")
    sent = []
    monkeypatch.setattr(gv, "send_gv", lambda lid, body, p: sent.append(("gv", body)) or {"sent": True})
    monkeypatch.setattr(messaging_gateway, "send_sms",
                        lambda lid, body, p=None: sent.append(("twilio", body)) or {"sent": True})
    return path, lead_id, sent


def test_google_voice_seller_texts_from_gv_until_moved(setup):
    path, lead_id, sent = setup
    gv.route_text(lead_id, "hi", path)
    assert sent == [("gv", "hi")]
    assert gv.is_gv_contact("309-369-5663")

    result = gv.send_move_to_twilio(lead_id, "save this number", path)
    assert result["sent"]
    assert sent[-1] == ("twilio", "save this number")  # the move text itself goes from Twilio
    assert "309-369-5663" in gv.load_moved_to_twilio()
    assert not gv.is_gv_contact("309-369-5663")
    notes = [i["content"] for i in db.fetch_interactions(lead_id, path)]
    assert any(n.startswith("📲 Moved to Twilio") for n in notes)

    gv.route_text(lead_id, "next text", path)
    assert sent[-1] == ("twilio", "next text")


def test_failed_move_text_keeps_them_on_google_voice(setup, monkeypatch):
    path, lead_id, sent = setup
    monkeypatch.setattr(messaging_gateway, "send_sms",
                        lambda lid, body, p=None: {"sent": False, "error": "carrier rejected"})
    assert not gv.send_move_to_twilio(lead_id, "save this number", path)["sent"]
    assert gv.load_moved_to_twilio() == {}
    gv.route_text(lead_id, "hi", path)
    assert sent[-1] == ("gv", "hi")


def test_move_text_template_fills_in():
    text = gv.MOVE_TO_TWILIO_TEXT.format(first="Kelly", sender="Rom")
    assert text.startswith("Hi Kelly, it's Rom with Silverline.")
    assert "Photos" in text


def _load(source_file, names, namespace):
    source = ROOT / source_file
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets))]
    for node in nodes:  # drop route decorators; only the plain functions are tested
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def test_inbound_mms_photos_become_photo_lines():
    from flask import Flask, request
    ns = _load("webhook_server.py", {"_MEDIA_URL_RE", "_inbound_media_lines"}, {"re": re, "request": request})
    form = {"NumMedia": "2",
            "MediaUrl0": f"https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/{MSG}/Media/{MEDIA}",
            "MediaContentType0": "image/jpeg",
            "MediaUrl1": f"https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/{MSG}/Media/ME{'c' * 32}",
            "MediaContentType1": "video/mp4"}
    with Flask(__name__).test_request_context("/twilio/inbound", method="POST", data=form):
        lines = ns["_inbound_media_lines"]()
    assert lines == [f"📷 Photo: {MSG}/{MEDIA}", f"📎 File (video/mp4): {MSG}/ME{'c' * 32}"]


def test_dashboard_shows_photos_and_clean_previews():
    ns = _load("dashboard.py", {"_MEDIA_LINE_RE", "media_preview", "render_media"}, {"re": re})
    stored = f"Here's the kitchen\n📷 Photo: {MSG}/{MEDIA}"
    assert ns["media_preview"](stored) == "Here's the kitchen 📷 Photo"
    html = ns["render_media"](stored)
    assert f"<img src='http://localhost:5001/twilio/media/{MSG}/{MEDIA}'" in html
    assert "Here's the kitchen<br>" in html
    assert ns["render_media"]("plain text, no photo") == "plain text, no photo"
