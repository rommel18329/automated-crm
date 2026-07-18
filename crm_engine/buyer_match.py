"""Buyer–seller matchmaking: rank the investor buy boxes against a seller
lead's property and draft intro texts to the best matches.

The demand side of the business (387 investor leads with buy boxes) is
matched to the supply side (warm/hot/contract sellers) with a single cheap
Haiku call: the buyers are compacted to one line each, so a full 387-buyer
sweep is ~$0.01-0.02. The AI is grounded in the buy_box fields + notes and
must return strict JSON.
"""
from __future__ import annotations

import json
import re

from . import db
from .ai_message_generator import client

MATCH_SYSTEM_PROMPT = """You match a property a real-estate wholesaler has
(or may get) under contract to the investor BUYERS on their list.

You get one SELLER PROPERTY summary and a numbered list of BUYERS with their
criteria (states, property types, price range, condition appetite, free-text
notes). Score how well each buyer's criteria fit THIS property.

Return STRICT JSON only — an array of at most 10 objects, best first:
[{"lead_id": <int>, "score": <1-10>, "reason": "<one short sentence>"}]

Rules:
- Only include buyers scoring 5+. If nothing fits, return [].
- Location fit matters most (state, then any towns/areas named in notes),
  then property type, then price band, then condition/deal type.
- If a buyer names a specific area/town and THIS property is NOT in it,
  score 4 or lower — a location mismatch can never be a strong match. The
  score must agree with the reason: never write a mismatch in the reason
  while giving a high score.
- The reason must cite the buyer's OWN criteria (e.g. "wants riverfront
  between Clinton and LeClaire; this is a Clinton riverfront house").
- Never invent criteria a buyer didn't state. No text outside the JSON."""


def _seller_summary(lead: dict, interactions: list[dict]) -> str:
    """Compact, grounded description of the seller's property."""
    tl = lead.get("timeline") or ""
    notes = [i["content"][:220] for i in interactions
             if i["type"] == "note" and not i["content"].startswith("🤖")][:4]
    recent_ai = [i["content"][:300] for i in interactions
                 if i["content"].startswith(("🤖", "📝"))][:2]
    parts = [
        f"Name: {lead.get('name')}",
        f"Status: {lead.get('status')}",
        f"Details: {tl[:400]}",
    ]
    if notes:
        parts.append("Notes: " + " | ".join(notes))
    if recent_ai:
        parts.append("Call/AI notes: " + " | ".join(recent_ai))
    return "\n".join(parts)


def _buyer_line(lead: dict) -> str | None:
    """One compact line per buyer: id | name | criteria."""
    try:
        bb = json.loads(lead.get("buy_box") or "{}")
    except Exception:
        return None
    if not bb:
        return None
    bits = []
    if bb.get("states"):
        bits.append("states:" + ",".join(bb["states"]))
    if bb.get("property_types"):
        bits.append("types:" + ",".join(bb["property_types"]))
    pmin, pmax = bb.get("price_min", 0), bb.get("price_max", 0)
    if pmin or pmax:
        bits.append(f"price:{pmin}-{pmax}")
    if bb.get("condition") and bb["condition"] != "Any":
        bits.append("cond:" + bb["condition"])
    if bb.get("deal_types"):
        bits.append("deals:" + ",".join(bb["deal_types"]))
    if bb.get("notes"):
        bits.append("notes:" + re.sub(r"\s+", " ", bb["notes"])[:110])
    if not bits:
        return None
    return f"{lead['id']} | {lead.get('name','')[:28]} | " + " ".join(bits)


# Haiku 4.5 pricing per 1M tokens
_IN_RATE, _OUT_RATE = 1.00, 5.00


def match_buyers(seller_lead: dict, interactions: list[dict],
                 db_path=db.DB_PATH) -> dict:
    """Rank all investor buy boxes against this seller's property.
    Returns {"matches": [{lead_id, score, reason, name, phone}], "cost_usd",
    "buyers_scanned"} — or {"error": ...}."""
    buyers = [l for l in db.fetch_leads(db_path) if l.get("status") == "investor"]
    lines = [b for b in (_buyer_line(l) for l in buyers) if b]
    if not lines:
        return {"error": "No investor buy boxes on file."}
    lookup = {l["id"]: l for l in buyers}

    user_msg = (
        "SELLER PROPERTY:\n" + _seller_summary(seller_lead, interactions)
        + "\n\nBUYERS (id | name | criteria):\n" + "\n".join(lines)
        + "\n\nReturn the JSON array now."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=900,
            system=MATCH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = (resp.content[0].text or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        start, end = raw.find("["), raw.rfind("]") + 1
        matches = json.loads(raw[start:end]) if start >= 0 else []
        cost = (resp.usage.input_tokens * _IN_RATE
                + resp.usage.output_tokens * _OUT_RATE) / 1_000_000
    except Exception as exc:
        return {"error": f"Match failed: {exc}"}

    out = []
    for m in matches:
        b = lookup.get(m.get("lead_id"))
        if not b:
            continue
        out.append({
            "lead_id": b["id"],
            "name": b["name"],
            "phone": b["phone"],
            "score": m.get("score", 0),
            "reason": m.get("reason", ""),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return {"matches": out, "cost_usd": round(cost, 4),
            "buyers_scanned": len(lines)}


BUYER_TEXT_SYSTEM_PROMPT = """You write ONE short SMS from a wholesaler to an
INVESTOR BUYER on their list, teasing a property that fits the buyer's stated
criteria. Casual, direct, no hype words, under 300 characters, no em dashes.
Mention the location/type hook that matches THEIR criteria and ask if they
want the details. Sign off with the sender's first name. Output ONLY the SMS
text, nothing else. Never invent price, condition, or address details that
aren't in the property summary."""


def draft_buyer_text(buyer: dict, seller_lead: dict, interactions: list[dict],
                     reason: str, sender: str = "Rom") -> dict:
    """One-tap intro text to a matched buyer. Returns {message, cost_usd}."""
    user_msg = (
        f"BUYER: {buyer.get('name')} — their criteria fit because: {reason}\n\n"
        "PROPERTY SUMMARY:\n" + _seller_summary(seller_lead, interactions)
        + f"\n\nSender first name: {sender}\nWrite the SMS."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            system=BUYER_TEXT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        cost = (resp.usage.input_tokens * _IN_RATE
                + resp.usage.output_tokens * _OUT_RATE) / 1_000_000
        return {"message": (resp.content[0].text or "").strip(),
                "cost_usd": round(cost, 4)}
    except Exception as exc:
        return {"message": "", "error": str(exc), "cost_usd": 0.0}
