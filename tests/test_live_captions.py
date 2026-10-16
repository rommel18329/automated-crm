"""Live captions on dialer calls: Twilio real-time transcription → softphone panel,
in-call AI bullets (only with the AI call notes switch on), transcript saved at hang-up."""
import ast
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, Response, jsonify, request

from crm_engine import ai_budget, ai_message_generator as ai, db

ROOT = Path(__file__).resolve().parents[1]
LIVE_NAMES = {"_live_transcription_twiml", "_conference_twiml", "_live_calls", "_live_by_agent",
              "_live_lock", "LIVE_BULLETS_MAX", "LIVE_BULLETS_EVERY", "_LIVE_KEEP_SECONDS",
              "_live_text", "_prune_live_calls", "_live_refresh_bullets", "_maybe_refresh_bullets",
              "_finish_live_call", "_live_has_transcript", "voice_live_transcript",
              "dialer_live_transcript", "voice_recording"}


class _SyncThreading:
    """threading stand-in: real locks, threads run inline so tests are deterministic."""
    Lock = staticmethod(threading.Lock)

    class Thread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@pytest.fixture
def live(tmp_path, monkeypatch):
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead_id = db.create_lead("Paul Seller", "5155550100", path)
    notes_for = []
    ns = {"app": Flask(__name__), "request": request, "jsonify": jsonify, "Response": Response,
          "json": json, "time": time, "threading": _SyncThreading, "db": db, "DB_PATH": path,
          "datetime": datetime, "log": logging.getLogger(__name__), "_cors": _cors,
          "_batch_lock": threading.RLock(), "_multi_batches": {"b1": {"agent": "rom"}},
          "PUBLIC_BASE_URL": "https://crm.example", "VI_SERVICE_SID": "IS1",
          "_call_sid_to_lead": {}, "_start_transcription": lambda *a: notes_for.append(("batch", a)),
          "_auto_call_notes": lambda lid, text: notes_for.append(("ai", lid, text))}
    source = ROOT / "webhook_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in LIVE_NAMES)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in LIVE_NAMES for t in n.targets))
             or (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") in LIVE_NAMES)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), ns)
    switch = {"active": False}
    monkeypatch.setattr(ai_budget, "autonotes_status", lambda: dict(switch))
    monkeypatch.setattr(ai_budget, "quote_request", lambda req, client=None: "dial-session-quote")
    bullet_calls = []

    def fake_run(approval, req):
        bullet_calls.append((approval, req["feature"]))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="• Wants $120k\n• 60 days")],
                               usage=SimpleNamespace(input_tokens=300, output_tokens=20))
    monkeypatch.setattr(ai, "run_request", fake_run)
    return SimpleNamespace(ns=ns, client=ns["app"].test_client(), path=path, lead_id=lead_id,
                           notes_for=notes_for, switch=switch, bullet_calls=bullet_calls)


def _event(live, event, **form):
    return live.client.post(f"/voice/live-transcript/b1/{live.lead_id}",
                            data={"TranscriptionEvent": event, "CallSid": "CA1", **form})


def _say(live, track, text, final=True):
    return _event(live, "transcription-content", Track=track, Final="true" if final else "false",
                  TranscriptionData=json.dumps({"transcript": text}))


def test_connected_call_twiml_starts_live_transcription(live):
    twiml = live.ns["_conference_twiml"]("multi_b1", "+15150000001",
                                         live=live.ns["_live_transcription_twiml"]("b1", 7))
    start = twiml.index("<Start><Transcription")
    assert start < twiml.index("<Dial")                    # captions start before the bridge
    assert 'partialResults="true"' in twiml and 'track="both_tracks"' in twiml
    assert 'statusCallbackUrl="https://crm.example/voice/live-transcript/b1/7"' in twiml


def test_softphone_sees_partials_then_final_lines(live):
    assert _event(live, "transcription-started").status_code == 204
    _say(live, "inbound_track", "yeah I might sell", final=False)
    view = live.client.get("/dialer/live-transcript?agent=rom").json
    assert view["active"] and view["lines"] == []
    assert view["partial"] == [{"who": "Them", "text": "yeah I might sell"}]

    _say(live, "inbound_track", "Yeah, I might sell if the price is right.")
    _say(live, "outbound_track", "What number did you have in mind?")
    view = live.client.get("/dialer/live-transcript?agent=rom").json
    assert view["partial"] == []
    assert view["lines"] == [{"who": "Them", "text": "Yeah, I might sell if the price is right."},
                             {"who": "Rom", "text": "What number did you have in mind?"}]
    assert view["bullets"] == "" and live.bullet_calls == []   # AI switch off: no paid call
    assert live.client.get("/dialer/live-transcript?agent=gabby").json == {"active": False}


def test_live_ai_bullets_only_with_the_switch_and_rate_limited(live):
    live.switch["active"] = True
    _say(live, "inbound_track", "I'd want about 120 thousand.")
    _say(live, "inbound_track", "Need to be out in 60 days.")   # within 30s: no second refresh
    _say(live, "outbound_track", "Got it.")                     # our side never triggers one
    assert live.bullet_calls == [("dial-session-quote", "live_bullets")]
    assert live.client.get("/dialer/live-transcript?agent=rom").json["bullets"].startswith("• Wants $120k")


def test_hang_up_saves_transcript_once_and_runs_ai_notes(live):
    _say(live, "inbound_track", "Call me next week.")
    _say(live, "outbound_track", "Will do.")
    _event(live, "transcription-stopped")
    _event(live, "transcription-stopped")                      # duplicate event: saved once
    notes = [i["content"] for i in db.fetch_interactions(live.lead_id, live.path)]
    assert notes == ["📝 Call transcript:\nThem: Call me next week.\nRom: Will do."]
    assert live.notes_for == [("ai", live.lead_id, "Them: Call me next week.\nRom: Will do.")]
    assert live.client.get("/dialer/live-transcript?agent=rom").json["ended"] is True


def test_recording_skips_after_call_transcript_only_when_live_captions_worked(live, monkeypatch):
    monkeypatch.setattr(db, "update_call_recording", lambda *a, **k: None)
    _say(live, "inbound_track", "Hello?")
    live.ns["_call_sid_to_lead"].update({"CA1": live.lead_id, "CA2": live.lead_id})
    for sid in ("CA1", "CA2"):
        live.client.post("/voice/recording", data={"CallSid": sid, "RecordingSid": "RE" + sid,
                                                   "RecordingUrl": "https://api.twilio.com/rec"})
    assert live.notes_for == [("batch", ("RECA2", live.lead_id))]   # CA1 had live captions
