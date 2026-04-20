from __future__ import annotations

from openai import OpenAI

client = OpenAI()


DEFAULT_FALLBACK = "Hey, just checking in—are you still considering selling the property?"


def generate_message(lead: dict, objective: str, strategy: str, last_message: str) -> str:
    prompt = f"""
You are writing a single SMS follow-up for a real-estate seller lead.

Lead context:
- Name: {lead.get('name', 'Unknown')}
- Status: {lead.get('status', 'warm')}
- Motivation score: {lead.get('motivation_score', 'unknown')}
- Timeline: {lead.get('timeline', 'unknown')}
- Touch count: {lead.get('touch_count', 0)}

Conversation context:
- Last message sent: {last_message or 'None'}
- Last strategy used: {lead.get('last_strategy_used', 'none')}

Objective: {objective}
Strategy: {strategy}

Tone rules:
- human
- conversational
- not salesy
- short SMS style
- mirror seller tone
- no emojis unless natural
- no corporate language

Output constraints:
- 1 to 3 sentences max
- plain text only
- no markdown
- no bullet points
""".strip()

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
        )
        text = (response.output_text or "").strip()
        if not text:
            return DEFAULT_FALLBACK
        # Keep output strictly concise for SMS.
        sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
        if len(sentences) > 3:
            text = ". ".join(sentences[:3]).strip() + "."
        return text
    except Exception:
        return DEFAULT_FALLBACK
