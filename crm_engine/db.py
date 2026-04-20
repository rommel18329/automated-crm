from __future__ import annotations

import sqlite3
import csv
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).resolve().parents[1] / "crm.db"


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


def seed_sample_data(db_path: Path = DB_PATH, replace_existing: bool = False) -> None:
    today = date.today().isoformat()
    now = datetime.utcnow()

    raw_rows = [
        {"first": "Sanchez", "last": "Chavez", "email": "echavez101215@gmail.com", "phones": ["7734544780", "7739889874", "7738424153", "7734740770", ""], "address1": "2424 W Lithuanian Plaza Ct", "city": "Chicago", "state": "IL"},
        {"first": "Zach", "last": "Hellberg", "email": "", "phones": ["3195947713", "3199360881", "3193391300", "3197527363", "3197523563"], "address1": "1516 Sheridan Ave", "city": "Iowa City", "state": "Iowa"},
        {"first": "Joeseph", "last": "Schneider", "email": "", "phones": ["3193619602", "", "", "", ""], "address1": "2150 Chandler St SW", "city": "Cedar Rapids", "state": "IA"},
        {"first": "Jimmie", "last": "Stanley", "email": "", "phones": ["6417773299", "6419325373", "", "", ""], "address1": "323 S 2nd St", "city": "Albia", "state": "IA"},
        {"first": "Beau/Jenny", "last": "Jensen", "email": "", "phones": ["5158357794", "5158364415", "5158263743", "", ""], "address1": "490 Bute St", "city": "Stanhope", "state": "IA"},
        {"first": "KIMBERLI", "last": "(last)", "email": "n/a", "phones": ["319-270-0975", "", "", "", ""], "address1": "1925 belmont parkway NW", "city": "", "state": ""},
        {"first": "Christian", "last": "Committed", "email": "creativerenovationsoutreach@yahoo.com", "phones": ["7733678437", "3122877941", "3124347739", "7083335694", "7738631322"], "address1": "7833 S Marquette Ave", "city": "Chicago", "state": "IL"},
        {"first": "Steven B", "last": "Hebert", "email": "", "phones": ["5159553051", "3039796639", "", "", ""], "address1": "500 Avenue E", "city": "Fort Dodge", "state": "IA"},
        {"first": "Taneka", "last": "Dennie", "email": "tanekadennie@gmail.com", "phones": ["7736511568", "7733391567", "", "", ""], "address1": "6811 S Parnell Ave", "city": "Chicago", "state": "IL"},
        {"first": "Premsagar", "last": "Mulkanoor", "email": "premsagarm@gmail.com", "phones": ["7087158561", "8154690025", "7087995863", "8143972947", "6194466395"], "address1": "15870 Dixie Hwy", "city": "Markham", "state": "IL"},
        {"first": "Dondra", "last": "Davis", "email": "fatselftj@hotmail.com", "phones": ["6187221776", "6184331993", "3144564642", "6184331216", "6184625197"], "address1": "1910 Sycamore St", "city": "Alton", "state": "Illinois"},
        {"first": "Jonathan", "last": "Kutas", "email": "jksatuk2@aol.com", "phones": ["2195887890", "8505448525", "8728024002", "2199721754", "3129521530"], "address1": "1702 N Maplewood Ave # 1", "city": "Chicago", "state": "IL"},
        {"first": "James", "last": "Duban", "email": "", "phones": ["(937) 750-1826", "9377501426", "9377501826", "6184480083", "6182358901"], "address1": "1248 Antique Ln", "city": "Mascoutah", "state": "IL"},
        {"first": "Zachary", "last": "Anderson", "email": "", "phones": ["(937) 750-1826", "2172023878", "3094482495", "2173754474", "2178417804"], "address1": "303 1st St", "city": "Congerville", "state": "IL"},
        {"first": "Fernado", "last": "Suaste", "email": "chelsea.mae.1414@gmail.com", "phones": ["7733708049", "7083396685", "7733345218", "7085665932", "7735614302"], "address1": "15637 Turlington Ave", "city": "Harvey", "state": "IL"},
        {"first": "Zenah", "last": "Taher", "email": "brittanysmith3442@gmail.com", "phones": ["7084975910", "7086141855", "7086140443", "7088608497", "7086921856"], "address1": "15217 Lexington Ave", "city": "Harvey", "state": "IL"},
        {"first": "Barry", "last": "Tobin", "email": "tobinbarry@hotmail.com", "phones": ["8153750582", "8157588754", "6077769534", "6183031526", "7087172624"], "address1": "212 S 11th St", "city": "Dekalb", "state": "IL"},
        {"first": "DENNIS", "last": "BALES", "email": "", "phones": ["2178218635", "6189675428", "6184835290", "", ""], "address1": "350 Main St", "city": "Westervelt", "state": ""},
        {"first": "BENJAMIN", "last": "HICKMAN", "email": "", "phones": ["2173160428", "", "", "", ""], "address1": "807 SPATES ST", "city": "JACKSONVILLE", "state": "IL"},
        {"first": "Cleo", "last": "Grant", "email": "gcleo@hotmail.com", "phones": ["7736101332", "7739283983", "7089259974", "7736773921", "7735443156"], "address1": "33 E 122nd Pl", "city": "Chicago", "state": "IL"},
        {"first": "MARGARET", "last": "LIEN", "email": "", "phones": ["3093330623", "", "", "", ""], "address1": "421 S LAFAYETTE ST", "city": "MACOMB", "state": "IL"},
        {"first": "CINDY", "last": "HARDEN", "email": "", "phones": ["8154053759", "8154335152", "8154330090", "8154332837", "6308702418"], "address1": "614 2ND AVE", "city": "OTTAWA", "state": "IL"},
        {"first": "EDVAR", "last": "DUARTE", "email": "", "phones": ["9095613270", "", "", "", ""], "address1": "820 BOBBY AVE", "city": "MACOMB", "state": "IL"},
        {"first": "Nicholas", "last": "Hemann", "email": "", "phones": ["3193216133", "", "", "", ""], "address1": "708 E Iowa Ave", "city": "Iowa City", "state": "IA"},
        {"first": "Robert A", "last": "Chidester", "email": "nchidester@yahoo.com", "phones": ["8153827667", "7737282726", "8153388464", "8153388408", "8153381799"], "address1": "611 McHenry Ave", "city": "Woodstock", "state": "IL"},
        {"first": "Lindsay M", "last": "Dewitt", "email": "", "phones": ["5152930286", "", "", "", ""], "address1": "303 N Cadwell Ave", "city": "Eagle Grove", "state": "IA"},
        {"first": "Dixie", "last": "Leon", "email": "", "phones": ["6416827323", "", "", "", ""], "address1": "418 S Foster Ave", "city": "Ottumwa", "state": "IA"},
        {"first": "CLETUS", "last": "FEIG", "email": "", "phones": ["6183223680", "", "", "", ""], "address1": "633 W 6TH ST", "city": "CENTRALIA", "state": "IL"},
        {"first": "Kendra", "last": "Harms", "email": "", "phones": ["6414308179", "6418476935", "", "", ""], "address1": "725 Front St", "city": "Geneva", "state": "IA"},
        {"first": "James", "last": "Maher", "email": "", "phones": ["(773) 594-6482", "7735946482", "8476630000", "8476639116", "8479665686"], "address1": "1132 W 4th St", "city": "Davenport", "state": "IA"},
        {"first": "David", "last": "Sharkey", "email": "", "phones": ["6417809831", "5152662369", "6412040317", "", ""], "address1": "705 N Illinois St", "city": "Lake City", "state": "IA"},
        {"first": "Stephan", "last": "Palen", "email": "", "phones": ["6417992366", "6416821723", "6417770525", "", ""], "address1": "2420 N Court St", "city": "Ottumwa", "state": "IA"},
        {"first": "DANNY", "last": "SALGADO", "email": "", "phones": ["7739317774", "7737674860", "7735308180", "7734062272", "7737350902"], "address1": "5610 S PARKSIDE AVE", "city": "CHICAGO", "state": "IL"},
        {"first": "Elmer", "last": "Montejo", "email": "", "phones": ["6412267098", "6412267098", "", "", ""], "address1": "1015 Locust St", "city": "Ottumwa", "state": "IA"},
        {"first": "S", "last": "Latinik", "email": "sshlomol@aol.com", "phones": ["2244130735", "2244130735", "8474206533", "8479331210", "8476762011"], "address1": "11545 S Laflin St", "city": "Chicago", "state": "IL"},
        {"first": "Andre", "last": "ALJ INVESTMENTS", "email": "anthony_c_jackson@yahoo.com", "phones": ["7082831152", "3128671883", "7082830780", "", ""], "address1": "1327 W 72nd Pl # 1", "city": "Chicago", "state": "Illinois"},
        {"first": "JINGYUN", "last": "HUANG", "email": "", "phones": ["3124093384", "3096607700", "6462078489", "", ""], "address1": "2420 63RD ST", "city": "DOWNERS GROVE", "state": "Illinois"},
        {"first": "Simon", "last": "Dortch", "email": "reecedillardsigns@gmail.com", "phones": ["7738245185", "3125208532", "7733780939", "7732873077", "7083666744"], "address1": "560 N Laramie Ave # 1", "city": "Chicago", "state": "IL"},
        {"first": "Ryan", "last": "Andreini", "email": "", "phones": ["5157071144", "5152250700", "5152801618", "", ""], "address1": "612 SE 6th St", "city": "Des Moines", "state": "IA"},
    ]

    def normalize_seed_phone(raw: str) -> str | None:
        digits = re.sub(r"\D", "", raw or "")
        if len(digits) != 10:
            return None
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"

    def make_address(row: dict[str, Any]) -> str:
        parts = [row["address1"].strip(), row["city"].strip(), row["state"].strip()]
        return ", ".join([p for p in parts if p])

    records: list[dict[str, Any]] = []
    for idx, row in enumerate(raw_rows, start=1):
        full_name = f"{row['first']} {row['last']}".strip()
        if idx <= 5:
            status = "hot"
            motivation = 7 + (idx % 3)
            stage = "negotiating"
            timeline_days = 30 - idx
            timeline_text = "Wants to sell this month"
            probability = 72 + idx
            touch_count = 4
            last_strategy = "call"
        elif idx <= 15:
            status = "warm"
            motivation = 4 + (idx % 3)
            stage = "qualifying"
            timeline_days = 60 + idx
            timeline_text = "Considering options in the next 2-4 months"
            probability = 42 + idx
            touch_count = 2
            last_strategy = "follow-up"
        else:
            status = "cold"
            motivation = 2 + (idx % 3)
            stage = "initial_contact"
            timeline_days = 180 + idx
            timeline_text = "No immediate timeline to sell"
            probability = min(35, 10 + idx)
            touch_count = 1
            last_strategy = "value-based"

        normalized = [normalize_seed_phone(p) for p in row["phones"]]
        phone = normalized[0] or f"555-3{idx // 100}{(idx // 10) % 10}-{idx:04d}"
        alt_phones = [p for p in normalized[1:] if p]
        alt_phone_note = f" Alt phones: {', '.join(alt_phones)}." if alt_phones else ""
        email_note = f" Email: {row['email']}." if row["email"] else ""

        records.append(
            {
                "name": full_name,
                "phone": phone,
                "status": status,
                "motivation": motivation,
                "timeline": (
                    f"{timeline_text}. Address: {make_address(row)}. Situation: imported lead. "
                    f"Notes: seeded from provided list.{email_note}{alt_phone_note}"
                ),
                "timeline_days": timeline_days,
                "stage": stage,
                "probability": probability,
                "touch_count": touch_count,
                "last_strategy": last_strategy,
            }
        )

    with get_conn(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]
        if existing and not replace_existing:
            return
        if replace_existing:
            conn.execute("DELETE FROM interactions")
            conn.execute("DELETE FROM performance_logs")
            conn.execute("DELETE FROM leads")

        conn.executemany(
            """
            INSERT INTO leads (
                name, phone, status, motivation_score, timeline, timeline_days,
                last_contact_date, next_action_date, touch_count, last_strategy_used,
                conversation_stage, deal_probability
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    r["name"],
                    r["phone"],
                    r["status"],
                    r["motivation"],
                    r["timeline"],
                    r["timeline_days"],
                    today,
                    today,
                    r["touch_count"],
                    r["last_strategy"],
                    r["stage"],
                    r["probability"],
                )
                for r in records
            ],
        )

        leads_by_name = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM leads").fetchall()}
        interactions_data: list[tuple[int, str, str, str, str]] = []

        for idx, rec in enumerate(records, start=1):
            lead_id = leads_by_name[rec["name"]]
            first_name = rec["name"].split()[0]
            base = 500 + idx * 9
            if rec["status"] == "hot":
                interactions_data.extend(
                    [
                        (lead_id, "text", f"Hi {first_name}, can you share your target close date?", (now - timedelta(minutes=base + 60)).isoformat(timespec="seconds"), "outbound"),
                        (lead_id, "text", "I need to move quickly. What can you offer?", (now - timedelta(minutes=base + 40)).isoformat(timespec="seconds"), "inbound"),
                        (lead_id, "call", "Reviewed condition and fast-close options.", (now - timedelta(minutes=base)).isoformat(timespec="seconds"), "outbound"),
                    ]
                )
            elif rec["status"] == "warm":
                interactions_data.extend(
                    [
                        (lead_id, "text", f"Hey {first_name}, still thinking about selling?", (now - timedelta(minutes=base + 45)).isoformat(timespec="seconds"), "outbound"),
                        (lead_id, "text", "Possibly, still reviewing options.", (now - timedelta(minutes=base)).isoformat(timespec="seconds"), "inbound"),
                    ]
                )
            else:
                interactions_data.append(
                    (lead_id, "text", f"Hi {first_name}, checking in if timing has changed on your property.", (now - timedelta(minutes=base)).isoformat(timespec="seconds"), "outbound")
                )

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
