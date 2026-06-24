"""AI-powered contact import for the dialer.

Takes a messy raw blob (pasted from a spreadsheet, a list of addresses,
skip-trace output, texts, whatever) and uses Claude to extract clean
contact rows: name, phone, address. The caller previews the rows before
anything is written to crm.db.

Model: claude-opus-4-8 (per project decision — accuracy over cost for the
double-check-once import step).
"""
from __future__ import annotations

import csv
import io
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
MAX_TOKENS = 16384       # large lists need room; 4096 truncated big imports
CHUNK_CHARS = 12000      # split big pastes so each batch fits one API round-trip

SYSTEM_PROMPT = """You extract contact records from messy real estate lead lists.

The input is raw text a wholesaler pasted from somewhere: a spreadsheet, skip-trace
output, a phone's contact export, handwritten notes, address lists, etc. Formats vary
wildly and may be inconsistent within one paste.

Your job: pull out every distinct person/contact and return clean structured rows.

For each contact extract:
- name: the person's name. If only a property address is present with no person, use "" (empty).
- phones: a list of ALL phone numbers for the contact, in the order they appear
  (leftmost / first phone column first). Keep up to 5. Digits only or any format —
  you don't need to format them. The FIRST number is the primary.
  If a contact has no phone at all, skip that contact entirely (we can't dial them).
- address: the property address if present, else "".

RULES:
- One row per dialable contact. A contact MUST have a phone or be skipped.
- Do not invent data. If a field isn't present, leave it "".
- Do not deduplicate aggressively — if two rows clearly have different phone numbers, keep both.
- Strip obvious junk (column headers like "Name,Phone,Address", "DO NOT CALL" lines become a note not a name).
- Names: Title Case a clearly-lowercase name, but leave already-formatted names alone.

Return ONLY a valid JSON array, no extra text, no markdown fences:
[{"name": "...", "phones": ["...", "..."], "address": "..."}, ...]
If you find no dialable contacts, return []."""


@dataclass
class ImportedContact:
    name: str
    phone: str         # normalized primary number, XXX-XXX-XXXX (== phones[0])
    address: str
    raw_phone: str     # raw primary as returned by the model, for debugging
    phones: list[str]  # all numbers for this contact, in order, up to 5


