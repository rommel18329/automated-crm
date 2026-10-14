"""Backfill the CRM from a Google Voice Takeout export (Takeout/Voice/Calls).

GV Takeout writes one HTML file per conversation/event. Text files hold the
full thread, both directions, with exact timestamps:

    <div class="message">
      <abbr class="dt" title="2026-05-20T14:57:43.743-05:00">...</abbr>:
      <cite class="sender vcard"><a class="tel" href="tel:+17732178686">
          <abbr class="fn">Me</abbr></a></cite>:
      <q>message body</q>
    </div>

"Me" (tel +17732178686, our GV line) == outbound; anyone else == inbound.

This is the authoritative one-time backfill (complete, both directions). Ongoing
capture stays on the Gmail pipe. Idempotent: re-running inserts only what's
missing, using per-(lead, direction, body) occurrence counts so repeated short
messages ("Yes" twice) are preserved without duplicating.
"""
from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from . import db

GV_NUMBER_DIGITS = "17732178686"          # our Google Voice line ("Me")

_MSG_RE = re.compile(
    r'<div class="message">.*?'
    r'<abbr class="dt" title="([^"]+)">.*?'
    r'href="tel:([^"]*)".*?'
    r'<q>(.*?)</q>',
    re.DOTALL,
)
_FILE_NUM_RE = re.compile(r'(\+?1?\d{10})')


def _norm(s: str) -> str:
    """Collapse whitespace + lowercase for dedup matching (not for storage)."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _digits10(s: str) -> str:
    d = "".join(c for c in (s or "") if c.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d


def _to_utc_naive(iso: str) -> str | None:
    """Takeout dt (local, with offset) -> UTC naive ISO seconds, matching the
    DB's datetime.utcnow() convention so ordering is consistent."""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return None


def parse_text_file(path: Path) -> list[dict]:
    """Return [{ts, direction, body, their_digits}] for a Text export file."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in _MSG_RE.finditer(raw):
        iso, tel, body = m.group(1), m.group(2), m.group(3)
        ts = _to_utc_naive(iso)
        if not ts:
            continue
        tel_d = _digits10(tel)
        is_me = _digits10(tel) == _digits10(GV_NUMBER_DIGITS)
        body_txt = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        if not body_txt:
            continue
        out.append({
            "ts": ts,
            "direction": "outbound" if is_me else "inbound",
            "body": body_txt,
            "their_digits": "" if is_me else tel_d,
        })
    return out


def _thread_number(msgs: list[dict], path: Path) -> str:
    """The counterparty's 10-digit number: first inbound tel, else from filename."""
    for mm in msgs:
        if mm["their_digits"]:
            return mm["their_digits"]
    fm = _FILE_NUM_RE.search(path.name)
    return _digits10(fm.group(1)) if fm else ""


def _contact_name(path: Path) -> str:
    """'Paul(303 W MAIN ST,IL) - Text - 2026-10-05T19_13_06Z.html' -> 'Paul(303 W MAIN ST,IL)'."""
    return path.name.split(" - Text - ")[0].strip()


def scan(takeout_calls_dir: Path) -> dict:
    """Parse every Text file; group messages by counterparty number.

    A file holding only OUR messages (nothing they sent back in that stretch)
    has no counterparty tel and, for a saved contact, no number in the
    filename. Those used to be dropped silently -- e.g. a 2026-10-05 text to
    Paul never reached the CRM. Resolve them through the contact name: GV
    names every file of a contact the same way, and their other files carry
    the number. A name mapping to two different numbers is left unresolved."""
    by_num: dict[str, list[dict]] = defaultdict(list)
    files = sorted(takeout_calls_dir.glob("* - Text - *.html"))
    name_nums: dict[str, set] = defaultdict(set)
    pending: list[tuple[Path, list[dict]]] = []
    for f in files:
        msgs = parse_text_file(f)
        if not msgs:
            continue
        num = _thread_number(msgs, f)
        if not num:
            pending.append((f, msgs))
            continue
        name_nums[_contact_name(f)].add(num)
        by_num[num].extend(msgs)
    noname, by_name = 0, 0
    for f, msgs in pending:
        nums = name_nums.get(_contact_name(f), set())
        if len(nums) != 1:
            noname += 1
            continue
        by_num[next(iter(nums))].extend(msgs)
        by_name += 1
    return {"files": len(files), "no_number": noname, "by_name": by_name, "by_num": by_num}


