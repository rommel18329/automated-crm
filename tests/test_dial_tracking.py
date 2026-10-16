"""Every multi-line dial is recorded, list counts are honest, and list-dry alerts fire."""
import ast
import json
import logging
import threading
from pathlib import Path

import pytest

from crm_engine import db, list_health

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "crm.db"
    db.init_db(p)
    return p


def _webhook_funcs(names, namespace):
    source = ROOT / "webhook_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets))
             or (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") in names)]
    namespace.setdefault("log", logging.getLogger(__name__))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def _make_list(path, n, name="Test list"):
    ids = [db.create_lead(f"Lead {i}", f"515555{i:04d}", path, conversation_stage="not_contacted")
           for i in range(n)]
    list_id = db.create_dial_list(name, db_path=path)
    db.add_list_members(list_id, ids, path)
    return list_id, ids


def test_unanswered_lines_are_recorded_and_block_same_day_redials(path):
    ns = _webhook_funcs({"_AUTO_LINE_OUTCOMES", "_record_line_outcome"},
                        {"db": db, "DB_PATH": path, "_batch_lock": threading.RLock()})
    record = ns["_record_line_outcome"]
    list_id, (a, b, c, d) = _make_list(path, 4)
    batch = {"batch_id": "b1", "agent": "rom", "winner": None, "machines": [],
             "from_numbers": {a: "+15150000001"}}

    record(batch, a, "no-answer")
    record(batch, a, "no-answer")          # late duplicate callback: logged once
    record(batch, b, "failed")
    batch["winner"] = c
    record(batch, c, "completed")          # winner: the agent dispositions it
    record(batch, d, "completed")          # picked up after c won -> cut off
    record(batch, d, "canceled")

    with db.get_conn(path) as conn:
        rows = [(r["lead_id"], r["disposition"], r["source"], r["from_number"])
                for r in conn.execute("SELECT * FROM dispositions ORDER BY id")]
    assert rows == [(a, "No Answer (auto)", "auto", "+15150000001"),
                    (b, "Failed (auto)", "auto", None),
                    (d, db.CUT_OFF_DISPOSITION, "auto", None)]
    today = db.leads_dialed_today(path)
    assert a in today and b in today
    assert d not in today                  # cut-off lines are re-queued, not "dialed"


def test_list_counts_are_honest_and_never_dialed_go_first(path):
    list_id, ids = _make_list(path, 5)
    db.log_disposition(ids[0], "No Answer (auto)", "auto", db_path=path)
    db.log_disposition(ids[0], "Voicemail", "popup", db_path=path)
    db.log_disposition(ids[1], "No Answer (auto)", "auto", db_path=path)
    db.log_disposition(ids[2], "Wrong Number", "popup", db_path=path)
    db.log_disposition(ids[3], db.CUT_OFF_DISPOSITION, "auto", db_path=path)  # not a try
    db.update_lead(ids[1], {"conversation_stage": "initial_contact"}, path)

    summary = db.fetch_dial_lists(path)[0]
    assert summary["contacts"] == 5
    assert summary["dialed"] == 3          # ids 0, 1, 2 (unanswered dials count)
    assert summary["reached"] == 1
    assert summary["remaining"] == 2       # ids 3 and 4 never really dialed
    order = db.fetch_list_lead_ids(list_id, path)
    assert set(order[:2]) == {ids[3], ids[4]}   # never dialed first
    assert order[2] == ids[1]                   # then fewest tries
    assert order[-1] == ids[2]                  # wrong number (finished) last


def test_list_health_low_dry_and_bad(path, monkeypatch):
    monkeypatch.setattr(list_health, "LOW_NEVER_DIALED", 3)
    monkeypatch.setattr(list_health, "BAD_MIN_SAMPLE", 4)
    list_id, ids = _make_list(path, 4, "IA Stacked")
    assert list_health.list_health(list_id, "IA Stacked", path)["alerts"] == []

    db.log_disposition(ids[0], "No Answer (auto)", "auto", db_path=path)
    db.log_disposition(ids[1], "No Answer (auto)", "auto", db_path=path)
    levels = [a["level"] for a in list_health.list_health(list_id, "IA Stacked", path)["alerts"]]
    assert levels == ["low"]               # 2 never-dialed left, under the (patched) 3

    db.log_disposition(ids[2], "Failed (auto)", "auto", db_path=path)
    db.log_disposition(ids[3], "Wrong Number", "popup", db_path=path)
    health = list_health.list_health(list_id, "IA Stacked", path)
    assert [a["level"] for a in health["alerts"]] == ["low", "bad"]   # 2 of 4 failed/wrong
    assert "'IA Stacked' looks bad: 50%" in health["alerts"][1]["message"]

    for lid in ids[:3]:                    # everyone else gets to 3 tries
        for _ in range(3):
            db.log_disposition(lid, "No Answer (auto)", "auto", db_path=path)
    levels = [a["level"] for a in list_health.list_health(list_id, "IA Stacked", path)["alerts"]]
    assert levels[0] == "dry"


def test_cell_alert_goes_out_once_per_list_alert_per_day(path, tmp_path, monkeypatch):
    texts = []
    list_id, ids = _make_list(path, 1, "Tiny list")   # 1 never dialed < 100 -> low
    ns = _webhook_funcs(
        {"_LIST_ALERT_STATE", "_list_health_cache", "_list_health", "_maybe_alert_list"},
        {"db": db, "DB_PATH": path, "json": json, "time": __import__("time"),
         "PROJECT_ROOT": tmp_path, "_alert_cell": lambda key, msg: texts.append(msg),
         "threading": type("T", (), {"Thread": lambda target, args, daemon:
                                     type("R", (), {"start": lambda self: target(*args)})()})})
    q = {"list_id": list_id, "list_name": "Tiny list"}
    ns["_maybe_alert_list"](q)
    ns["_maybe_alert_list"](q)
    assert len(texts) == 1 and "'Tiny list' is running low" in texts[0]
    assert texts[0].startswith("📞 CRM: ")
    ns["_maybe_alert_list"]({"list_id": None})   # one-off import: no list, no alert
    assert len(texts) == 1
