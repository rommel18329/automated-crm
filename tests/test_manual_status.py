from datetime import date

import pytest

from crm_engine import db


@pytest.mark.parametrize("status", ["cold", "warm", "hot", "dead", "contract", "investor"])
def test_manual_classification_marks_contacted(tmp_path, status):
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead_id = db.create_lead("Test", "5155550100", path,
                             conversation_stage="not_contacted")
    db.update_lead(lead_id, {"last_contact_date": None}, path)
    db.set_manual_status(lead_id, status, path, contacted_on=date(2026, 10, 12))
    lead = db.fetch_lead(lead_id, path)
    assert lead["status"] == status
    assert lead["conversation_stage"] == "initial_contact"
    assert lead["last_contact_date"] == "2026-10-12"
    assert db.lead_status_bucket(lead) == status
    assert db.fetch_interactions(lead_id, path) == []


def test_status_edit_preserves_stage_dates_and_followup(tmp_path):
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead_id = db.create_lead("Test", "5155550100", path,
                             conversation_stage="negotiating")
    db.update_lead(lead_id, {"last_contact_date": "2026-10-12",
                             "next_action_date": "2026-10-20"}, path)
    db.set_manual_status(lead_id, "warm", path)
    db.set_manual_status(lead_id, "hot", path, contacted_on=date(2026, 10, 1))
    lead = db.fetch_lead(lead_id, path)
    assert lead["conversation_stage"] == "negotiating"
    assert lead["last_contact_date"] == "2026-10-12"
    assert lead["next_action_date"] == "2026-10-20"


@pytest.mark.parametrize("status", ["warm", "hot", "dead", "contract", "investor"])
def test_stale_stage_does_not_hide_classified_lead(status):
    assert db.lead_status_bucket({"status": status, "conversation_stage": "not_contacted"}) == status


def test_raw_cold_lead_stays_uncontacted():
    assert db.lead_status_bucket({"status": "cold", "conversation_stage": "not_contacted"}) == "uncontacted"


def test_dialer_status_route_saves_contact_and_audit(tmp_path):
    # Load only this handler: importing the server starts a live SMS digest thread.
    import ast
    import logging
    from datetime import datetime
    from pathlib import Path
    from flask import Flask, request, Response, jsonify

    source = Path(__file__).resolve().parents[1] / "webhook_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    handler = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "dialer_set_status")
    app = Flask(__name__)
    path = tmp_path / "crm.db"
    db.init_db(path)
    lead_id = db.create_lead("Mark test", "5155550100", path,
                             conversation_stage="not_contacted")
    namespace = dict(app=app, db=db, DB_PATH=path, request=request,
                     Response=Response, jsonify=jsonify, date=date,
                     datetime=datetime, log=logging.getLogger(__name__))
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
    response = app.test_client().post("/dialer/status", json={"lead_id": lead_id, "status": "warm"})
    assert response.status_code == 200
    assert response.json["ok"] is True
    lead = db.fetch_lead(lead_id, path)
    assert lead["status"] == "warm"
    assert lead["conversation_stage"] == "initial_contact"
    assert lead["last_contact_date"] == date.today().isoformat()
    assert db.lead_status_bucket(lead) == "warm"
    assert db.fetch_interactions(lead_id, path)[0]["content"] == "Marked WARM from dialer"
    assert db.count_dispositions(lead_id, path) == 1