def normalize_phone(raw: str) -> str | None:
    """Match the CRM's stored format: XXX-XXX-XXXX. Returns None if not a
    valid 10-digit US number."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def _chunk_text(raw_text: str, max_chars: int) -> list[str]:
    """Split a big paste into line-aligned batches no larger than max_chars.
    Splitting on line boundaries keeps individual contact rows intact."""
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in raw_text.splitlines():
        if cur and cur_len + len(line) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks or [raw_text]


def _call_claude_batch(text: str) -> tuple[list[dict], str | None]:
    """Extract rows from one batch. Returns (rows, error_message).

    The batch is small enough that the JSON array fits in one response. We pull
    the array out of whatever the model returns (tolerating any stray preamble
    or markdown fences) rather than relying on assistant prefill, which
    claude-opus-4-8 does not support.
    """
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    out = (resp.content[0].text or "")
    out = re.sub(r"^```(?:json)?|```$", "", out, flags=re.MULTILINE).strip()
    start = out.find("[")
    end = out.rfind("]") + 1
    if start == -1:
        return [], f"AI returned no JSON. Raw: {out[:200]}"
    try:
        return json.loads(out[start:end]), None
    except Exception:
        # Likely truncated mid-array (hit max_tokens). Salvage complete objects
        # by closing the array after the last full "}".
        last = out.rfind("}")
        if last != -1:
            try:
                return json.loads(out[start:last + 1] + "]"), None
            except Exception:
                pass
        stop = getattr(resp, "stop_reason", "?")
        return [], f"AI returned unparseable JSON (stop_reason={stop}). Raw: {out[:200]}"


def _rows_to_contacts(rows: list[dict]) -> tuple[list[ImportedContact], list[str]]:
    """Turn raw {name, phones/phone, address} dicts into clean, phone-normalized,
    cross-contact-deduplicated ImportedContacts. Shared by the table parser and
    the AI fallback so both behave identically."""
    contacts: list[ImportedContact] = []
    warnings: list[str] = []
    seen_phones: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        address = str(row.get("address", "")).strip()

        # Accept either the new "phones" list or a legacy single "phone".
        raw_list = row.get("phones")
        if not isinstance(raw_list, list):
            raw_list = [row.get("phone", "")]

        norm_phones: list[str] = []
        for rp in raw_list:
            n = normalize_phone(str(rp))
            if n and n not in norm_phones:   # keep order, drop dupes within a contact
                norm_phones.append(n)
            if len(norm_phones) >= 5:
                break

        if not norm_phones:
            warnings.append(f"Skipped (no valid phone): {name or address or '??'}")
            continue

        primary = norm_phones[0]
        if primary in seen_phones:
            warnings.append(f"Duplicate phone {primary} ({name or address}) — kept first only.")
            continue
        seen_phones.add(primary)
        contacts.append(ImportedContact(
            name=name or "(no name)", phone=primary, address=address,
            raw_phone=str(raw_list[0]), phones=norm_phones,
        ))

    return contacts, warnings


# Header cells that hold an actual dialable number: "Phone", "Phone 2" ... but
# NOT "Phone Label" / "Phone 2 Type" etc.
_PHONE_HEADER = re.compile(r"^phone(\s*\d+)?$")


def _parse_table(raw_text: str) -> list[dict] | None:
    """Parse a pasted spreadsheet (tab- or comma-delimited, with a header row)
    directly — no API call. Returns row dicts {name, phones, address}, or None
    if the text doesn't look like a table with a recognizable header (in which
    case the caller falls back to the AI parser).

    Handles the MultiStackMotivation / skip-trace export layout: First/Last
    Name, Phone + Phone 2..5, Address1/City/State/Zip. Quoted cells with
    embedded newlines (e.g. call-log Notes) are handled by the csv module.
    """
    delimiter = "\t" if "\t" in raw_text[:2000] else ","
    try:
        reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
        table = [r for r in reader if any(c.strip() for c in r)]
    except Exception:
        return None
    if len(table) < 2:
        return None

    header = [c.strip().lower() for c in table[0]]
    col = {}                      # normalized header name -> first column index
    phone_idx: list[int] = []     # indices of dialable phone columns, in order
    for i, h in enumerate(header):
        if _PHONE_HEADER.match(h):
            phone_idx.append(i)
        col.setdefault(h, i)

    # Only treat this as a table if we found a real phone column AND a name.
    has_name = "first name" in col or "name" in col or "last name" in col
    if not phone_idx or not has_name:
        return None

    def cell(row: list[str], key: str) -> str:
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    rows: list[dict] = []
    for row in table[1:]:
        if "name" in col and "first name" not in col:
            name = cell(row, "name")
        else:
            name = (cell(row, "first name") + " " + cell(row, "last name")).strip()

        phones = [row[i].strip() for i in phone_idx
                  if i < len(row) and row[i].strip()]

        street = cell(row, "address1") or cell(row, "address")
        addr2 = cell(row, "address2")
        if addr2:
            street = (street + " " + addr2).strip()
        loc = ", ".join(p for p in [
            cell(row, "city"),
            " ".join(x for x in [cell(row, "state"), cell(row, "zip")] if x),
        ] if p)
        address = ", ".join(p for p in [street, loc] if p)

        rows.append({"name": name, "phones": phones, "address": address})
    return rows


def parse_contacts(raw_text: str, mode: str = "auto") -> tuple[list[ImportedContact], list[str]]:
    """Extract dialable contacts from a pasted blob and return (contacts, warnings).

    contacts: clean, phone-normalized, de-duplicated by phone.
    warnings: human-readable notes about rows that were dropped/merged.

    mode:
      "auto"  — try table parse first; fall back to AI if not a table (default).
      "table" — table parse only; error if format not recognized.
      "ai"    — always send through Claude, skip table parse.
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return [], ["Nothing pasted."]

    if mode in ("auto", "table"):
        table_rows = _parse_table(raw_text)
        if table_rows is not None:
            return _rows_to_contacts(table_rows)
        if mode == "table":
            return [], ["Could not detect a table with name + phone columns. "
                        "Try 'Auto' or 'AI cleanup' mode instead."]

    chunks = _chunk_text(raw_text, CHUNK_CHARS)
    rows: list[dict] = []
    warnings: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        label = f"Batch {i}/{len(chunks)}: " if len(chunks) > 1 else ""
        try:
            batch_rows, err = _call_claude_batch(chunk)
        except Exception as exc:
            warnings.append(f"{label}AI import failed: {exc}")
            continue
        if err:
            warnings.append(f"{label}{err}")
            continue
        rows.extend(batch_rows)

    contacts, row_warnings = _rows_to_contacts(rows)
    return contacts, warnings + row_warnings
