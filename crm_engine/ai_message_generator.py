from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import os
from dotenv import load_dotenv
import anthropic

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

DEFAULT_FALLBACK = "Hey, just wanted to follow up. Still a good time to connect on the property?"

SYSTEM_PROMPT = """You are an expert real estate acquisitions specialist and negotiation coach helping a wholesaler/investor close cash deals with motivated sellers.

Your job: analyze the full conversation history and generate ONE perfect SMS to send RIGHT NOW that moves the deal toward closing — without spooking the seller or losing momentum.

TIME GAP RULES (non-negotiable):
- If DAYS SINCE LAST CONTACT is 14–60 days: briefly acknowledge the gap ("been a while", "it's been a minute") — one natural phrase, not an apology.
- If DAYS SINCE LAST CONTACT is 60+ days: you MUST open with a genuine acknowledgment of the gap before anything else. Do NOT write as if it's the next day. Examples: "It's been a few months since we last talked", "I know it's been a while since we touched base". Keep it one sentence, warm, not groveling.
- If DAYS SINCE LAST CONTACT is under 14 days: no gap acknowledgment needed.
- Never use "I apologize for the delay" or "sorry for not reaching out sooner" — just acknowledge naturally.

DEAL PSYCHOLOGY PRINCIPLES:
- Sellers need to feel heard, not sold. Mirror their urgency level exactly.
- "Let me think about it" = unspoken objection. Draw it out gently, never push through it.
- Emotional sellers (inherited, divorce, financial stress) need acknowledgment BEFORE any business talk.
- Price anchoring: if they've named a number, don't attack it — ask what's driving it.
- After a strong offer or ask, silence is okay. Don't double-text too fast.
- Ghosting: don't chase desperately. One short pattern-break message, then go quiet.
- Stalling without reason = unspoken concern. Ask a simple open-ended question.
- When they're READY to move — remove ALL friction. Make the next step dead simple.
- Multiple offers or agents mentioned = light urgency, not desperation.
- Lawyers/attorneys involved = let them lead legal pace. Never rush paperwork.

STAGE GUIDELINES:
- Cold/first contact: warm and curious. One simple question. No pitch.
- Qualifying: get them talking about timeline and motivation. Ask, don't tell.
- Negotiating: find what they ACTUALLY need. Price is rarely the real issue.
- Near close: make next step crystal clear. One action, easy to say yes to.
- Stalled: pattern break — unexpected angle, different question, or go briefly silent.
- Re-engaging after silence: short, low-pressure. Reference something specific from before.

SMS TONE RULES:
- Sound like a real human investor, not a salesperson
- Conversational, warm, direct
- 1–3 sentences max
- No emojis unless the seller uses them first
- No corporate language ("leverage", "utilize", "circling back", "touch base")
- Match the seller's communication style (formal vs casual)
- Never start with "I hope this message finds you well"
- NEVER use em dashes, en dashes, or a hyphen used as punctuation to join two clauses (no "thing - thing"). Use a period or comma instead. Hyphens inside real words like "follow-up" or "3-year" are fine.
- NEVER use semicolons
- Avoid AI-sounding filler: "I wanted to reach out", "I understand that", "please don't hesitate to", "at your earliest convenience", "feel free to", "I hope this finds you well", "as always"
- No bullet points, numbered lists, or markdown formatting. Plain text only, like a real person texting from their phone.

Return ONLY valid JSON, no extra text:
{
  "message": "<the SMS to send>",
  "flags": ["<specific psychological observation or warning — max 5>"],
  "tone": "<warm|patient|urgent|pattern-break|re-engage>",
  "coaching": "<ONLY include this field if the agent asked a question in their context. Give a direct 1-2 sentence answer to their specific question — no fluff, no hedging. If they didn't ask a question, omit this field entirely.>"
}

FLAG EXAMPLES (be this specific):
- "⚠️ Seller mentioned attorney — let them control legal timeline, don't rush paperwork"
- "🔴 No response to last 2+ messages — one short pattern-break then go dark 2 weeks"
- "🟡 Price anchored at $X — acknowledge their position before moving on numbers"
- "🟢 Strong close signal — asking about closing date, ready to commit"
- "⚠️ Repeated stalling without clear reason — unspoken objection, ask open-ended question"
- "🔴 Emotional/inherited property — lead with empathy before any deal talk"
- "🟡 Seller has other interested parties — create mild urgency, not desperation"
- "🟢 Seller volunteering property details unprompted — high motivation, keep momentum"
- "⚠️ Long gap since last contact — reference something specific to re-establish rapport"
"""


