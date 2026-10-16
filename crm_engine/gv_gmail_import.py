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
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import db

_STATE = Path(__file__).resolve().parents[1] / "gv_imported_ids.json"
_FROM_RE = re.compile(r'<([0-9]+)\.([0-9]+)\.[^@]+@txt\.voice\.google\.com>')
_TO_RE = re.compile(r'([0-9]+)\.([0-9]+)(?:\.[^@]+)?@txt\.voice\.google\.com')
_SUBJ_NAME_RE = re.compile(r'from\s+(.*?)\s*\(\d{3}\)\s*\d{3}-\d{4}\s*$')

# Google Voice only accepts texts sent to a reply address it issued on an
# incoming text: <gv-line>.<their-number>.<code>@txt.voice.google.com. The bare
# <gv-line>.<their-number>@ form bounces ("address not found"). So we keep the
# newest issued address per contact and only ever send to one of those.
_REPLY_STORE = Path(__file__).resolve().parents[1] / "gv_reply_addrs.json"
_REPLY_ADDR_RE = re.compile(
    r'([0-9]{11})\.([0-9]{10,11})\.([A-Za-z0-9_-]+)@txt\.voice\.google\.com', re.I)
_BOUNCE_ADDR_RE = re.compile(
    r'[0-9]+\.[0-9]+(?:\.[A-Za-z0-9_-]+)?@txt\.voice\.google\.com', re.I)

NO_GV_THREAD_MSG = ("No Google Voice text thread with this number yet. Google Voice can "
                    "only reply to people who have texted it, so nothing was sent.")


def load_reply_addrs() -> dict:
    """{'XXX-XXX-XXXX': {'addr': reply address, 'ts': naive-UTC ISO of the email}}"""
    try:
        return json.loads(_REPLY_STORE.read_text())
    except Exception:
        return {}


def _save_reply_addrs(store: dict) -> None:
    try:
        _REPLY_STORE.write_text(json.dumps(store, indent=1, sort_keys=True))
    except Exception:
        pass


def reply_addr_from_header(value: str):
    """(phone, reply address) from a From/To header holding a GV-issued address
    for our line, else None."""
    m = _REPLY_ADDR_RE.search(value or "")
    if not m:
        return None
    from .gmail_client import GV_LINE
    if m.group(1) != GV_LINE:
        return None
    phone = _fmt_phone(m.group(2))
    if not phone:
        return None
    return phone, f"{m.group(1)}.{m.group(2)}.{m.group(3)}@txt.voice.google.com"


def remember_reply_addr(store: dict, phone: str, addr: str, ts_iso: str) -> bool:
    """Keep the newest issued address per contact. Returns True if it changed."""
    cur = store.get(phone)
    if cur and cur.get("addr") and (cur.get("ts") or "") >= ts_iso:
        return False
    store[phone] = {"addr": addr, "ts": ts_iso}
    return True


def has_reply_addr(phone: str, store: dict | None = None) -> bool:
    d = re.sub(r"\D", "", phone or "")
    key = _fmt_phone(d) if d else None
    return bool(key) and bool((store if store is not None else load_reply_addrs()).get(key))


def _msg_utc(full: dict) -> datetime:
    return datetime.fromtimestamp(int(full["internalDate"]) / 1000, timezone.utc).replace(tzinfo=None)


def bounced_relay_addrs(client, since_days: int = 60) -> dict:
    """{relay address (lowercased): [bounce times, naive UTC]} from mailer-daemon
    notices about txt.voice.google.com addresses."""
    out: dict = {}
    try:
        svc, token = client.service, None
        while True:
            r = svc.users().messages().list(
                userId="me", maxResults=100, pageToken=token,
                q=f"from:mailer-daemon txt.voice.google.com newer_than:{int(since_days)}d").execute()
            for m in r.get("messages", []):
                full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                                  metadataHeaders=["Date"]).execute()
                for a in _BOUNCE_ADDR_RE.findall(full.get("snippet", "")):
                    out.setdefault(a.lower(), []).append(_msg_utc(full))
            token = r.get("nextPageToken")
            if not token:
                break
    except Exception:
        pass
    return out


def _was_bounced(bounced: dict, to_header: str, ts: datetime) -> bool:
    m = _BOUNCE_ADDR_RE.search(to_header or "")
    if not m:
        return False
    for bts in bounced.get(m.group(0).lower(), []):
        if ts - timedelta(minutes=2) <= bts <= ts + timedelta(minutes=30):
            return True
    return False


