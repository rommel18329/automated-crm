"""Streamlit price-approval step for every paid AI action.

The first click prices the exact prompt with Anthropic's free token counter.
The second click runs it with that quote. Nothing paid runs without the
second click, and ai_budget refuses a call whose prompt changed after pricing.
"""
from __future__ import annotations

import streamlit as st

from crm_engine.ai_budget import (AIApprovalError, AIBudgetError, AIQuote, daily_limit,
                                  quote_lines, quote_request, set_daily_limit, usage_summary)


def _is_request(value) -> bool:
    return isinstance(value, dict) and "system" in value and "messages" in value


def _usd(value: float, places: int = 4) -> str:
    # Streamlit renders text between two "$" as LaTeX; escape so amounts show as money.
    return f"\\${value:.{places}f}"


def priced_action(key: str, label: str, prepare, run, *, area=None, help: str | None = None,
                  button_type: str = "secondary", use_container_width: bool = False,
                  button_label: str | None = None):
    """Two-step paid AI button.

    prepare() returns one request dict, a list of them, or anything else when no
    paid call is needed (run(None, value) then runs right away).
    run(approval, prepared) does the work; approval is one AIQuote, or a list of
    them matching a list of requests. Returns run()'s result on the script run
    where it executed, otherwise None.
    """
    area = area or st
    state_key = f"{key}__ai_quote"
    pending = st.session_state.get(state_key)
    if pending is None:
        if area.button(button_label or f"{label} · check price", key=f"{key}__price", type=button_type,
                       use_container_width=use_container_width,
                       help=help or "Counts tokens for free and shows the price. Nothing paid runs yet."):
            try:
                with st.spinner("Pricing…"):
                    prepared = prepare()
                    if isinstance(prepared, list) and prepared and all(_is_request(r) for r in prepared):
                        quotes = [quote_request(r).to_dict() for r in prepared]
                    elif _is_request(prepared):
                        quotes = [quote_request(prepared).to_dict()]
                    else:
                        return run(None, prepared)
            except Exception as exc:
                area.error(f"Could not price this AI action: {exc}")
                return None
            st.session_state[state_key] = {"prepared": prepared, "quotes": quotes}
            st.rerun()
        return None

    quotes = [AIQuote.from_value(q) for q in pending["quotes"]]
    ceiling = sum(q.hard_ceiling_usd for q in quotes)
    expected = sum(q.expected_total_usd for q in quotes)
    remaining = usage_summary()["remaining_usd"]
    box = st.container(border=True)
    calls = f" across {len(quotes)} calls" if len(quotes) > 1 else ""
    box.markdown(f"**🤖 {label}**: expected **{_usd(expected)}**, max **{_usd(ceiling)}**{calls}")
    box.caption(f"{_usd(remaining)} left of today's {_usd(daily_limit(), 2)} AI limit. This prices the content "
                "as it was when you clicked. If you changed it, cancel and check the price again.")
    with box.expander("Price details"):
        for q in quotes:
            for line in quote_lines(q):
                st.caption(line.replace("$", "\\$"))
    over = ceiling > remaining + 1e-9
    if over:
        box.warning("This would go over today's AI limit. Raise it under AI spend in the sidebar, or cancel.")
    approve_col, cancel_col = box.columns(2)
    if approve_col.button(f"Approve & run (max {_usd(ceiling)})", key=f"{key}__approve",
                          type="primary", disabled=over, use_container_width=True):
        st.session_state.pop(state_key, None)
        approval = quotes if isinstance(pending["prepared"], list) else quotes[0]
        try:
            with st.spinner(f"{label}…"):
                return run(approval, pending["prepared"])
        except (AIApprovalError, AIBudgetError) as exc:
            box.error(str(exc).replace("$", "\\$"))
            return None
    if cancel_col.button("Cancel", key=f"{key}__cancel", use_container_width=True):
        st.session_state.pop(state_key, None)
        st.rerun()
    return None


def budget_panel() -> None:
    """Today's and this month's AI spend, plus the daily limit."""
    summary = usage_summary()
    st.caption(f"🤖 AI today {_usd(summary['today_usd'])} of {_usd(summary['daily_limit_usd'], 2)} "
               f"· month {_usd(summary['month_usd'])}")
    with st.expander("AI spend & limit"):
        new_limit = st.number_input("Daily AI limit ($)", min_value=0.0, step=0.25,
                                    value=float(summary["daily_limit_usd"]), key="ai_daily_limit_input")
        if st.button("Save limit", key="ai_daily_limit_save"):
            set_daily_limit(new_limit)
            st.rerun()
        for feature, cost in sorted(summary["by_feature"].items(), key=lambda kv: -kv[1]):
            st.caption(f"{feature}: {_usd(cost)} this month")
        st.caption("Every paid AI call shows its price and waits for your approval. "
                   "Nothing runs in the background.")
