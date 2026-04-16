from __future__ import annotations

from dataclasses import dataclass

ROTATION = ["casual", "value-based", "pattern-break", "call-attempt", "reset-tone"]


@dataclass(slots=True)
class MessageSuggestion:
    objective: str
    tone: str
    strategy: str
    reasoning: str
    message: str


def _next_strategy(last_strategy_used: str | None, ignored_texts: int) -> str:
    if ignored_texts >= 2:
        return "pattern-break"
    if not last_strategy_used or last_strategy_used not in ROTATION:
        return ROTATION[0]
    idx = ROTATION.index(last_strategy_used)
    return ROTATION[(idx + 1) % len(ROTATION)]


def generate_message_suggestion(lead: dict, last_message: str, ignored_texts: int, strong_intent: bool) -> MessageSuggestion:
    strategy = _next_strategy(lead.get("last_strategy_used"), ignored_texts)
    objective = "move to call" if strong_intent else "get reply"
    tone = "conversational + human + mirrors seller tone"

    if strategy == "pattern-break":
        msg = "Should I stop reaching out, or are you still open to selling if the numbers make sense?"
        reason = "2-3 ignored messages detected; pattern break to re-engage."
        objective = "re-engage"
    elif strategy == "value-based":
        msg = "Quick one—would a simple, no-pressure offer range help you decide your next step?"
        reason = "Value-based nudge after prior touch to increase response probability."
    elif strategy == "call-attempt":
        msg = "Would a 5-minute call later today be easiest so I can answer everything quickly?"
        reason = "Rotation suggests call attempt to reduce text friction."
        objective = "move to call"
    elif strategy == "reset-tone":
        msg = "No rush on timing. If plans changed, I can check back when it fits you better."
        reason = "Reset tone to lower pressure and preserve relationship."
    else:
        msg = "Hey {name}, just checking in—any updates on your plans for the property?".format(name=lead["name"].split()[0])
        reason = "Casual follow-up to prompt a low-friction reply."

    if strong_intent:
        msg += " If it's easier, we can jump on a quick call today."
        reason += " Intent signals are strong, call transition suggested."

    if last_message:
        reason += " Last outbound message used as context for continuity."

    return MessageSuggestion(objective=objective, tone=tone, strategy=strategy, reasoning=reason, message=msg)
