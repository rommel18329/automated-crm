from __future__ import annotations

from dataclasses import dataclass, field

from .ai_message_generator import generate_message, prepare_message_request, MessageResult


STRATEGY_ROTATION = ["casual follow-up", "value-based", "pattern break", "call attempt", "reset tone"]


@dataclass(slots=True)
class FollowupDraft:
    message: str
    objective: str
    strategy: str
    reasoning: str
    flags: list[str] = field(default_factory=list)
    tone: str = "warm"
    coaching: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _choose_objective(strong_intent: bool, ignored_texts: int) -> str:
    if strong_intent:
        return "move to close — they have shown buying signals, make next step easy"
    if ignored_texts >= 2:
        return "re-engage — pattern break, low pressure"
    return "advance conversation — get a reply that reveals their current thinking"


def _choose_strategy(last_strategy: str | None, ignored_texts: int) -> str:
    if ignored_texts >= 2:
        return "pattern break"
    if not last_strategy or last_strategy not in STRATEGY_ROTATION:
        return STRATEGY_ROTATION[0]
    idx = STRATEGY_ROTATION.index(last_strategy)
    return STRATEGY_ROTATION[(idx + 1) % len(STRATEGY_ROTATION)]


def generate_followup_message(
    lead: dict,
    last_message: str,
    ignored_texts: int,
    strong_intent: bool,
    conversation: list[dict] | None = None,
    extra_context: str = "",
    approval=None,
    prepared: dict | None = None,
) -> FollowupDraft:
    objective = _choose_objective(strong_intent, ignored_texts)
    strategy = _choose_strategy(lead.get("last_strategy_used"), ignored_texts)

    result: MessageResult = generate_message(
        lead=lead,
        conversation=conversation or [],
        objective=objective,
        strategy=strategy,
        extra_context=extra_context,
        approval=approval,
        prepared=prepared,
    )
    reasoning = (
        f"Objective: {objective} | Strategy: {strategy} | Tone: {result.tone}"
    )
    return FollowupDraft(
        message=result.message,
        objective=objective,
        strategy=strategy,
        reasoning=reasoning,
        flags=result.flags,
        tone=result.tone,
        coaching=result.coaching,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )


def prepare_followup_request(lead: dict, last_message: str, ignored_texts: int,
                             strong_intent: bool, conversation: list[dict] | None = None,
                             extra_context: str = "") -> dict:
    objective = _choose_objective(strong_intent, ignored_texts)
    strategy = _choose_strategy(lead.get("last_strategy_used"), ignored_texts)
    return prepare_message_request(lead, conversation or [], objective, strategy, extra_context)
