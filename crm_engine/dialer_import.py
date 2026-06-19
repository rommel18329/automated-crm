"""AI-powered contact import for the dialer.

Takes a messy raw blob (pasted from a spreadsheet, a list of addresses,
skip-trace output, texts, whatever) and uses Claude to extract clean
contact rows: name, phone, address. The caller previews the rows before
anything is written to crm.db.

Model: claude-opus-4-8 (per project decision — accuracy over cost for the
double-check-once import step).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import anthropic

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You extract contact records from messy real estate lead lists.

The input is raw text a wholesaler pasted from somewhere: a spreadsheet, skip-trace
output, a phone's contact export, handwritten notes, address lists, etc. Formats vary
wildly and may be inconsistent within one paste.

Your job: pull out every distinct person/contact and return clean structured rows.

For each contact extract:
- name: the person's name. If only a property address is present with no person, use "" (empty).
- phone: the primary phone number. Digits only or any format — you don't need to format it.
  If multiple numbers exist for one contact, pick the most likely mobile/primary one.
  If no phone exists for a contact, skip that contact entirely (we can't dial them).
- address: the property address if present, else "".

RULES:
- One row per dialable contact. A contact MUST have a phone or be skipped.
- Do not invent data. If a field isn't present, leave it "".
- Do not deduplicate aggressively — if two rows clearly have different phone numbers, keep both.
- Strip obvious junk (column headers like "Name,Phone,Address", "DO NOT CALL" lines become a note not a name).
- Names: Title Case a clearly-lowercase name, but leave already-formatted names alone.

Return ONLY a valid JSON array, no extra text, no markdown fences:
[{"name": "...", "phone": "...", "address": "..."}, ...]
If you find no dialable contacts, return []."""


@dataclass
class ImportedContact:
    name: str
    phone: str        # normalized XXX-XXX-XXXX
    address: str
    raw_phone: str     # what the model returned, for debugging


def normalize_phone(raw: str) -> str | None:
    """Match the CRM's stored format: XXX-XXX-XXXX. Returns None if not a
    valid 10-digit US number."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def parse_contacts(raw_text: str) -> tuple[list[ImportedContact], list[str]]:
    """Run the raw blob through Claude and return (contacts, warnings).

    contacts: clean, phone-normalized, de-duplicated by phone.
    warnings: human-readable notes about rows that were dropped/merged.
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return [], ["Nothing pasted."]

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": raw_text}],
        )
        out = (resp.content[0].text or "").strip()
        out = re.sub(r"^```(?:json)?|```$", "", out, flags=re.MULTILINE).strip()
        start = out.find("[")
        end = out.rfind("]") + 1
        if start == -1 or end == 0:
            return [], [f"AI returned no contact list. Raw: {out[:200]}"]
        rows = json.loads(out[start:end])
    except Exception as exc:
        return [], [f"AI import failed: {exc}"]

    contacts: list[ImportedContact] = []
    warnings: list[str] = []
    seen_phones: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_phone = str(row.get("phone", "")).strip()
        name = str(row.get("name", "")).strip()
        address = str(row.get("address", "")).strip()

        norm = normalize_phone(raw_phone)
        if not norm:
            warnings.append(f"Skipped (no valid phone): {name or address or raw_phone or '??'}")
            continue
        if norm in seen_phones:
            warnings.append(f"Duplicate phone {norm} ({name or address}) — kept first only.")
            continue
        seen_phones.add(norm)
        contacts.append(ImportedContact(name=name or "(no name)", phone=norm,
                                        address=address, raw_phone=raw_phone))

    return contacts, warnings
