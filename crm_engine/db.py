from __future__ import annotations

import sqlite3
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
    sample_leads = [
        # HOT leads (3): timeline <= 30, motivation >= 6, recent activity.
        (
            "John Carter",
            "(555) 010-1001",
            "hot",
            9,
            "Needs to relocate in 2 weeks. Situation: job transfer. Notes: requested pricing and close-date options.",
            14,
            today,
            today,
            5,
            "call",
            "negotiating",
            86,
        ),
        (
            "Sarah Mitchell",
            "(555) 010-1002",
            "hot",
            8,
            "Wants to sell this month. Situation: inherited property. Notes: asked if we can close fast.",
            21,
            today,
            today,
            4,
            "follow-up",
            "qualifying",
            79,
        ),
        (
            "Michael Reyes",
            "(555) 010-1003",
            "hot",
            7,
            "Trying to avoid another mortgage payment. Situation: vacant rental. Notes: asked for as-is cash offer.",
            10,
            today,
            today,
            6,
            "call",
            "negotiating",
            83,
        ),
        # WARM leads (5): moderate motivation, unclear timeline, inconsistent replies.
        (
            "Emily Brooks",
            "(555) 010-2001",
            "warm",
            6,
            "Might sell in the next 2-3 months. Situation: downsizing. Notes: comparing options.",
            75,
            today,
            today,
            3,
            "value-based",
            "qualifying",
            56,
        ),
        (
            "Daniel Foster",
            "(555) 010-2002",
            "warm",
            5,
            "Could list later this year. Situation: needs repairs first. Notes: delayed responses.",
            120,
            today,
            today,
            2,
            "follow-up",
            "initial_contact",
            47,
        ),
        (
            "Olivia Price",
            "(555) 010-2003",
            "warm",
            4,
            "Exploring timing around school year. Situation: family move possible. Notes: not fully decided.",
            90,
            today,
            today,
            3,
            "casual follow-up",
            "initial_contact",
            43,
        ),
        (
            "Ryan Bennett",
            "(555) 010-2004",
            "warm",
            6,
            "Interested if numbers make sense in 60-90 days. Situation: divorce transition. Notes: asks occasional questions.",
            80,
            today,
            today,
            4,
            "value-based",
            "qualifying",
            58,
        ),
        (
            "Megan Walsh",
            "(555) 010-2005",
            "warm",
            5,
            "Considering sale after lease ends. Situation: tenant turnover. Notes: engaged but slow follow-through.",
            110,
            today,
            today,
            2,
            "follow-up",
            "initial_contact",
            45,
        ),
        # COLD leads (10): low motivation, long/unknown timeline, low engagement.
        (
            "Chris Turner",
            "(555) 010-3001",
            "cold",
            3,
            "Maybe next year. Situation: no immediate pressure. Notes: one reply then silence.",
            210,
            today,
            today,
            1,
            "casual follow-up",
            "initial_contact",
            21,
        ),
        (
            "Laura Jenkins",
            "(555) 010-3002",
            "cold",
            2,
            "Just curious about values. Situation: staying put for now. Notes: low urgency.",
            365,
            today,
            today,
            1,
            "value-based",
            "initial_contact",
            17,
        ),
        (
            "Kevin Simmons",
            "(555) 010-3003",
            "cold",
            4,
            "Could sell eventually. Situation: exploring refinance first. Notes: vague interest.",
            180,
            today,
            today,
            2,
            "follow-up",
            "initial_contact",
            28,
        ),
        (
            "Natalie Reed",
            "(555) 010-3004",
            "cold",
            3,
            "No timeline yet. Situation: undecided family plans. Notes: hard to reach.",
            240,
            today,
            today,
            1,
            "casual follow-up",
            "initial_contact",
            19,
        ),
        (
            "Brandon Hayes",
            "(555) 010-3005",
            "cold",
            2,
            "Maybe after major renovations. Situation: not market-ready. Notes: no urgency.",
            300,
            today,
            today,
            1,
            "value-based",
            "initial_contact",
            15,
        ),
        (
            "Alyssa Grant",
            "(555) 010-3006",
            "cold",
            4,
            "Could revisit in 6+ months. Situation: job stability concerns. Notes: occasional short replies.",
            200,
            today,
            today,
            2,
            "follow-up",
            "initial_contact",
            30,
        ),
        (
            "Tyler Morgan",
            "(555) 010-3007",
            "cold",
            3,
            "Unknown timeline. Situation: inheritance still in probate. Notes: waiting on paperwork.",
            270,
            today,
            today,
            1,
            "casual follow-up",
            "initial_contact",
            22,
        ),
        (
            "Rachel Stone",
            "(555) 010-3008",
            "cold",
            2,
            "Not ready this year. Situation: wants to hold as rental. Notes: no response to latest touch.",
            365,
            today,
            today,
            1,
            "follow-up",
            "initial_contact",
            14,
        ),
        (
            "Ethan Parker",
            "(555) 010-3009",
            "cold",
            4,
            "Possibly next spring. Situation: waiting for market change. Notes: still non-committal.",
            190,
            today,
            today,
            2,
            "value-based",
            "initial_contact",
            29,
        ),
        (
            "Hannah Collins",
            "(555) 010-3010",
            "cold",
            1,
            "No plans to sell now. Situation: gathering information only. Notes: asked to check back later.",
            420,
            today,
            today,
            1,
            "casual follow-up",
            "initial_contact",
            11,
        ),
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
        leads_by_name = {
            row["name"]: row["id"]
            for row in conn.execute("SELECT id, name FROM leads").fetchall()
        }

        def ts(minutes_ago: int) -> str:
            return (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")

        interactions_data = [
            # HOT (4-6 each; strong intent signals)
            (leads_by_name["John Carter"], "text", "Hi John, are you still open to selling your property?", ts(3000), "outbound"),
            (leads_by_name["John Carter"], "text", "Yes, what price can you offer if we move quickly?", ts(2950), "inbound"),
            (leads_by_name["John Carter"], "call", "Discussed timeline and condition; he said he needs to sell fast.", ts(2100), "outbound"),
            (leads_by_name["John Carter"], "text", "I need to sell fast, can you close this month?", ts(1400), "inbound"),
            (leads_by_name["John Carter"], "note", "Situation: job transfer in two weeks. Notes: motivated and comparing cash buyers.", ts(800), "outbound"),
            (leads_by_name["Sarah Mitchell"], "text", "Checking in Sarah—still considering a sale this month?", ts(3200), "outbound"),
            (leads_by_name["Sarah Mitchell"], "text", "I might, depends on speed. Can you close this month?", ts(3100), "inbound"),
            (leads_by_name["Sarah Mitchell"], "call", "Reviewed inherited property details and expected as-is process.", ts(2000), "outbound"),
            (leads_by_name["Sarah Mitchell"], "text", "What price range can you offer me?", ts(1200), "inbound"),
            (leads_by_name["Michael Reyes"], "text", "Hey Michael, still evaluating options for your rental?", ts(4000), "outbound"),
            (leads_by_name["Michael Reyes"], "text", "Yes, I need a clean as-is offer.", ts(3920), "inbound"),
            (leads_by_name["Michael Reyes"], "call", "Talked through avoided mortgage payment and fast close option.", ts(2500), "outbound"),
            (leads_by_name["Michael Reyes"], "text", "Can you close this month if I accept?", ts(1800), "inbound"),
            (leads_by_name["Michael Reyes"], "note", "Situation: vacant rental costing money monthly. Notes: strong urgency.", ts(900), "outbound"),
            # WARM (3-5 each; mixed signals)
            (leads_by_name["Emily Brooks"], "text", "Hi Emily, are you still exploring selling this year?", ts(6000), "outbound"),
            (leads_by_name["Emily Brooks"], "text", "I might sell soon, still reviewing options.", ts(5600), "inbound"),
            (leads_by_name["Emily Brooks"], "text", "Would it help if I shared current as-is value ranges?", ts(4100), "outbound"),
            (leads_by_name["Emily Brooks"], "text", "Maybe, send when you can.", ts(3900), "inbound"),
            (leads_by_name["Daniel Foster"], "text", "Checking in Daniel—any update after contractor quotes?", ts(6100), "outbound"),
            (leads_by_name["Daniel Foster"], "text", "Still figuring things out, not ready yet.", ts(5200), "inbound"),
            (leads_by_name["Daniel Foster"], "text", "No rush, I can check back next week.", ts(3200), "outbound"),
            (leads_by_name["Olivia Price"], "text", "Are you still considering a move after the school year?", ts(5800), "outbound"),
            (leads_by_name["Olivia Price"], "text", "Possibly, timeline is unclear right now.", ts(5200), "inbound"),
            (leads_by_name["Olivia Price"], "text", "Understood—want me to send a rough value range?", ts(3000), "outbound"),
            (leads_by_name["Ryan Bennett"], "text", "Hi Ryan, open to discussing options this week?", ts(7000), "outbound"),
            (leads_by_name["Ryan Bennett"], "text", "Maybe. Just exploring options for now.", ts(6500), "inbound"),
            (leads_by_name["Ryan Bennett"], "call", "Short call; interested in numbers but no commitment.", ts(5000), "outbound"),
            (leads_by_name["Ryan Bennett"], "text", "If numbers make sense I might sell soon.", ts(4200), "inbound"),
            (leads_by_name["Megan Walsh"], "text", "Checking in Megan—still thinking about selling after lease end?", ts(6200), "outbound"),
            (leads_by_name["Megan Walsh"], "text", "Possibly, tenant timeline is still uncertain.", ts(5600), "inbound"),
            (leads_by_name["Megan Walsh"], "text", "Got it, I can circle back once lease details are clear.", ts(3300), "outbound"),
            # COLD (1-3 each; weak engagement / ghosting)
            (leads_by_name["Chris Turner"], "text", "Hi Chris, would you consider selling this year?", ts(8000), "outbound"),
            (leads_by_name["Chris Turner"], "text", "Maybe next year.", ts(7800), "inbound"),
            (leads_by_name["Chris Turner"], "text", "No problem, I can check back later this season.", ts(5000), "outbound"),
            (leads_by_name["Laura Jenkins"], "text", "Would you like a quick value estimate for your place?", ts(7800), "outbound"),
            (leads_by_name["Kevin Simmons"], "text", "Checking in Kevin—any plans to sell soon?", ts(7600), "outbound"),
            (leads_by_name["Kevin Simmons"], "text", "Just exploring options.", ts(7200), "inbound"),
            (leads_by_name["Natalie Reed"], "text", "Hi Natalie, open to talking about a possible sale timeline?", ts(7400), "outbound"),
            (leads_by_name["Brandon Hayes"], "text", "Would an as-is option be helpful after renovations?", ts(7300), "outbound"),
            (leads_by_name["Alyssa Grant"], "text", "Hi Alyssa, any update on your plans?", ts(7100), "outbound"),
            (leads_by_name["Alyssa Grant"], "text", "Still uncertain, maybe later.", ts(6900), "inbound"),
            (leads_by_name["Tyler Morgan"], "text", "Checking in Tyler—still waiting on probate paperwork?", ts(7050), "outbound"),
            (leads_by_name["Tyler Morgan"], "text", "Yes, nothing new yet.", ts(6800), "inbound"),
            (leads_by_name["Rachel Stone"], "text", "Would you consider selling if the offer made sense?", ts(6900), "outbound"),
            (leads_by_name["Ethan Parker"], "text", "Hi Ethan, want updated comps for spring planning?", ts(6750), "outbound"),
            (leads_by_name["Ethan Parker"], "text", "Maybe later, not urgent.", ts(6400), "inbound"),
            (leads_by_name["Hannah Collins"], "text", "Checking in Hannah—still okay to reconnect later?", ts(6600), "outbound"),
        ]
        conn.executemany(
            "INSERT INTO interactions (lead_id, type, content, timestamp, direction) VALUES (?,?,?,?,?)",
            interactions_data,
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