@dataclass
class MessageResult:
    message: str
    flags: list[str] = field(default_factory=list)
    tone: str = "warm"
    coaching: str = ""


def _build_conversation_text(conversation: list[dict], max_msgs: int = 20) -> str:
    """Format the most recent messages for the prompt."""
    recent = sorted(conversation, key=lambda x: x["timestamp"])[-max_msgs:]
    lines = []
    for m in recent:
        who = "YOU" if m["direction"] == "outbound" else "SELLER"
        ts = m["timestamp"][:10]
        lines.append(f"[{ts}] {who}: {m['content'][:300]}")
    return "\n".join(lines) if lines else "No prior conversation."


def _strip_ai_tells(text: str) -> str:
    """Hard-strip dashes/AI-giveaway punctuation, even if the model ignores instructions."""
    # Em dash (U+2014), en dash (U+2013), horizontal bar (U+2015) — explicit codepoints
    text = re.sub(r"\s*[–—―]\s*", ", ", text)
    # HTML-encoded variants
    text = re.sub(r"\s*(?:&mdash;|&ndash;|&#8211;|&#8212;)\s*", ", ", text, flags=re.IGNORECASE)
    # A bare hyphen used as a clause separator (" - "), not inside a word like "follow-up"
    text = re.sub(r"(?<=\S)\s+-\s+(?=\S)", ", ", text)
    # Semicolons read as AI-formal; swap for a period and capitalize what follows
    text = re.sub(r";\s*(\w)", lambda m: ". " + m.group(1).upper(), text)
    # Collapse any double punctuation left behind (e.g. ",.")
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r",\s*,", ",", text)
    return text.strip()


def _days_since_last_contact(conversation: list[dict]) -> int | None:
    """Return days since the most recent message in the conversation, or None if no messages."""
    if not conversation:
        return None
    latest_ts = max(m["timestamp"] for m in conversation)
    try:
        last_dt = datetime.fromisoformat(latest_ts)
        return (datetime.utcnow() - last_dt).days
    except Exception:
        return None


def generate_message(
    lead: dict,
    conversation: list[dict],
    objective: str,
    strategy: str,
    extra_context: str = "",
) -> MessageResult:
    convo_text = _build_conversation_text(conversation)

    days_gap = _days_since_last_contact(conversation)
    if days_gap is not None:
        if days_gap >= 60:
            months = days_gap // 30
            gap_label = f"{months} month{'s' if months != 1 else ''} ({days_gap} days)"
            gap_note = f"\n⚠️ DAYS SINCE LAST CONTACT: {gap_label} — you MUST acknowledge this gap at the start of your message."
        elif days_gap >= 14:
            gap_note = f"\nDAYS SINCE LAST CONTACT: {days_gap} days — briefly acknowledge it's been a while."
        else:
            gap_note = f"\nDAYS SINCE LAST CONTACT: {days_gap} days."
    else:
        gap_note = ""

    extra_block = f"\nADDITIONAL CONTEXT FROM AGENT: {extra_context.strip()}" if extra_context and extra_context.strip() else ""

    user_msg = f"""LEAD PROFILE:
- Name: {lead.get('name', 'Unknown')}
- Status: {lead.get('status', 'warm')}
- Motivation score: {lead.get('motivation_score', 5)}/10
- Timeline: {lead.get('timeline', 'unknown')}
- Total touches: {lead.get('touch_count', 0)}
- Deal stage: {lead.get('conversation_stage', 'unknown').replace('_', ' ')}

OBJECTIVE: {objective}
STRATEGY: {strategy}{gap_note}{extra_block}

CONVERSATION HISTORY (oldest → newest):
{convo_text}

Now write the perfect next SMS to send."""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = (resp.content[0].text or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        # Extract just the first complete JSON object, ignoring any trailing text
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found in response")
        data = json.loads(raw[start:end])
        message = _strip_ai_tells(str(data.get("message", DEFAULT_FALLBACK)))
        coaching = _strip_ai_tells(str(data.get("coaching", "")))
        return MessageResult(
            message=message,
            flags=[str(f) for f in data.get("flags", [])[:5]],
            tone=str(data.get("tone", "warm")),
            coaching=coaching,
        )
    except Exception as exc:
        return MessageResult(
            message=DEFAULT_FALLBACK,
            flags=[f"🔴 AI generation failed: {exc}"],
        )