def refresh_reply_addr(client, phone: str):
    """Check Gmail for this contact's newest incoming GV text, store its reply
    address, and return the newest address on file (or None)."""
    d = re.sub(r"\D", "", phone or "")[-10:]
    key = _fmt_phone(d) if len(d) == 10 else None
    if not key:
        return None
    store = load_reply_addrs()
    try:
        svc = client.service
        r = svc.users().messages().list(
            userId="me", maxResults=3,
            q=f'from:txt.voice.google.com subject:"({d[:3]}) {d[3:6]}-{d[6:]}"').execute()
        for m in r.get("messages", []):
            full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                              metadataHeaders=["From"]).execute()
            hdr = {h["name"].lower(): h["value"] for h in full["payload"].get("headers", [])}
            hit = reply_addr_from_header(hdr.get("from", ""))
            if hit and hit[0] == key and remember_reply_addr(
                    store, key, hit[1], _msg_utc(full).isoformat(timespec="seconds")):
                _save_reply_addrs(store)
    except Exception:
        pass
    rec = store.get(key)
    return rec.get("addr") if rec else None


def _wait_for_bounce(client, addr: str, sent_ms: int, timeout: int = 45) -> bool:
    """True if a mailer-daemon notice about `addr` arrives after the send.
    Google Voice rejects bad addresses within seconds."""
    addr_l, deadline = addr.lower(), time.time() + timeout
    while True:
        try:
            svc = client.service
            r = svc.users().messages().list(userId="me", maxResults=10,
                                            q="from:mailer-daemon newer_than:1d").execute()
            for m in r.get("messages", []):
                full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                                  metadataHeaders=["Date"]).execute()
                if (int(full["internalDate"]) >= sent_ms - 5000
                        and addr_l in full.get("snippet", "").lower()):
                    return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(5)


def backfill_reply_addrs(client, since_days: int = 400) -> dict:
    """One-time: record the newest GV-issued reply address for every contact
    from Gmail history (incoming GV texts, plus our own non-bounced sends)."""
    svc = client.service

    def list_ids(q):
        ids, token = [], None
        while True:
            r = svc.users().messages().list(userId="me", q=q, maxResults=500,
                                            pageToken=token).execute()
            ids += [m["id"] for m in r.get("messages", [])]
            token = r.get("nextPageToken")
            if not token or len(ids) >= 5000:
                return ids

    def metadata(ids, headers):
        out = {}
        for s in range(0, len(ids), 50):
            batch = svc.new_batch_http_request()
            for i in ids[s:s + 50]:
                batch.add(svc.users().messages().get(userId="me", id=i, format="metadata",
                                                     metadataHeaders=headers),
                          request_id=i,
                          callback=lambda rid, resp, exc: out.__setitem__(rid, resp) if not exc else None)
            batch.execute()
            time.sleep(0.5)
        for i in ids:   # retry any the batch dropped
            if i not in out:
                try:
                    out[i] = svc.users().messages().get(userId="me", id=i, format="metadata",
                                                        metadataHeaders=headers).execute()
                except Exception:
                    pass
        return out

    store = load_reply_addrs()
    bounced = bounced_relay_addrs(client, since_days)
    changed = 0
    for q, hdr_name in ((f"from:txt.voice.google.com newer_than:{since_days}d", "From"),
                        (f"in:sent to:txt.voice.google.com newer_than:{since_days}d", "To")):
        for full in metadata(list_ids(q), [hdr_name]).values():
            hdr = {h["name"].lower(): h["value"] for h in full["payload"].get("headers", [])}
            value, ts = hdr.get(hdr_name.lower(), ""), _msg_utc(full)
            if hdr_name == "To" and _was_bounced(bounced, value, ts):
                continue
            hit = reply_addr_from_header(value)
            if hit and remember_reply_addr(store, hit[0], hit[1], ts.isoformat(timespec="seconds")):
                changed += 1
    _save_reply_addrs(store)
    return {"contacts_with_reply_address": len(store), "updated": changed}


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


_CONTACTS = Path(__file__).resolve().parents[1] / "gv_contacts.json"


def load_gv_contacts() -> set:
    """Phone numbers (XXX-XXX-XXXX) that have texted the Google Voice number."""
    try:
        return set(json.loads(_CONTACTS.read_text()))
    except Exception:
        return set()


def _save_contacts(contacts: set) -> None:
    try:
        _CONTACTS.write_text(json.dumps(sorted(contacts)))
    except Exception:
        pass


