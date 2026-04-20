from __future__ import annotations

from pathlib import Path

from crm_engine import db, engine


def test_daily_evaluation_and_hot_escalation(tmp_path: Path):
    path = tmp_path / "test.db"
    db.init_db(path)
    db.seed_sample_data(path)

    # Make a warm lead show clear intent.
    warm = db.fetch_leads(path, "status='warm'")[0]
    db.add_interaction(warm["id"], "text", "Can you share your price today?", "inbound", path)

    result = engine.evaluate_all_leads(path)
    updated = db.fetch_lead(warm["id"], path)

    assert updated["status"] == "hot"
    assert any(c["lead_id"] == warm["id"] for c in result["call_list"])


def test_ignored_texts_create_ghosting_signal(tmp_path: Path):
    path = tmp_path / "ghost.db"
    db.init_db(path)
    db.seed_sample_data(path)

    lead = db.fetch_leads(path, "status='warm'")[0]
    db.add_interaction(lead["id"], "text", "Ping 1", "outbound", path)
    db.add_interaction(lead["id"], "text", "Ping 2", "outbound", path)
    db.add_interaction(lead["id"], "text", "Ping 3", "outbound", path)

    interactions = db.fetch_interactions(lead["id"], path)
    ev = engine.evaluate_lead(lead, interactions)
    assert ev.ghosting_signal == "3_texts_ignored_suggest_call"
