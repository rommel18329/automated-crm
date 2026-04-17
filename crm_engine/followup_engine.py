from __future__ import annotations

from dataclasses import dataclass

from .ai_message_generator import generate_message


STRATEGY_ROTATION = ["casual follow-up", "value-based", "pattern break", "call attempt", "reset tone"]


@dataclass(slots=True)
class FollowupDraft:
    message: str
    objective: str
    strategy: str
    reasoning: str


def _choose_objective(strong_intent: bool, ignored_texts: int) -> str:
    if strong_intent:
        return "move to call"
    if ignored_texts >= 2:
        return "re-engage"
    return "get reply"


def _choose_strategy(last_strategy: str | None, ignored_texts: int) -> str:
    if ignored_texts >= 2:
        return "pattern break"
    if not last_strategy or last_strategy not in STRATEGY_ROTATION:
        return STRATEGY_ROTATION[0]
    idx = STRATEGY_ROTATION.index(last_strategy)
    return STRATEGY_ROTATION[(idx + 1) % len(STRATEGY_ROTATION)]


def generate_followup_message(lead: dict, last_message: str, ignored_texts: int, strong_intent: bool) -> FollowupDraft:
    objective = _choose_objective(strong_intent, ignored_texts)
    strategy = _choose_strategy(lead.get("last_strategy_used"), ignored_texts)
    msg = generate_message(
        lead=lead,
        objective=objective,
        strategy=strategy,
        last_message=last_message,
    )
    reasoning = (
        f"Objective={objective}; strategy={strategy}; "
        "AI generated message in approval mode using lead context and conversation context."
    )
    return FollowupDraft(message=msg, objective=objective, strategy=strategy, reasoning=reasoning)
