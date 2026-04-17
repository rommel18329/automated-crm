from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from . import db
from .followup_engine import FollowupDraft, generate_followup_message

URGENT_KEYWORDS = ["asap", "urgent", "quick", "immediately", "this week", "today", "deadline"]
PRICE_KEYWORDS = ["price", "offer", "cash", "number", "how much"]


@dataclass(slots=True)
class LeadEvaluation:
    lead_id: int
    should_escalate_hot: bool
    intent_signals: list[str]
    suggested_status: str
    next_action_date: str | None
    ghosting_signal: str | None
    touch_message: str


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value).date()


def detect_intent_signals(lead: dict[str, Any], interactions: list[dict[str, Any]]) -> list[str]:
    signals: list[str] = []
    joined = " ".join(i["content"].lower() for i in interactions if i["direction"] == "inbound")

    if lead.get("timeline_days") is not None and lead["timeline_days"] <= 30 and lead["motivation_score"] >= 6:
        signals.append("timeline<=30_and_motivation>=6")
    if any(k in joined for k in PRICE_KEYWORDS):
        signals.append("asks_price")
    if any(k in joined for k in URGENT_KEYWORDS):
        signals.append("urgency_language")

    inbound_count = sum(1 for i in interactions if i["direction"] == "inbound")
    if inbound_count >= 2:
        signals.append("multiple_replies")

    # fast reply proxy: inbound exists on same day as outbound
    outbound_dates = {
        datetime.fromisoformat(i["timestamp"]).date()
        for i in interactions
        if i["direction"] == "outbound"
    }
    fast_replies = [
        i
        for i in interactions
        if i["direction"] == "inbound" and datetime.fromisoformat(i["timestamp"]).date() in outbound_dates
    ]
    if fast_replies:
        signals.append("fast_replies")

    return signals


def ignored_outbound_texts(interactions: list[dict[str, Any]]) -> int:
    sorted_interactions = sorted(interactions, key=lambda x: x["timestamp"])
    count = 0
    for item in reversed(sorted_interactions):
        if item["direction"] == "inbound":
            break
        if item["direction"] == "outbound" and item["type"] == "text":
            count += 1
    return count


def last_outbound_was_call(interactions: list[dict[str, Any]]) -> bool:
    if not interactions:
        return False
    latest = max(interactions, key=lambda x: x["timestamp"])
    return latest["direction"] == "outbound" and latest["type"] == "call"


def compute_next_action_date(lead: dict[str, Any], interactions: list[dict[str, Any]]) -> str | None:
    if lead["status"] == "hot":
        return date.today().isoformat()
    ignored = ignored_outbound_texts(interactions)
    if lead["status"] == "cold":
        days = 21 if ignored < 2 else 28
    else:  # warm
        days = 2 if ignored == 0 else 4
    return (date.today() + timedelta(days=days)).isoformat()


def evaluate_lead(lead: dict[str, Any], interactions: list[dict[str, Any]]) -> LeadEvaluation:
    signals = detect_intent_signals(lead, interactions)
    escalate = bool(signals)

    ignored_texts = ignored_outbound_texts(interactions)
    ghosting: str | None = None
    if ignored_texts >= 3:
        ghosting = "3_texts_ignored_suggest_call"
    if ignored_texts >= 1 and last_outbound_was_call(interactions):
        ghosting = "call_ignored_pause_and_decide_keep_warm_or_downgrade"

    suggested = lead["status"]
    if escalate and lead["status"] != "hot":
        suggested = "hot"

    next_action = compute_next_action_date({**lead, "status": suggested}, interactions)

    touch_msg = f"We are at {lead['touch_count']} touches. Most deals convert at 5–7+ touches."
    return LeadEvaluation(
        lead_id=lead["id"],
        should_escalate_hot=escalate,
        intent_signals=signals,
        suggested_status=suggested,
        next_action_date=next_action,
        ghosting_signal=ghosting,
        touch_message=touch_msg,
    )


def generate_call_brief(lead: dict[str, Any], evaluation: LeadEvaluation, interactions: list[dict[str, Any]]) -> dict[str, Any]:
    last_inbound = next((i for i in sorted(interactions, key=lambda x: x["timestamp"], reverse=True) if i["direction"] == "inbound"), None)
    summary = lead["timeline"]
    why_hot = ", ".join(evaluation.intent_signals) if evaluation.intent_signals else "manual hot status"

    return {
        "lead_id": lead["id"],
        "lead_name": lead["name"],
        "situation_summary": summary,
        "why_hot": why_hot,
        "objective": "Book appointment or secure verbal commitment to next concrete step",
        "three_questions": [
            "What timeline feels realistic for you if terms are right?",
            "What outcome matters most: speed, certainty, or net price?",
            "What would make you feel comfortable moving forward this week?",
        ],
        "closing_suggestions": [
            "Offer a simple next-step choice (today vs tomorrow).",
            "Confirm exact concern and reflect it back before proposing terms.",
            "Use assumptive close: 'Let's lock a quick walkthrough call at 4:30.'",
        ],
        "last_inbound": last_inbound["content"] if last_inbound else "No inbound yet",
    }


