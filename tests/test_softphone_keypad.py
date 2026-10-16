"""Softphone keypad (press 1 to be connected) and the Attempt #N counter."""
import ast
import json
import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, Response, jsonify, request

from crm_engine import db

SOURCE = Path(__file__).resolve().parents[1] / "webhook_server.py"


def _routes(names, extra):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets))]
    app = Flask(__name__)
    ns = {"app": app, "request": request, "Response": Response, "jsonify": jsonify, "re": re,
          "_cors": lambda r: r, "log": logging.getLogger(__name__),
          "_HANGUP_TWIML": "<Response><Hangup/></Response>", **extra}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return app.test_client(), ns


def test_attempt_number_counts_every_real_dial(tmp_path):
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead = db.create_lead("Seller", "5155550100", path)
    assert db.count_dial_attempts(lead, path) == 0           # softphone shows "Attempt #1"
    db.log_disposition(lead, "No Answer (auto)", "auto", db_path=path)
    db.log_disposition(lead, "Voicemail", "popup", db_path=path)
    db.log_disposition(lead, "(status only)", "popup", db_path=path)
    db.log_disposition(lead, db.CUT_OFF_DISPOSITION, "auto", db_path=path)
    assert db.count_dial_attempts(lead, path) == 2           # next call shows "Attempt #3"


def test_dtmf_twiml_plays_only_valid_keys():
    client, _ = _routes({"_DTMF_RE", "voice_dtmf"}, {})
    ok = client.post("/voice/dtmf/1")
    assert b'<Play digits="1"/>' in ok.data
    assert b'<Play digits="#"/>' in client.post("/voice/dtmf/%23").data
    assert client.post("/voice/dtmf/1;evil").status_code in (400, 404)


def test_keypad_presses_on_the_seller_leg_of_a_connected_batch(monkeypatch):
    updates = []

    class Participant:
        def __init__(self, sid): self.sid = sid
        def update(self, **kw): updates.append((self.sid, kw))

    class Conferences:
        def list(self, **kw): return [SimpleNamespace(sid="CF1")] if kw.get("friendly_name") == "multi_b1" else []
        def __call__(self, sid): return SimpleNamespace(participants=Participant)

    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", lambda *a, **k: SimpleNamespace(conferences=Conferences()))
    batches = {}
    client, _ = _routes({"_DTMF_RE", "dialer_dtmf"}, {
        "_batch_lock": threading.Lock(), "_multi_batches": batches, "ACCOUNT_SID": "AC", "AUTH_TOKEN": "t",
        "PUBLIC_BASE_URL": "https://crm.example"})

    r = client.post("/dialer/dtmf", json={"agent": "rom", "digits": "1"})
    assert r.json == {"ok": False, "error": "no connected multi-line call"}   # browser sends it instead

    batches["b1"] = {"agent": "rom", "status": "connected", "winner": 7, "conference": "multi_b1",
                     "call_sids": {7: "CAseller", 8: "CAother"}}
    assert client.post("/dialer/dtmf", json={"agent": "rom", "digits": "#"}).json == {"ok": True}
    assert updates == [("CAseller", {"announce_url": "https://crm.example/voice/dtmf/%23",
                                     "announce_method": "POST"})]
    assert client.post("/dialer/dtmf", json={"agent": "rom", "digits": "1&x"}).status_code == 400


def test_softphone_script_still_parses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    page = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "softphone_page")
    html = next(ast.literal_eval(n.value) for n in page.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "html" for t in n.targets))
    assert 'id="btnKeypad"' in html and 'id="keypad"' in html
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    script = tmp_path / "softphone.js"
    script.write_text(js, encoding="utf-8")
    result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
