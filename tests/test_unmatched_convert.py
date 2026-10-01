"""convert_unmatched_to_lead: sweep siblings, extract address, reuse (no dup)."""
from __future__ import annotations

from pathlib import Path

from crm_engine import db


def _seed(dbp):
    db.init_db(dbp)


def test_convert_sweeps_siblings_and_extracts_address(tmp_path: Path):
    dbp = tmp_path / "t.db"
    _seed(dbp)
    db.add_unmatched_message("515-111-2222", "text",
                             "Yes I might sell 123 Main St, IA", dbp)
    db.add_unmatched_message("515-111-2222", "text", "call me after 5", dbp)
    u = db.fetch_unmatched_inbox(dbp)
    assert len(u) == 2

    res = db.convert_unmatched_to_lead(u[0]["id"], "Jane Seller", dbp)
    assert res["messages_added"] == 2            # both siblings pulled in
    assert res["address"] == "123 Main St, IA"   # address extracted
    assert res["reused"] is False
    assert len(db.fetch_interactions(res["lead_id"], dbp)) == 2
    assert db.fetch_unmatched_inbox(dbp) == []    # all cleared from review


def test_convert_reuses_existing_lead_no_duplicate(tmp_path: Path):
    dbp = tmp_path / "t.db"
    _seed(dbp)
    db.add_unmatched_message("312-555-0100", "text", "hello", dbp)
    r1 = db.convert_unmatched_to_lead(db.fetch_unmatched_inbox(dbp)[0]["id"], "Bob", dbp)
    db.add_unmatched_message("312-555-0100", "text", "still interested", dbp)
    r2 = db.convert_unmatched_to_lead(db.fetch_unmatched_inbox(dbp)[0]["id"], "Bob", dbp)
    assert r2["reused"] is True
    assert r2["lead_id"] == r1["lead_id"]
    assert len([l for l in db.fetch_leads(dbp) if l["phone"] == "312-555-0100"]) == 1


def test_extract_address():
    assert db.extract_address("wife of seller( 1516 11th st,IA)") == "1516 11th st,IA"
    assert db.extract_address("303 W MAIN ST, IL needs work") == "303 W MAIN ST, IL"
    assert db.extract_address("no street here, just talk") == ""
