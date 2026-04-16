from __future__ import annotations

from pathlib import Path

from crm_engine import db


def test_import_leads_from_csv_insert_and_update(tmp_path: Path):
    db_path = tmp_path / "import.db"
    csv_path = tmp_path / "leads.csv"

    db.init_db(db_path)
    csv_path.write_text(
        "name,phone,status,motivation,timeline,stage\n"
        "John Carter,5552011001,hot,8,Need to close in 2 weeks,negotiating\n"
        "Sarah Lane,555-222-3333,warm,5,Maybe this quarter,qualifying\n"
    )

    summary = db.import_leads_from_csv(str(csv_path), db_path=db_path)
    assert summary["imported"] == 2
    assert summary["inserted"] == 2
    assert summary["updated"] == 0

    # Re-import same phone with changed status -> should update, not duplicate.
    csv_path.write_text(
        "name,phone,status,motivation,timeline,stage\n"
        "John Carter,555-201-1001,warm,6,Timing changed to 90 days,qualifying\n"
    )
    summary2 = db.import_leads_from_csv(str(csv_path), db_path=db_path)
    assert summary2["inserted"] == 0
    assert summary2["updated"] == 1

    leads = db.fetch_leads(db_path)
    john = next(l for l in leads if l["phone"] == "555-201-1001")
    assert john["status"] == "warm"
    assert john["touch_count"] == 0


def test_import_leads_from_csv_validation_errors(tmp_path: Path):
    db_path = tmp_path / "import_errors.db"
    csv_path = tmp_path / "bad.csv"

    db.init_db(db_path)
    csv_path.write_text(
        "name,phone,status,motivation,timeline,stage\n"
        ",555-000-1111,warm,5,Maybe later,qualifying\n"
        "Jane,123,warm,5,Maybe later,qualifying\n"
        "Ben,555-000-2222,warm,5,Maybe later,unknown_stage\n"
    )

    summary = db.import_leads_from_csv(str(csv_path), db_path=db_path)
    assert summary["imported"] == 0
    assert len(summary["errors"]) == 3
