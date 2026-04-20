from __future__ import annotations

import json
from pathlib import Path

from . import db, engine


def run_daily(db_path: Path = db.DB_PATH) -> dict:
    db.init_db(db_path)
    return engine.evaluate_all_leads(db_path)


def main() -> None:
    result = run_daily()
    print("=== Daily CRM Decision Run (Approval Mode) ===")
    print(json.dumps(result["summary"], indent=2))
    print("\nFollow-up approvals queued:", len(result["followup_queue"]))
    print("Call list:", len(result["call_list"]))


if __name__ == "__main__":
    main()
