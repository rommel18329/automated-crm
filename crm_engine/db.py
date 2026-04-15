from __future__ import annotations

import sqlite3
import csv
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    now = datetime.utcnow()

    hot_leads = [
        {
            "name": "John Carter",
            "phone": "555-201-1001",
            "status": "hot",
            "motivation": 9,
            "timeline": "Looking to close in 2 weeks",
            "timeline_days": 14,
            "stage": "negotiating",
            "probability": 86,
            "touch_count": 5,
            "last_strategy": "call",
            "situation": "Relocating for a job transfer",
            "notes": "Asked for an offer number and close timeline this month.",
            "interactions": [
                ("text", "Hey John, are you still open to selling your property?", "outbound", 4200),
                ("text", "Yes, what price can you offer if we move quickly?", "inbound", 4160),
                ("call", "Reviewed property condition and timing; he said he needs to sell fast.", "outbound", 2800),
                ("text", "I need to sell fast, can you close this month?", "inbound", 1800),
                ("note", "Situation: relocating. Notes: comparing two buyers, wants certainty.", "outbound", 1200),
            ],
        },
        {
            "name": "Sarah Mitchell",
            "phone": "555-201-1002",
            "status": "hot",
            "motivation": 8,
            "timeline": "Wants this wrapped up in under 30 days",
            "timeline_days": 21,
            "stage": "qualifying",
            "probability": 80,
            "touch_count": 4,
            "last_strategy": "follow-up",
            "situation": "Inherited a property she does not want to manage",
            "notes": "Asked if we can close this month and what range we can offer.",
            "interactions": [
                ("text", "Checking in Sarah—still considering a sale this month?", "outbound", 4600),
                ("text", "I might, depends on speed. Can you close this month?", "inbound", 4500),
                ("call", "Walked through inherited-home process and net sheet expectations.", "outbound", 2600),
                ("text", "What price range can you offer me?", "inbound", 900),
            ],
        },
        {
            "name": "Michael Reyes",
            "phone": "555-201-1003",
            "status": "hot",
            "motivation": 7,
            "timeline": "Aiming to sell within 10-14 days",
            "timeline_days": 10,
            "stage": "negotiating",
            "probability": 84,
            "touch_count": 6,
            "last_strategy": "call",
            "situation": "Vacant rental is creating monthly carrying costs",
            "notes": "Wants as-is terms and quick close before next mortgage payment.",
            "interactions": [
                ("text", "Hey Michael, still evaluating options for your rental?", "outbound", 5000),
                ("text", "Yes, I need a clean as-is offer.", "inbound", 4940),
                ("call", "Discussed fast-close options and title timeline.", "outbound", 3200),
                ("text", "Can you close this month if I accept?", "inbound", 2100),
                ("note", "Situation: vacant rental. Notes: urgency tied to payment due date.", "outbound", 1300),
            ],
        },
        {
            "name": "Alicia Gomez",
            "phone": "555-201-1004",
            "status": "hot",
            "motivation": 8,
            "timeline": "Needs a buyer in 3 weeks",
            "timeline_days": 20,
            "stage": "qualifying",
            "probability": 78,
            "touch_count": 4,
            "last_strategy": "call",
            "situation": "Divorce settlement requires property decision soon",
            "notes": "Asked directly about proceeds and certainty of closing date.",
            "interactions": [
                ("text", "Alicia, are you still open to a direct sale option?", "outbound", 4300),
                ("text", "Yes, I need a clear number soon.", "inbound", 4250),
                ("call", "Discussed timeline pressure from legal deadlines.", "outbound", 2400),
                ("text", "If we agree, can you close this month?", "inbound", 700),
            ],
        },
        {
            "name": "David Nguyen",
            "phone": "555-201-1005",
            "status": "hot",
            "motivation": 6,
            "timeline": "Trying to sell in under 30 days",
            "timeline_days": 28,
            "stage": "negotiating",
            "probability": 75,
            "touch_count": 5,
            "last_strategy": "follow-up",
            "situation": "Behind on payments and wants a fast resolution",
            "notes": "Repeated urgency and asked for quickest path to close.",
            "interactions": [
                ("text", "David, are you still looking for a fast sale?", "outbound", 4700),
                ("text", "Yes, I need to move quickly.", "inbound", 4650),
                ("text", "Would a same-month close help solve timing?", "outbound", 3000),
                ("text", "Yes, what can you offer?", "inbound", 2900),
                ("call", "Reviewed as-is process and close timeline.", "outbound", 900),
            ],
        },
    ]

    warm_leads = [
        ("Emily Brooks", "555-202-2001", 6, "Likely decision in 2-3 months", 75, "qualifying", 56, 3, "value-based", "Downsizing after kids moved out", "Engaged but comparing options and timing."),
        ("Daniel Foster", "555-202-2002", 5, "Possibly later this year", 120, "initial_contact", 47, 2, "follow-up", "Needs repairs before listing", "Responds slowly after contractor updates."),
        ("Olivia Price", "555-202-2003", 4, "Maybe after school year", 90, "initial_contact", 43, 3, "casual follow-up", "Family considering relocation", "Interested but timeline still unclear."),
        ("Ryan Bennett", "555-202-2004", 6, "Could move forward in 60-90 days", 80, "qualifying", 58, 4, "value-based", "Household transition in progress", "Asks occasional pricing questions."),
        ("Megan Walsh", "555-202-2005", 5, "After tenant lease ends", 110, "initial_contact", 45, 2, "follow-up", "Managing tenant turnover", "Open to follow-up, no clear commitment."),
        ("Brian Coleman", "555-202-2006", 4, "Unclear, maybe this fall", 100, "initial_contact", 42, 3, "casual follow-up", "Unsure whether to sell or rent", "One reply then delayed response."),
        ("Jasmine Patel", "555-202-2007", 5, "Could revisit in 2 months", 70, "qualifying", 52, 3, "value-based", "Considering move for work", "Wants numbers before deciding."),
        ("Trevor Hall", "555-202-2008", 6, "Maybe by end of quarter", 85, "qualifying", 55, 4, "follow-up", "Planning around business schedule", "Inconsistent but responsive to calls."),
        ("Nina Lawson", "555-202-2009", 4, "Possibly in 3-4 months", 105, "initial_contact", 44, 2, "casual follow-up", "Exploring neighborhood comps", "Curious, but low urgency today."),
        ("Marcus Reed", "555-202-2010", 5, "Could sell in about 90 days", 90, "qualifying", 50, 3, "value-based", "Potential move to another state", "Needs confidence in net proceeds."),
    ]

    cold_leads = [
        ("Laura Jenkins", "555-203-3001", 2, "No plans this year", 365, "initial_contact", 17, 1, "value-based", "Staying put for now", "Asked for rough value then went silent."),
        ("Kevin Simmons", "555-203-3002", 4, "Maybe next year", 210, "initial_contact", 28, 2, "follow-up", "Exploring refinance first", "Vague interest only."),
        ("Natalie Reed", "555-203-3003", 3, "No clear timeline", 240, "initial_contact", 19, 1, "casual follow-up", "Family plans undecided", "Hard to reach."),
        ("Brandon Hayes", "555-203-3004", 2, "After major renovations", 300, "initial_contact", 15, 1, "value-based", "Property not market-ready", "No urgency expressed."),
        ("Alyssa Grant", "555-203-3005", 4, "Maybe in 6+ months", 200, "initial_contact", 30, 2, "follow-up", "Waiting for job clarity", "Short replies then silence."),
        ("Tyler Morgan", "555-203-3006", 3, "Unknown until paperwork clears", 270, "initial_contact", 22, 1, "casual follow-up", "Probate process still open", "No near-term decision."),
        ("Rachel Stone", "555-203-3007", 2, "Not this year", 365, "initial_contact", 14, 1, "follow-up", "Prefers keeping as rental", "Did not respond to last outreach."),
        ("Ethan Parker", "555-203-3008", 4, "Maybe next spring", 190, "initial_contact", 29, 2, "value-based", "Watching market conditions", "Interested in comps only."),
        ("Hannah Collins", "555-203-3009", 1, "No intent to sell now", 420, "initial_contact", 11, 1, "casual follow-up", "Information gathering only", "Requested later check-in."),
        ("Noah Bryant", "555-203-3010", 3, "Could revisit in a year", 400, "initial_contact", 18, 1, "value-based", "Holding as long-term rental", "No urgency and minimal engagement."),
        ("Grace Kim", "555-203-3011", 2, "Maybe after retirement", 500, "initial_contact", 12, 1, "casual follow-up", "No immediate life event", "One brief response only."),
        ("Owen Lewis", "555-203-3012", 4, "Possibly in 8-10 months", 280, "initial_contact", 26, 2, "follow-up", "Considering relocation eventually", "Not ready for concrete steps."),
        ("Chloe Sanders", "555-203-3013", 3, "Undecided timeline", 260, "initial_contact", 20, 1, "casual follow-up", "Comparing refinance and sale", "Paused communication."),
        ("Evan Harper", "555-203-3014", 2, "Not before next year", 330, "initial_contact", 16, 1, "value-based", "Focused on other priorities", "Very low response rate."),
        ("Sophie Bennett", "555-203-3015", 4, "Could consider in 6-9 months", 220, "initial_contact", 27, 2, "follow-up", "Might move if job changes", "No urgency signals yet."),
    ]

    def lead_tuple(record: dict) -> tuple:
        timeline = f"{record['timeline']}. Situation: {record['situation']}. Notes: {record['notes']}"
        return (
            record["name"],
            record["phone"],
            record["status"],
            record["motivation"],
            timeline,
            record["timeline_days"],
            today,
            today,
            record["touch_count"],
            record["last_strategy"],
            record["stage"],
            record["probability"],
        )

    def warm_or_cold_record(row: tuple, status: str) -> dict:
        (
            name,
            phone,
            motivation,
            timeline,
            timeline_days,
            stage,
            probability,
            touch_count,
            last_strategy,
            situation,
            notes,
        ) = row
        return {
            "name": name,
            "phone": phone,
            "status": status,
            "motivation": motivation,
            "timeline": timeline,
            "timeline_days": timeline_days,
            "stage": stage,
            "probability": probability,
            "touch_count": touch_count,
            "last_strategy": last_strategy,
            "situation": situation,
            "notes": notes,
        }

    all_records = hot_leads + [warm_or_cold_record(r, "warm") for r in warm_leads] + [warm_or_cold_record(r, "cold") for r in cold_leads]

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
            [lead_tuple(record) for record in all_records],
        )

        leads_by_name = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM leads").fetchall()}

        interactions_data: list[tuple[int, str, str, str, str]] = []

        # Hot interactions: 4-6 each with strong intent language.
        for record in hot_leads:
            for type_, content, direction, minutes_ago in record["interactions"]:
                ts = (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
                interactions_data.append((leads_by_name[record["name"]], type_, content, ts, direction))

        # Warm interactions: 3-5 each with mixed signals.
        for idx, row in enumerate(warm_leads):
            rec = warm_or_cold_record(row, "warm")
            lead_id = leads_by_name[rec["name"]]
            base = 6500 + (idx * 120)
            interactions_data.extend(
                [
                    (lead_id, "text", f"Hi {rec['name'].split()[0]}, are you still considering selling?", (now - timedelta(minutes=base)).isoformat(timespec="seconds"), "outbound"),
                    (lead_id, "text", "I might sell soon, still exploring options.", (now - timedelta(minutes=base - 80)).isoformat(timespec="seconds"), "inbound"),
                    (lead_id, "text", "No rush—want me to share a simple value range?", (now - timedelta(minutes=base - 600)).isoformat(timespec="seconds"), "outbound"),
                ]
            )
            if idx % 2 == 0:
                interactions_data.append((lead_id, "text", "Maybe. I need a little more time to decide.", (now - timedelta(minutes=base - 760)).isoformat(timespec="seconds"), "inbound"))

        # Cold interactions: 1-3 each with weak engagement/ghosting patterns.
        for idx, row in enumerate(cold_leads):
            rec = warm_or_cold_record(row, "cold")
            lead_id = leads_by_name[rec["name"]]
            base = 8200 + (idx * 90)
            interactions_data.append((lead_id, "text", f"Hey {rec['name'].split()[0]}, still open to discussing your property options?", (now - timedelta(minutes=base)).isoformat(timespec="seconds"), "outbound"))
            if idx % 3 != 1:
                interactions_data.append((lead_id, "text", "Just exploring, nothing urgent right now.", (now - timedelta(minutes=base - 70)).isoformat(timespec="seconds"), "inbound"))
            if idx % 4 == 0:
                interactions_data.append((lead_id, "text", "Totally fine—I can check back later in the year.", (now - timedelta(minutes=base - 500)).isoformat(timespec="seconds"), "outbound"))

        conn.executemany(
            "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
            interactions_data,
        )


def _normalize_phone(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) != 10:
        return None
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"


def import_leads_from_csv(file_path: str, db_path: Path = DB_PATH, clear_existing: bool = False) -> dict[str, Any]:
    required_fields = {"name", "phone", "status", "motivation", "timeline", "stage"}
    status_allowed = {"cold", "warm", "hot", "dead", "contract"}
    stage_map = {
        "initial": "initial_contact",
        "initial_contact": "initial_contact",
        "qualifying": "qualifying",
        "negotiating": "negotiating",
        "closing": "closing",
        "not_contacted": "not_contacted",
    }

    rows_to_insert: list[tuple[Any, ...]] = []
    rows_to_update: list[tuple[Any, ...]] = []
    errors: list[str] = []

    with open(file_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        missing_headers = required_fields - headers
        if missing_headers:
            raise ValueError(f"CSV missing required columns: {', '.join(sorted(missing_headers))}")

        parsed_rows: list[dict[str, Any]] = []
        for idx, raw in enumerate(reader, start=2):
            row = {str(k).strip().lower(): (str(v).strip() if v is not None else "") for k, v in raw.items()}
            name = row.get("name", "").strip()
            phone_raw = row.get("phone", "").strip()
            status = row.get("status", "").strip().lower()
            stage_input = row.get("stage", "").strip().lower()
            timeline = row.get("timeline", "").strip()
            motivation_raw = row.get("motivation", "").strip()

            if not name:
                errors.append(f"Row {idx}: missing name")
                continue
            if not phone_raw:
                errors.append(f"Row {idx}: missing phone")
                continue
            phone = _normalize_phone(phone_raw)
            if not phone:
                errors.append(f"Row {idx}: invalid phone format '{phone_raw}'")
                continue
            if status not in status_allowed:
                errors.append(f"Row {idx}: invalid status '{status}'")
                continue
            if not timeline:
                errors.append(f"Row {idx}: missing timeline")
                continue
            if stage_input not in stage_map:
                errors.append(f"Row {idx}: invalid stage '{stage_input}'")
                continue

            try:
                motivation = int(motivation_raw)
            except ValueError:
                errors.append(f"Row {idx}: invalid motivation '{motivation_raw}'")
                continue
            if not 1 <= motivation <= 10:
                errors.append(f"Row {idx}: motivation must be 1-10")
                continue

            timeline_days_raw = row.get("timeline_days", "").strip()
            timeline_days = int(timeline_days_raw) if timeline_days_raw.isdigit() else None
            deal_probability_raw = row.get("deal_probability", "").strip()
            deal_probability = int(deal_probability_raw) if deal_probability_raw.isdigit() else 0
            deal_probability = max(0, min(100, deal_probability))

            parsed_rows.append(
                {
                    "name": name,
                    "phone": phone,
                    "status": status,
                    "motivation_score": motivation,
                    "timeline": timeline,
                    "timeline_days": timeline_days,
                    "conversation_stage": stage_map[stage_input],
                    "deal_probability": deal_probability,
                }
            )

    with get_conn(db_path) as conn:
        if clear_existing:
            conn.execute("DELETE FROM interactions")
            conn.execute("DELETE FROM performance_logs")
            conn.execute("DELETE FROM leads")

        existing_phones = {
            r["phone"]: r["id"] for r in conn.execute("SELECT id, phone FROM leads").fetchall()
        }

        for row in parsed_rows:
            base_values = (
                row["name"],
                row["phone"],
                row["status"],
                row["motivation_score"],
                row["timeline"],
                row["timeline_days"],
                None,
                date.today().isoformat(),
                0,  # touch_count reset for imports
                None,  # last_strategy_used reset for imports
                row["conversation_stage"],
                row["deal_probability"],
            )
            existing_id = existing_phones.get(row["phone"])
            if existing_id is None:
                rows_to_insert.append(base_values)
            else:
                rows_to_update.append(base_values + (existing_id,))

        if rows_to_insert:
            conn.executemany(
                """
                INSERT INTO leads (
                    name, phone, status, motivation_score, timeline, timeline_days,
                    last_contact_date, next_action_date, touch_count, last_strategy_used,
                    conversation_stage, deal_probability
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows_to_insert,
            )

        if rows_to_update:
            conn.executemany(
                """
                UPDATE leads
                SET name=?, phone=?, status=?, motivation_score=?, timeline=?, timeline_days=?,
                    last_contact_date=?, next_action_date=?, touch_count=?, last_strategy_used=?,
                    conversation_stage=?, deal_probability=?
                WHERE id=?
                """,
                rows_to_update,
            )

    return {
        "imported": len(parsed_rows),
        "inserted": len(rows_to_insert),
        "updated": len(rows_to_update),
        "errors": errors,
    }


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
