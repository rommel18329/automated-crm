"""Appointments from the softphone + reminder texts to the user's cell (fake sender)."""
import ast
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask, Response, jsonify, request

from crm_engine import appointments as ap, db

# Friday 2026-10-16 1:00 PM Central = 18:00 UTC
NOW = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead = db.create_lead("Trent Holliday", "641-344-7109", path)
    db.update_lead(lead, {"timeline": "Address: 311 S Maple St, Creston, IA. Situation: . Notes: x"}, path)
    return path, lead


def at(minutes_from_now):
    return NOW + timedelta(minutes=minutes_from_now)


def test_booking_logs_it_and_schedules_five_reminders(setup):
    path, lead = setup
    appt = ap.book(lead, "2026-10-19", "17:00", "call", "manufactured home", "rom", path, now=NOW)
    assert appt["starts_at"] == "2026-10-19T22:00:00"          # 5 PM CDT
    assert ap.when_label(appt["starts_at"]) == "5:00 PM Mon Oct 19"
    with db.get_conn(path) as conn:
        offsets = [(r["offset_min"], r["status"]) for r in conn.execute(
            "SELECT * FROM appointment_reminders ORDER BY offset_min DESC")]
    assert offsets == [(1440, "pending"), (60, "pending"), (30, "pending"), (10, "pending"), (5, "pending")]
    notes = [i["content"] for i in db.fetch_interactions(lead, path)]
    assert notes[0] == "📅 Appointment: call back 5:00 PM Mon Oct 19 · manufactured home (reminders on)"
    assert db.fetch_lead(lead, path)["next_action_date"] == "2026-10-19"
    assert "Never reply STOP" in ap.booked_text(appt)
    with pytest.raises(ValueError):
        ap.book(lead, "2026-10-16", "12:00", db_path=path, now=NOW)   # already passed


def test_same_day_booking_skips_reminders_already_past(setup):
    path, lead = setup
    ap.book(lead, "2026-10-16", "13:45", db_path=path, now=NOW)        # 45 min from now
    with db.get_conn(path) as conn:
        plan = {r["offset_min"]: r["status"] for r in conn.execute("SELECT * FROM appointment_reminders")}
    assert plan == {1440: "skipped", 60: "skipped", 30: "pending", 10: "pending", 5: "pending"}


def test_reminders_go_out_on_time_and_only_the_latest_after_downtime(setup):
    path, lead = setup
    appt = ap.book(lead, "2026-10-16", "15:00", db_path=path, now=NOW)  # 2 hours out
    texts = []
    send = lambda msg: texts.append(msg) or True
    assert ap.send_due_reminders(send, path, now=at(30)) == 0           # nothing due yet
    assert ap.send_due_reminders(send, path, now=at(61)) == 1           # 1-hour reminder
    assert texts[0].startswith("📅 Appt in 1 hour (3:00 PM Fri Oct 16): Trent Holliday 641-344-7109")
    assert "311 S Maple St" in texts[0] and texts[0].endswith(f"(#{appt['id']}).")
    # server down from then until 7 min before: the 30 and 10 min ones are missed
    assert ap.send_due_reminders(send, path, now=at(113)) == 1
    assert len(texts) == 2 and "in 7 min" in texts[1]                    # one text, not a burst
    assert ap.send_due_reminders(send, path, now=at(116)) == 1           # 5-minute reminder
    assert ap.send_due_reminders(send, path, now=at(117)) == 0           # nothing left


def test_family_whatsapp_gets_only_the_one_hour_reminder_and_never_blocks_the_cell_text(setup):
    path, lead = setup
    appt = ap.book(lead, "2026-10-16", "15:00", "call", "roof", db_path=path, now=NOW)   # 2 hours out
    wa = []
    send = lambda msg: True
    ap.send_due_reminders(send, path, now=at(61), send_whatsapp=wa.append)               # 1-hour reminder
    assert len(wa) == 1 and wa[0]["1"] == "In 1 hour" and wa[0]["2"] == "Trent Holliday 641-344-7109"
    assert wa[0]["3"].startswith("3:00 PM Fri Oct 16, call back, 311 S Maple St") and wa[0]["3"].endswith("roof")
    ap.send_due_reminders(send, path, now=at(113), send_whatsapp=wa.append)              # 7 min before
    assert len(wa) == 1                                                                   # not the 10/5 min ones

    def boom(_):
        raise RuntimeError("whatsapp down")
    ap.book(lead, "2026-10-16", "16:30", db_path=path, now=NOW)
    texts = []
    assert ap.send_due_reminders(lambda m: texts.append(m) or True, path, now=at(150),
                                 send_whatsapp=boom) == 1                                 # cell text still sent
    assert len(texts) == 1


