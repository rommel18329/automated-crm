# update_timelines.py
# Reads every lead's notes, uses GPT to extract timeline + situation tags,
# and writes them back to crm.db.
# Run: python update_timelines.py
# Safe to re-run (idempotent on timeline_days; appends new tags without duplicating).

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crm_engine import db
from crm_engine.db import get_conn

DB_PATH = PROJECT_ROOT / "crm.db"
client = OpenAI()

SYSTEM_PROMPT = """You are a real-estate acquisitions analyst reviewing call/text notes for a motivated seller lead.

Given the notes, extract:
1. timeline_days: integer estimate of how many days until they want to close/sell
   - Use null if genuinely unknown or no timeline discussed
   - "this month" / "ASAP" / "urgent" = 30
   - "spring 2026" from a note dated ~Oct 2025 = 150
   - "next year" = 365
   - "few months" = 90
   - "couple weeks" = 14
2. timeline_desc: short phrase (under 12 words) describing the timeline
   - Examples: "Wants to close spring 2026", "No timeline established",
     "Motivated - needs to sell ASAP", "Waiting on attorney clearance",
     "Undecided - reviewing options", "Contract timeline 30 days"
   - If unknown: "Unknown timeline"
3. tags: array of compact kebab-case situation tags extracted from notes
   - Property condition: "needs-new-floors", "roof-needs-work", "needs-electrical-update",
     "has-new-appliances", "foundation-issues", "basement-unfinished", "new-windows"
   - Price/offer: "asking-85k", "offer-at-100k", "cash-offer", "seller-finance-discussed",
     "price-firm", "open-to-negotiation", "above-market-offer"
   - Seller situation: "probate", "estate-sale", "divorce", "vacant", "absentee-owner",
     "tenants-in-place", "tired-landlord", "behind-on-taxes", "inherited-property"
   - Deal status: "under-contract", "inspection-period", "attorney-involved",
     "multiple-investors-interested", "partner-needs-to-sign", "reviewing-terms"
   - Contact status: "no-answer-multiple-attempts", "responsive", "going-dark",
     "requested-callback", "left-voicemail"
   - Only include tags clearly supported by the notes (max 8 tags)

Return ONLY valid JSON with no extra text:
{"timeline_days": <int or null>, "timeline_desc": "<string>", "tags": ["tag1", "tag2"]}"""


def parse_timeline_parts(timeline_text: str):
    """Returns (prefix, address, situation, notes) from compound timeline string."""
    timeline = timeline_text or ""
    address, situation, notes = "", "", ""
    if "Address:" in timeline:
        before_addr, after_addr = timeline.split("Address:", 1)
        timeline = before_addr.strip().rstrip(".")
        if "Situation:" in after_addr:
            addr_part, after_sit = after_addr.split("Situation:", 1)
            address = addr_part.strip().rstrip(".")
            after = after_sit
        else:
            address = after_addr.strip()
            after = ""
    else:
        after = timeline
    if "Situation:" in after:
        _, tail = after.split("Situation:", 1)
        if "Notes:" in tail:
            sit_part, notes_part = tail.split("Notes:", 1)
            situation = sit_part.strip().rstrip(".")
            notes = notes_part.strip()
        else:
            situation = tail.strip()
    return timeline, address, situation, notes


def analyse_lead(lead: dict, notes: list[dict]) -> dict:
    """Call GPT to extract timeline and situation tags from notes."""
    if not notes:
        return {"timeline_days": None, "timeline_desc": "Unknown timeline", "tags": []}

    # Build note summary (most recent first, cap at ~3000 chars to keep tokens low)
    note_lines = []
    for n in sorted(notes, key=lambda x: x["timestamp"], reverse=True):
        line = f"[{n['timestamp'][:10]}] {n['content']}"
        note_lines.append(line)

    notes_text = "\n".join(note_lines)
    if len(notes_text) > 3500:
        notes_text = notes_text[:3500] + "\n...[truncated]"

    user_msg = (
        f"Lead: {lead['name']} | Status: {lead['status']} | "
        f"Total calls: {lead['touch_count']}\n\n"
        f"Notes (newest first):\n{notes_text}"
    )

    try:
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = (resp.output_text or "").strip()
        # Strip any markdown fences
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        result = json.loads(raw)
        # Validate
        result["timeline_days"] = (
            int(result["timeline_days"]) if result.get("timeline_days") is not None else None
        )
        result["timeline_desc"] = str(result.get("timeline_desc", "Unknown timeline"))[:120]
        result["tags"] = [str(t) for t in result.get("tags", [])][:8]
        return result
    except Exception as e:
        print(f"    GPT error for {lead['name']}: {e}")
        return {"timeline_days": None, "timeline_desc": "Unknown timeline", "tags": []}


def build_new_timeline(lead: dict, analysis: dict) -> str:
    """Reconstruct the timeline field with new prefix and merged tags."""
    _, address, existing_situation, existing_notes = parse_timeline_parts(
        lead["timeline"] or ""
    )

    # Merge existing situation tags with new GPT tags
    existing_tags = [t.strip() for t in re.split(r"[,;]+", existing_situation) if t.strip()]
    new_tags = analysis.get("tags", [])

    # Deduplicate: prefer new tags, keep existing ones not covered by new
    all_tags_set = set(t.lower() for t in new_tags)
    merged = list(new_tags)
    for et in existing_tags:
        if et.lower() not in all_tags_set:
            merged.append(et)

    situation_str = ", ".join(merged) if merged else ""

    parts = [analysis["timeline_desc"]]
    if address:
        parts.append(f"Address: {address}")
    if situation_str:
        parts.append(f"Situation: {situation_str}")
    if existing_notes:
        parts.append(f"Notes: {existing_notes}")

    return ". ".join(parts)


def main():
    db.init_db(DB_PATH)
    leads = db.fetch_leads(DB_PATH)
    print(f"Processing {len(leads)} leads...\n")

    updated = 0
    errors = 0

    for i, lead in enumerate(leads, 1):
        notes = [
            n for n in db.fetch_interactions(lead["id"], DB_PATH) if n["type"] == "note"
        ]
        print(f"[{i:2d}/{len(leads)}] {lead['name']} — {len(notes)} notes", end=" ... ")

        analysis = analyse_lead(lead, notes)

        new_timeline = build_new_timeline(lead, analysis)
        update_fields = {
            "timeline": new_timeline,
            "timeline_days": analysis["timeline_days"],
        }

        # Also bump motivation for high-signal keywords in tags
        high_signal = {"asap", "urgent", "motivated", "30-days", "needs-to-sell"}
        if any(t in " ".join(analysis["tags"]) for t in high_signal):
            if lead["motivation_score"] < 7:
                update_fields["motivation_score"] = min(10, lead["motivation_score"] + 1)

        db.update_lead(lead["id"], update_fields, DB_PATH)

        print(f"{analysis['timeline_desc']} ({analysis['timeline_days']} days) | tags: {analysis['tags']}")
        updated += 1

        # Respect rate limit (gpt-4o-mini allows ~500 RPM on free tier)
        if i % 10 == 0:
            time.sleep(1)

    print(f"\nDone. {updated} leads updated, {errors} errors.")


if __name__ == "__main__":
    main()