def _priority_score(lead: dict[str, Any], eval_: LeadEvaluation) -> int:
    signal_weight = len(eval_.intent_signals) * 8
    timeline_weight = max(0, 30 - (lead.get("timeline_days") or 999))
    return (lead["motivation_score"] * 6) + signal_weight + timeline_weight + lead["deal_probability"]


def build_call_list(leads_with_eval: list[tuple[dict[str, Any], LeadEvaluation]]) -> list[dict[str, Any]]:
    hot = [(l, e) for l, e in leads_with_eval if e.suggested_status == "hot"]
    sorted_hot = sorted(hot, key=lambda t: _priority_score(*t), reverse=True)
    return [
        {
            "lead_id": l["id"],
            "name": l["name"],
            "phone": l["phone"],
            "priority_score": _priority_score(l, e),
            "intent_signals": e.intent_signals,
        }
        for l, e in sorted_hot
    ]


def build_followup_queue(leads_with_eval: list[tuple[dict[str, Any], LeadEvaluation]], all_interactions: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    today = date.today().isoformat()
    for lead, evaluation in leads_with_eval:
        if evaluation.suggested_status == "hot":
            continue
        next_action = lead.get("next_action_date") or today
        if next_action > today:
            continue
        interactions = all_interactions.get(lead["id"], [])
        ignored = ignored_outbound_texts(interactions)
        strong_intent = bool(evaluation.intent_signals)
        last_outbound = next((i for i in interactions if i["direction"] == "outbound"), None)
        suggestion: FollowupDraft = generate_followup_message(
            lead=lead,
            last_message=last_outbound["content"] if last_outbound else "",
            ignored_texts=ignored,
            strong_intent=strong_intent,
        )
        queue.append(
            {
                "lead_id": lead["id"],
                "lead_name": lead["name"],
                "last_message": last_outbound["content"] if last_outbound else "",
                "new_message": suggestion.message,
                "objective": suggestion.objective,
                "reasoning": suggestion.reasoning,
                "strategy": suggestion.strategy,
                "options": ["approve", "edit", "skip", "call instead"],
                "touch_notice": evaluation.touch_message,
                "ghosting_signal": evaluation.ghosting_signal,
            }
        )
    return queue


def post_interaction_options() -> list[str]:
    return ["keep warm", "upgrade to hot", "downgrade to cold"]


def performance_insights(summary: dict[str, Any]) -> dict[str, Any]:
    by_touch = summary["by_touch"]
    by_type = summary["by_type"]

    touch_rates = [
        {
            "touch_number": r["touch_number"],
            "response_rate": (r["replied"] / r["sent"]) if r["sent"] else 0,
        }
        for r in by_touch
    ]

    best_type = by_type[0]["message_type"] if by_type else None
    return {
        "response_rate_by_touch": touch_rates,
        "best_message_type": best_type,
        "timing_hint": "Schedule follow-ups near prior response windows (hour/day clusters).",
    }


def evaluate_all_leads(db_path=db.DB_PATH) -> dict[str, Any]:
    leads = db.fetch_leads(db_path)
    all_interactions = {lead["id"]: db.fetch_interactions(lead["id"], db_path) for lead in leads}
    leads_with_eval: list[tuple[dict[str, Any], LeadEvaluation]] = []

    for lead in leads:
        eval_ = evaluate_lead(lead, all_interactions.get(lead["id"], []))
        leads_with_eval.append((lead, eval_))
        db.update_lead(
            lead["id"],
            {
                "status": eval_.suggested_status,
                "next_action_date": eval_.next_action_date,
            },
            db_path,
        )

    followups = build_followup_queue(leads_with_eval, all_interactions)
    call_list = build_call_list(leads_with_eval)
    call_briefs = {
        lead["id"]: generate_call_brief(lead, eval_, all_interactions.get(lead["id"], []))
        for lead, eval_ in leads_with_eval
        if eval_.suggested_status == "hot"
    }

    counts = Counter(eval_.suggested_status for _, eval_ in leads_with_eval)
    risk_leads = [
        {"lead_id": lead["id"], "name": lead["name"], "risk": eval_.ghosting_signal}
        for lead, eval_ in leads_with_eval
        if eval_.ghosting_signal
    ]

    return {
        "summary": {
            "hot": counts.get("hot", 0),
            "warm": counts.get("warm", 0),
            "cold": counts.get("cold", 0),
            "follow_ups": len(followups),
            "risk_leads": len(risk_leads),
        },
        "followup_queue": followups,
        "call_list": call_list,
        "call_briefs": call_briefs,
        "risk_leads": risk_leads,
    }