# ── Moving Google Voice sellers onto a Twilio number ─────────────────────
_MOVED = Path(__file__).resolve().parents[1] / "gv_moved.json"
MOVE_TO_TWILIO_TEXT = ("Hi {first}, it's {sender} with Silverline. I'm moving my texts to this "
                       "number, so please save it and text me here from now on. Photos of the "
                       "property come through here too.")


def load_moved_to_twilio() -> dict:
    """{'XXX-XXX-XXXX': ISO time the move text was sent from Twilio}."""
    try:
        return json.loads(_MOVED.read_text())
    except Exception:
        return {}


def mark_moved_to_twilio(phone: str) -> None:
    moved = load_moved_to_twilio()
    moved[phone] = datetime.now().astimezone().isoformat(timespec="seconds")
    tmp = _MOVED.with_suffix(".tmp")
    tmp.write_text(json.dumps(moved, indent=1, sort_keys=True))
    tmp.replace(_MOVED)


def send_move_to_twilio(lead_id: int, body: str, db_path=db.DB_PATH) -> dict:
    """Send the switch text from Twilio (never Google Voice). Once it is sent,
    this lead's future texts route through Twilio. Manual click only."""
    from .messaging_gateway import send_sms, _pick_from_number
    lead = db.fetch_lead(lead_id, db_path)
    if not lead:
        return {"sent": False, "error": "lead not found"}
    result = send_sms(lead_id, body, db_path)
    if result.get("sent"):
        mark_moved_to_twilio(lead["phone"])
        db.add_interaction(
            lead_id, "note",
            f"📲 Moved to Twilio: sent the switch text from {_pick_from_number(lead['phone'])}. "
            "Texts to this lead now go from Twilio.",
            "outbound", db_path)
    return result


