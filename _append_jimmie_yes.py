"""Append final inbound 'Yes' to Jimmie Stanley's text thread."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db

DB_PATH = PROJECT_ROOT / "crm.db"
LEAD_ID = 34

with db.get_conn(DB_PATH) as conn:
    conn.execute(
        "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
        (LEAD_ID, "text", "Yes", "2026-03-26T17:40:00", "inbound"),
    )

print("Added final inbound 'Yes' from Jimmie.")
