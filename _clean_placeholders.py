"""Remove all placeholder and old approval log interactions from crm.db."""
from crm_engine import db
from pathlib import Path

DB = Path("crm.db")

with db.get_conn(DB) as conn:
    # Count first
    placeholders = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE content LIKE '[SENT PLACEHOLDER]%'"
    ).fetchone()[0]
    approvals = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE content LIKE 'Suggested message approved:%'"
    ).fetchone()[0]

    print(f"[SENT PLACEHOLDER] entries: {placeholders}")
    print(f"Suggested message approved: entries: {approvals}")

    conn.execute("DELETE FROM interactions WHERE content LIKE '[SENT PLACEHOLDER]%'")
    conn.execute("DELETE FROM interactions WHERE content LIKE 'Suggested message approved:%'")

print(f"Deleted {placeholders + approvals} junk interactions.")
