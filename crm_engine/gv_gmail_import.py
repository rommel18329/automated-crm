"""Import Google Voice texts (forwarded to Gmail) into the CRM as inbound
text interactions, so messages people send to the GV number show up in
Messages/context exactly like Twilio inbound texts.

GV forwards each inbound text as an email from <...>.<sender>.<id>@txt.voice.
google.com. We parse the sender number, name, message body and time, match
to a lead by phone (else the unmatched inbox), and log it inbound.

Dedupe is belt-and-suspenders: a JSON set of already-processed Gmail message
IDs (cross-run), plus a content+time check against existing interactions (so
texts already pulled from the old Takeout import are never duplicated). No
database schema change. Read-only inbound only (GV never emails texts you send).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import db

_STATE = Path(__file__).resolve().parents[1] / "gv_imported_ids.json"
_FROM_RE = re.compile(r'<([0-9]+)\.([0-9]+)\.[^@]+@txt\.voice\.google\.com>')
_SUBJ_NAME_RE = re.compile(r'from\s+(.*?)\s*\(\d{3}\)\s*\d{3}-\d{4}\s*$')


def _load_done() -> set:
    try:
        return set(json.loads(_STATE.read_text()))
    except Exception:
        return set()


def _save_done(done: set) -> None:
    try:
        _STATE.write_text(json.dumps(sorted(done)))
    except Exception:
        pass


def _fmt_phone(digits: str) -> str | None:
    d = re.sub(r"\D", "", digits or "")
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    if len(d) != 10:
        return None
    return f"{d[:3]}-{d[3:6]}-{d[6:]}"


def _extract_message(body: str) -> str:
    """The message is between the GV link header and the 'YOUR ACCOUNT' footer."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for marker in ("YOUR ACCOUNT <https://voice.google.com> HELP CENTER",
                   "YOUR ACCOUNT", "This email was sent to you"):
        i = text.find(marker)
        if i != -1:
            text = text[:i]
            break
    # drop the leading google-voice link header line(s)
    lines = [ln for ln in text.split("\n")
             if ln.strip() and ln.strip() != "<https://voice.google.com>"]
    return "\n".join(lines).strip()


def parse_gv_email(msg: dict) -> dict | None:
    """Return {phone, name, text, ts(datetime UTC-naive)} or None if not a GV text."""
    frm = msg.get("from", "") or ""
    m = _FROM_RE.search(frm)
    if not m:
        return None
    phone = _fmt_phone(m.group(2))          # 2nd segment = sender's number
    if not phone:
        return None
    subject = msg.get("subject", "") or ""
    name_m = _SUBJ_NAME_RE.search(subject)
    name = (name_m.group(1).strip() if name_m else "").strip()
    text = _extract_message(msg.get("body", ""))
    if not text:
        return None
    ts = None
    raw_date = msg.get("date") or ""
    try:
        dt = parsedate_to_datetime(raw_date)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        ts = dt
    except Exception:
        ts = datetime.utcnow()
    return {"phone": phone, "name": name, "text": text, "ts": ts}


def _already_logged(lead_id: int, text: str, ts: datetime, db_path) -> bool:
    """True if a near-identical inbound text already exists (catches the prior
    Takeout import + any overlap). Exact content within a +/-2h window."""
    lo = (ts - timedelta(hours=2)).isoformat()
    hi = (ts + timedelta(hours=2)).isoformat()
    with db.get_conn(db_path) as conn:
        row = conn.execute(
            """SELECT 1 FROM interactions WHERE lead_id=? AND type='text'
               AND direction='inbound' AND content=? AND timestamp BETWEEN ? AND ?
               LIMIT 1""",
            (lead_id, text, lo, hi),
        ).fetchone()
    return row is not None


def _unmatched_exists(phone: str, text: str, db_path) -> bool:
    tagged = f"[Google Voice] {text}"
    with db.get_conn(db_path) as conn:
        row = conn.execute(
            """SELECT 1 FROM unmatched_inbox WHERE phone=? AND content=? LIMIT 1""",
            (phone, tagged),
        ).fetchone()
    return row is not None


def sync_google_voice(client=None, since_days: int = 60, db_path=db.DB_PATH) -> dict:
    """Pull GV texts from Gmail into the CRM. Returns a summary dict."""
    from .gmail_client import GmailClient
    if client is None:
        client = GmailClient.connect(interactive=False)
        client.verify_identity()

    done = _load_done()
    leads = db.fetch_leads(db_path)
    by_phone = {l["phone"]: l for l in leads}

    matched = unmatched = skipped = 0
    touched: set[int] = set()
    query = f"from:txt.voice.google.com newer_than:{int(since_days)}d"

    for tid in client.list_threads(query):
        thread = client.get_thread(tid)
        for msg in thread.get("messages", []):
            mid = msg.get("id")
            if not mid or mid in done:
                continue
            done.add(mid)                       # processed, parseable or not
            parsed = parse_gv_email(msg)
            if not parsed:
                continue
            phone, text, ts = parsed["phone"], parsed["text"], parsed["ts"]
            lead = by_phone.get(phone)
            if lead:
                if _already_logged(lead["id"], text, ts, db_path):
                    skipped += 1
                    continue
                db.add_interaction(lead["id"], "text", text, "inbound", db_path, ts=ts)
                fields = {"next_action_date": date.today().isoformat()}
                db.update_lead(lead["id"], fields, db_path)
                try:
                    with db.get_conn(db_path) as conn:
                        conn.execute("DELETE FROM followup_suggestions WHERE lead_id=?",
                                     (lead["id"],))
                except Exception:
                    pass
                touched.add(lead["id"])
                matched += 1
            else:
                if _unmatched_exists(phone, text, db_path):
                    skipped += 1
                else:
                    db.add_unmatched_message(phone, "text", f"[Google Voice] {text}", db_path)
                    unmatched += 1

    _save_done(done)

    # AI auto-note on the leads that got new GV messages (best-effort).
    for lid in touched:
        try:
            from .messaging_gateway import auto_note_after_text
            auto_note_after_text(lid, db_path)
        except Exception:
            pass

    return {"matched": matched, "unmatched": unmatched, "skipped": skipped,
            "leads_touched": len(touched)}