def is_gv_contact(phone: str) -> bool:
    """Texts to this contact go from Google Voice (not yet moved to Twilio)."""
    return bool(phone) and phone in load_gv_contacts() and phone not in load_moved_to_twilio()


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
    for marker in ("To respond to this",
                   "YOUR ACCOUNT <https://voice.google.com> HELP CENTER",
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


def _already_logged(lead_id: int, text: str, ts: datetime, db_path,
                    direction: str = "inbound") -> bool:
    """True if a near-identical text already exists for this lead/direction
    (catches the Takeout import + any overlap). Exact content within +/-2h."""
    lo = (ts - timedelta(hours=2)).isoformat()
    hi = (ts + timedelta(hours=2)).isoformat()
    with db.get_conn(db_path) as conn:
        row = conn.execute(
            """SELECT 1 FROM interactions WHERE lead_id=? AND type='text'
               AND direction=? AND content=? AND timestamp BETWEEN ? AND ?
               LIMIT 1""",
            (lead_id, direction, text, lo, hi),
        ).fetchone()
    return row is not None


def _extract_sent_body(body: str) -> str:
    """Our sent text sits above the quoted GV original; strip the quote/footer."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = []
    for ln in text.split("\n"):
        st = ln.strip()
        if st.startswith(">") or st.startswith("<https://voice.google.com>"):
            break
        if st.startswith("On ") and st.endswith("wrote:"):
            break
        if st in ("YOUR ACCOUNT", "To respond to this text message, "
                  "reply to this email or visit Google Voice."):
            break
        lines.append(ln)
    return "\n".join(lines).strip()


def parse_gv_sent(msg: dict) -> dict | None:
    """Parse one of OUR Sent GV emails -> {phone, text, ts}. The To address is
    <gv-line>.<their-number>[.token]@txt.voice.google.com; body is what we sent."""
    to = msg.get("to", "") or ""
    m = _TO_RE.search(to)
    if not m:
        return None
    phone = _fmt_phone(m.group(2))
    if not phone:
        return None
    text = _extract_sent_body(msg.get("body", ""))
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(msg.get("date") or "")
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        ts = dt
    except Exception:
        ts = datetime.utcnow()
    return {"phone": phone, "text": text, "ts": ts}


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
    contacts = load_gv_contacts()
    reply_store = load_reply_addrs()
    bounced = bounced_relay_addrs(client, max(int(since_days), 3))
    query = f"from:txt.voice.google.com newer_than:{int(since_days)}d"

    for tid in client.list_threads(query):
        thread = client.get_thread(tid)
        for msg in thread.get("messages", []):
            mid = msg.get("id")
            if not mid:
                continue
            parsed = parse_gv_email(msg)
            if parsed:
                contacts.add(parsed["phone"])   # GV-sourced contact (idempotent)
                _hit = reply_addr_from_header(msg.get("from", ""))
                if _hit:
                    remember_reply_addr(reply_store, _hit[0], _hit[1],
                                        parsed["ts"].isoformat(timespec="seconds"))
            if mid in done:
                continue
            done.add(mid)                       # processed, parseable or not
            if not parsed:
                continue
            phone, text, ts = parsed["phone"], parsed["text"], parsed["ts"]
            lead = by_phone.get(phone)
            if lead:
                if _already_logged(lead["id"], text, ts, db_path):
                    skipped += 1
                    continue
                db.add_interaction(lead["id"], "text", text, "inbound", db_path, ts=ts, channel="gv")
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

    # Outbound: our own GV replies live in Gmail Sent (GV never forwards them
    # inbound). Capture them so the CRM knows what we've already said.
    sent_q = f"in:sent to:txt.voice.google.com newer_than:{int(since_days)}d"
    for tid in client.list_threads(sent_q):
        thread = client.get_thread(tid)
        for msg in thread.get("messages", []):
            mid = msg.get("id")
            if not mid or mid in done:
                continue
            done.add(mid)
            parsed = parse_gv_sent(msg)
            if not parsed:
                continue
            phone, text, ts = parsed["phone"], parsed["text"], parsed["ts"]
            if _was_bounced(bounced, msg.get("to", ""), ts):
                skipped += 1      # Google Voice rejected it: never delivered
                continue
            _hit = reply_addr_from_header(msg.get("to", ""))
            if _hit:
                remember_reply_addr(reply_store, _hit[0], _hit[1], ts.isoformat(timespec="seconds"))
            lead = by_phone.get(phone)
            if not lead:
                continue  # never create a lead from an outbound-only thread
            if _already_logged(lead["id"], text, ts, db_path, direction="outbound"):
                skipped += 1
                continue
            db.add_interaction(lead["id"], "text", text, "outbound", db_path,
                               ts=ts, channel="gv")
            touched.add(lead["id"])
            matched += 1

    _save_done(done)
    _save_contacts(contacts)
    _save_reply_addrs(reply_store)

    return {"matched": matched, "unmatched": unmatched, "skipped": skipped,
            "leads_touched": len(touched)}


def send_gv(lead_id: int, body: str, db_path=db.DB_PATH, wait_for_bounce: int = 45) -> dict:
    """Text a lead from the Google Voice number by emailing their GV-issued reply
    address, then wait for a bounce before calling it sent. Logs the outbound
    text only if it wasn't rejected. Returns {"sent": bool, "error", ...}; a
    contact with no GV thread returns no_gv_thread=True and sends nothing."""
    lead = db.fetch_lead(lead_id, db_path)
    if not lead:
        return {"sent": False, "error": "lead not found"}
    try:
        from .gmail_client import GmailClient
        client = GmailClient.connect(interactive=False)
    except Exception as e:
        return {"sent": False, "error": f"Gmail not connected: {type(e).__name__}: {str(e)[:160]}"}
    addr = refresh_reply_addr(client, lead["phone"])
    if not addr:
        return {"sent": False, "no_gv_thread": True, "error": NO_GV_THREAD_MSG}
    sent_ms = int(time.time() * 1000)
    try:
        resp = client.send_gv_reply(addr, body)
    except Exception as e:
        return {"sent": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    if _wait_for_bounce(client, addr, sent_ms, wait_for_bounce):
        return {"sent": False, "bounced": True,
                "error": "Google Voice rejected it (bounced). NOT delivered and not logged."}
    # Delivered as far as we can tell: log it, and mark the Gmail message done so
    # the Sent-folder sync doesn't log it a second time.
    db.add_interaction(lead_id, "text", body, "outbound", db_path, channel="gv")
    if resp and resp.get("id"):
        done = _load_done()
        done.add(resp["id"])
        _save_done(done)
    return {"sent": True, "error": None}


def route_text(lead_id: int, body: str, db_path=db.DB_PATH) -> dict:
    """Send a text the way this lead knows us: Google Voice contacts get it from
    the Google Voice number (if they have a thread); everyone else from Twilio.
    Never falls back to Twilio on its own -- a GV contact with no thread comes
    back as {"sent": False, "no_gv_thread": True} so the user decides.
    Contacts already moved to Twilio (send_move_to_twilio) text from Twilio."""
    lead = db.fetch_lead(lead_id, db_path)
    if not lead:
        return {"sent": False, "error": "lead not found"}
    if lead.get("phone") in load_gv_contacts() and lead.get("phone") not in load_moved_to_twilio():
        return send_gv(lead_id, body, db_path)
    from .messaging_gateway import send_sms
    return send_sms(lead_id, body, db_path)