def test_failed_send_is_retried_and_old_appointments_are_dropped(setup):
    path, lead = setup
    ap.book(lead, "2026-10-16", "13:20", db_path=path, now=NOW)
    assert ap.send_due_reminders(lambda m: False, path, now=at(11)) == 0
    assert ap.send_due_reminders(lambda m: True, path, now=at(11)) == 1  # retried next loop
    assert ap.send_due_reminders(lambda m: True, path, now=at(40)) == 0  # started 20 min ago: skipped


def test_ok_reply_stops_that_appointments_remaining_reminders(setup):
    path, lead = setup
    first = ap.book(lead, "2026-10-16", "15:00", db_path=path, now=NOW)
    second = ap.book(lead, "2026-10-16", "16:00", db_path=path, now=NOW)
    texts = []
    ap.send_due_reminders(lambda m: texts.append(m) or True, path, now=at(61))   # first's 1-hour text
    assert ap.acknowledge_reply("Got it 👍", path, now=at(62)) is None            # not a plain OK reply
    reply = ap.acknowledge_reply("ok", path, now=at(62))
    assert reply == f"👍 Reminders off for Trent Holliday, 3:00 PM Fri Oct 16 (#{first['id']})."
    # first's 30/10/5 min reminders are stopped; second's 1-hour one still goes out
    assert ap.send_due_reminders(lambda m: texts.append(m) or True, path, now=at(121)) == 1
    assert f"#{second['id']}" in texts[-1]                                        # only the other one
    assert ap.acknowledge_reply(f"OK #{second['id']}", path, now=at(122)).startswith("👍")
    assert ap.acknowledge_reply("ok", path, now=at(123)) is None                  # nothing left to stop


@pytest.mark.parametrize("text,expected", [
    ("ok", (True, None)), ("OK!", (True, None)), ("Got it", (True, None)), ("k", (True, None)),
    ("👍", (True, None)), ("yes", (True, None)), ("ok 12", (True, 12)), ("OK #7", (True, 7)),
    ("STOP", (False, None)), ("call him back at 5", (False, None)), ("ok but move it to 6", (False, None)),
])
def test_ok_parsing(text, expected):
    assert ap.parse_ack(text) == expected


def test_reschedule_resets_reminders_and_cancel_stops_them(setup):
    path, lead = setup
    appt = ap.book(lead, "2026-10-16", "15:00", db_path=path, now=NOW)
    ap.send_due_reminders(lambda m: True, path, now=at(61))
    ap.acknowledge_reply("ok", path, now=at(62))
    moved = ap.reschedule(appt["id"], "2026-10-18", "09:30", path, now=at(63))
    assert moved["status"] == "scheduled"
    with db.get_conn(path) as conn:
        assert {r["status"] for r in conn.execute("SELECT status FROM appointment_reminders")} == {"pending"}
    ap.cancel(appt["id"], path)
    assert db.upcoming_appointments("2026-10-16T00:00:00", lead, path) == []
    assert ap.send_due_reminders(lambda m: True, path, now=at(60 * 24)) == 0


def test_booking_route(setup, monkeypatch):
    path, lead = setup
    source = Path(__file__).resolve().parents[1] / "webhook_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in ("dialer_appointment", "dialer_appointments")]
    app = Flask(__name__)
    texts, whatsapps = [], []
    ns = {"app": app, "request": request, "Response": Response, "jsonify": jsonify, "db": db,
          "DB_PATH": path, "_cors": lambda r: r, "log": logging.getLogger(__name__), "datetime": datetime,
          "_text_my_cell": lambda m: texts.append(m) or True,
          "_whatsapp_family": lambda v: whatsapps.append(v) or True,
          "threading": type("T", (), {"Thread": lambda target, args, daemon:
                                      type("R", (), {"start": lambda self: target(*args)})()})}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), ns)
    client = app.test_client()
    future = (datetime.now(ap.CRM_TZ) + timedelta(days=3)).date().isoformat()
    r = client.post("/dialer/appointment", json={"lead_id": lead, "agent": "rom", "date": future,
                                                 "time": "17:00", "kind": "call", "note": "x"})
    assert r.json["ok"] and r.json["label"].startswith("5:00 PM")
    assert texts and texts[0].startswith("📅 Booked: Trent Holliday")
    assert whatsapps[0]["1"] == "Booked" and whatsapps[0]["2"] == "Trent Holliday 641-344-7109"
    listed = client.get(f"/dialer/appointments?lead_id={lead}").json
    assert [a["id"] for a in listed] == [r.json["id"]]
    bad = client.post("/dialer/appointment", json={"lead_id": lead, "date": "nope", "time": "17:00"})
    assert bad.status_code == 400 and not bad.json["ok"]
