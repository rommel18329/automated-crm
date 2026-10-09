"""Link email threads (the email_reviews store) to leads so they can show
inline in the Messages conversation, time-ordered with texts/calls.

A thread is attached to a lead when either the cross-channel matcher already
set cross_channel.lead_id, or the thread's counterparty address matches a
lead's email field. Each email renders as its own bubble with a deep link into
Gmail; we never copy email bodies into the interactions table.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import getaddresses

from . import db
from .gmail_client import MAILBOX

# Google Voice forwards texts as emails from this host; those are NOT real email
# threads (they already show as GV texts), so they must never enter the email
# timeline or populate lead.email.
_GV_HOST = "@txt.voice.google.com"


def gmail_thread_url(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/{MAILBOX}/#all/{thread_id}"


def _name_tokens(n: str) -> list:
    return re.sub(r"[^a-z ]", "", (n or "").lower()).split()


def _build_lastname_index(leads: list) -> dict:
    idx: dict = {}
    for l in leads:
        toks = _name_tokens(l["name"])
        if toks:
            idx.setdefault(toks[-1], []).append((toks, l))
    return idx


def _match_lead(cp_name: str, lastname_index: dict):
    """Conservative: unique lead whose last name matches and whose first name
    shares a prefix (so 'Jon Kutas' matches 'Jonathan Kutas')."""
    toks = _name_tokens(cp_name)
    if len(toks) < 2:
        return None
    cands = lastname_index.get(toks[-1], [])
    hits = []
    for ltoks, lead in cands:
        if ltoks and (ltoks[0].startswith(toks[0]) or toks[0].startswith(ltoks[0])):
            hits.append(lead)
    return hits[0] if len(hits) == 1 else None


def _first_addr(value: str):
    a = getaddresses([value or ""])
    return (a[0][0], a[0][1].lower()) if a and a[0][1] else ("", "")


def _norm_ts(s: str) -> str:
    """ISO (possibly tz-aware) -> naive UTC seconds, to sort beside interactions."""
    try:
        dt = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return s or ""


def _counterparty(tj: dict):
    """(display_name, address) of the non-us party, or ('','') for a GV-forward
    thread (which is a text, not a real email) or an all-us thread."""
    for m in tj.get("messages", []):
        name, addr = _first_addr(m.get("from", ""))
        if addr and addr != MAILBOX:
            if addr.endswith(_GV_HOST):
                return ("", "")   # GV text forward, not a real email
            return (name, addr)
    return ("", "")


def build_email_index(db_path=db.DB_PATH) -> dict:
    """lead_id -> list of email items shaped like interactions:
    {type:'email', direction, timestamp, subject, snippet, url}."""
    try:
        rows = db.email_reviews(MAILBOX, db_path)
    except Exception:
        return {}
    email_to_lead = {}
    for l in db.fetch_leads(db_path):
        e = (l.get("email") or "").strip().lower()
        if e:
            email_to_lead[e] = l["id"]

    out: dict = {}
    for r in rows:
        try:
            tj = json.loads(r["thread_json"])
        except Exception:
            continue
        _, addr = _counterparty(tj)
        if not addr:
            continue   # GV-forward or all-us thread
        lead_id = email_to_lead.get(addr)
        if not lead_id:
            continue
        tid = r["thread_id"]
        url = gmail_thread_url(tid)
        for m in tj.get("messages", []):
            _, frm_addr = _first_addr(m.get("from", ""))
            direction = "outbound" if frm_addr == MAILBOX else "inbound"
            body = (m.get("body") or "").replace("\r", " ").replace("\n", " ").strip()
            out.setdefault(lead_id, []).append({
                "type": "email",
                "direction": direction,
                "timestamp": _norm_ts(m.get("timestamp") or r["latest_received_at"]),
                "subject": (m.get("subject") or "").strip(),
                "snippet": body[:200],
                "url": url,
                "content": (m.get("subject") or "").strip(),  # for any generic use
            })
    return out


def backfill_lead_emails(db_path=db.DB_PATH) -> list:
    """Associate real (non-GV) email threads to leads by a conservative name
    match, setting lead.email where the lead has none. Returns the list of
    (lead_id, name, address) set, for review. Idempotent."""
    try:
        rows = db.email_reviews(MAILBOX, db_path)
    except Exception:
        return []
    leads = db.fetch_leads(db_path)
    lastname_index = _build_lastname_index(leads)
    taken = {(l.get("email") or "").strip().lower() for l in leads if l.get("email")}
    done_lead = {l["id"] for l in leads if (l.get("email") or "").strip()}
    out = []
    for r in rows:
        try:
            tj = json.loads(r["thread_json"])
        except Exception:
            continue
        cp_name, addr = _counterparty(tj)
        if not addr or addr in taken:
            continue
        lead = _match_lead(cp_name, lastname_index)
        if not lead or lead["id"] in done_lead:
            continue
        db.update_lead(lead["id"], {"email": addr}, db_path)
        done_lead.add(lead["id"])
        taken.add(addr)
        out.append((lead["id"], lead["name"], addr))
    return out
