from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path("crm.db")


@dataclass(slots=True)
class Lead:
    id: int
    name: str
    phone: str
    status: str
    motivation_score: int
    timeline: str
    timeline_days: int | None
    last_contact_date: str | None
    next_action_date: str | None
    touch_count: int
    last_strategy_used: str | None
    conversation_stage: str
    deal_probability: int


def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('cold','warm','hot','dead','contract')),
                motivation_score INTEGER NOT NULL CHECK (motivation_score BETWEEN 1 AND 10),
                timeline TEXT NOT NULL DEFAULT '',
                timeline_days INTEGER,
                last_contact_date TEXT,
                next_action_date TEXT,
                touch_count INTEGER NOT NULL DEFAULT 0,
                last_strategy_used TEXT,
                conversation_stage TEXT NOT NULL CHECK (
                    conversation_stage IN ('not_contacted','initial_contact','qualifying','negotiating','closing')
                ) DEFAULT 'not_contacted',
                deal_probability INTEGER NOT NULL DEFAULT 0 CHECK (deal_probability BETWEEN 0 AND 100)
            );

            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('text','call','note')),
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
                FOREIGN KEY (lead_id) REFERENCES leads (id)
            );

            CREATE TABLE IF NOT EXISTS performance_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                touch_number INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                response_received INTEGER NOT NULL CHECK (response_received IN (0,1)),
                timestamp TEXT NOT NULL,
                FOREIGN KEY (lead_id) REFERENCES leads (id)
            );
            """
        )


def seed_sample_data(db_path: Path = DB_PATH) -> None:
    today = date.today().isoformat()
    sample_leads = [
        ("Maria Benton", "555-0101", "warm", 7, "Need to move for work", 21, today, today, 3, "follow-up", "qualifying", 62),
        ("James Howard", "555-0102", "cold", 4, "Just exploring", 120, None, today, 1, "value-based", "initial_contact", 24),
        ("Lena Ortiz", "555-0103", "hot", 9, "Wants offer this week", 7, today, today, 5, "call", "negotiating", 82),
        ("David Cole", "555-0104", "warm", 6, "Considering selling in spring", 45, today, today, 2, "follow-up", "initial_contact", 48),
    ]
    with get_conn(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]
        if existing:
            return
        conn.executemany(
            """
            INSERT INTO leads (
                name, phone, status, motivation_score, timeline, timeline_days,
                last_contact_date, next_action_date, touch_count, last_strategy_used,
                conversation_stage, deal_probability
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            sample_leads,
        )


def fetch_leads(db_path: Path = DB_PATH, where: str = "1=1", params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM leads WHERE {where} ORDER BY id", tuple(params)).fetchall()
    return [_to_dict(r) for r in rows]


def fetch_lead(lead_id: int, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    return _to_dict(row) if row else None


def fetch_interactions(lead_id: int, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM interactions WHERE lead_id=? ORDER BY timestamp DESC",
            (lead_id,),
        ).fetchall()
    return [_to_dict(r) for r in rows]


def add_interaction(
    lead_id: int,
    type_: str,
    content: str,
    direction: str,
    db_path: Path = DB_PATH,
    ts: datetime | None = None,
) -> None:
    timestamp = (ts or datetime.utcnow()).isoformat(timespec="seconds")
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
            (lead_id, type_, content, timestamp, direction),
        )


def log_performance(
    lead_id: int,
    touch_number: int,
    message_type: str,
    response_received: bool,
    db_path: Path = DB_PATH,
    ts: datetime | None = None,
) -> None:
    timestamp = (ts or datetime.utcnow()).isoformat(timespec="seconds")
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO performance_logs (lead_id, touch_number, message_type, response_received, timestamp)
            VALUES (?,?,?,?,?)
            """,
            (lead_id, touch_number, message_type, int(response_received), timestamp),
        )


def update_lead(lead_id: int, fields: dict[str, Any], db_path: Path = DB_PATH) -> None:
    if not fields:
        return
    set_clause = ", ".join([f"{k}=?" for k in fields])
    params = list(fields.values()) + [lead_id]
    with get_conn(db_path) as conn:
        conn.execute(f"UPDATE leads SET {set_clause} WHERE id=?", params)


def performance_summary(db_path: Path = DB_PATH) -> dict[str, Any]:
    with get_conn(db_path) as conn:
        by_touch = conn.execute(
            """
            SELECT touch_number, COUNT(*) as sent,
                   SUM(CASE WHEN response_received=1 THEN 1 ELSE 0 END) as replied
            FROM performance_logs
            GROUP BY touch_number
            ORDER BY touch_number
            """
        ).fetchall()
        by_type = conn.execute(
            """
            SELECT message_type, COUNT(*) as sent,
                   AVG(response_received) as response_rate
            FROM performance_logs
            GROUP BY message_type
            ORDER BY response_rate DESC
            """
        ).fetchall()
    return {
        "by_touch": [_to_dict(r) for r in by_touch],
        "by_type": [_to_dict(r) for r in by_type],
    }