_PHONE_RE = re.compile(r"\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}")


def _alt_phones(lead: dict) -> list[str]:
    """Extra numbers kept in the lead's timeline text ('Alt phones: 309-...')."""
    return [_digits10(m) for m in _PHONE_RE.findall(lead.get("timeline") or "")]


_NEAR_DUP_SECONDS = 600
_NEAR_DUP_RATIO = 0.85


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _near_duplicate(ts: str, direction: str, body: str, existing: list) -> bool:
    """Same text already logged within 10 min with only punctuation/line-break
    drift (Gmail-imported or hand-pasted copy vs the Takeout copy)."""
    t = datetime.fromisoformat(ts)
    b = _alnum(body)
    for d, et, eb in existing:
        if d == direction and abs((t - et).total_seconds()) <= _NEAR_DUP_SECONDS \
                and SequenceMatcher(None, b, eb).ratio() >= _NEAR_DUP_RATIO:
            return True
    return False


def plan_import(takeout_calls_dir: Path, db_path: Path = db.DB_PATH) -> dict:
    """Dry-run: what WOULD be inserted, matched to leads, with dedup applied."""
    data = scan(takeout_calls_dir)
    leads = db.fetch_leads(db_path)

    def _rank(l):
        # Prefer a live lead over a dead/duplicate one sharing the same number,
        # then the highest id (most recent). Fixes backfilling into a lead that
        # was marked "[DUPLICATE -> see lead N]".
        dead = l["status"] == "dead" or "[DUPLICATE" in (l.get("name") or "")
        return (0 if dead else 1, l["id"])

    phone_to_lead: dict = {}
    for l in leads:
        d = _digits10(l["phone"])
        if not d:
            continue
        cur = phone_to_lead.get(d)
        if cur is None or _rank(l) > _rank(cur):
            phone_to_lead[d] = l
    # Alt phones only fill numbers no lead has as its main phone.
    alt_to_lead: dict = {}
    for l in leads:
        for d in _alt_phones(l):
            if d and d not in phone_to_lead:
                cur = alt_to_lead.get(d)
                if cur is None or _rank(l) > _rank(cur):
                    alt_to_lead[d] = l
    phone_to_lead.update(alt_to_lead)

    matched, unmatched = [], []
    for num, msgs in data["by_num"].items():
        lead = phone_to_lead.get(num)
        if not lead:
            unmatched.append((num, len(msgs)))
            continue
        existing = db.fetch_interactions(lead["id"], db_path)
        # Normalize for dedup so whitespace/case drift (e.g. a Gmail-imported
        # inbound vs the Takeout copy) doesn't create a near-duplicate.
        have = Counter((i["direction"], _norm(i["content"]))
                       for i in existing if i["type"] == "text")
        logged = [(i["direction"], datetime.fromisoformat(i["timestamp"][:19]), _alnum(i["content"]))
                  for i in existing if i["type"] == "text"]
        groups: dict = defaultdict(list)
        for mm in sorted(msgs, key=lambda x: x["ts"]):
            groups[(mm["direction"], _norm(mm["body"]))].append((mm["ts"], mm["body"]))
        to_add = []
        for (direction, nbody), items in groups.items():
            skip = have.get((direction, nbody), 0)
            for ts, body in items[skip:]:
                if _near_duplicate(ts, direction, body, logged):
                    continue
                to_add.append((ts, direction, body))
        matched.append({"lead_id": lead["id"], "name": lead["name"],
                        "num": num, "total": len(msgs), "to_add": to_add})
    return {"stats": data, "matched": matched, "unmatched": unmatched}


def run_import(takeout_calls_dir: Path, db_path: Path = db.DB_PATH) -> dict:
    plan = plan_import(takeout_calls_dir, db_path)
    inserted = 0
    for m in plan["matched"]:
        for ts, direction, body in m["to_add"]:
            db.add_interaction(m["lead_id"], "text", body, direction,
                               db_path=db_path, ts=datetime.fromisoformat(ts),
                               channel="gv")
            inserted += 1
    return {"inserted": inserted,
            "matched_leads": len([m for m in plan["matched"] if m["to_add"]]),
            "unmatched_numbers": len(plan["unmatched"])}
