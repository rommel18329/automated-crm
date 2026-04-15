from __future__ import annotations

from datetime import date

import streamlit as st

from crm_engine import db, engine

st.set_page_config(page_title="Warm Minimal Operator Dashboard", layout="wide")

THEME_CSS = """
<style>
:root {
  --bg: #F7F5F2;
  --card: #FFFFFF;
  --accent: #6B8F71;
  --highlight: #EDE7DD;
  --text: #2E2E2E;
}
html, body, [data-testid="stAppViewContainer"] {
  background-color: var(--bg);
  color: var(--text);
}
.block-container {padding-top: 1.1rem;}
.card {
  background: var(--card);
  border-radius: 18px;
  padding: 14px 16px;
  margin-bottom: 14px;
  box-shadow: 0 8px 24px rgba(46,46,46,0.06);
}
.metric {
  background: var(--highlight);
  border-radius: 14px;
  padding: 12px;
  text-align: center;
}
.badge {
  display: inline-block;
  border-radius: 12px;
  padding: 2px 10px;
  font-size: 0.8rem;
  background: #e4efe5;
  color: var(--accent);
}
.pill {
  display: inline-block;
  border-radius: 999px;
  padding: 4px 10px;
  margin: 0 6px 6px 0;
  background: #edf3ee;
  color: #3c5f42;
  font-size: 0.82rem;
}
.plant {font-size: 1.3rem; opacity: 0.9;}
</style>
"""


def parse_timeline_parts(timeline_text: str) -> tuple[str, str, str]:
    timeline = timeline_text or ""
    situation = ""
    notes = ""
    if "Situation:" in timeline:
        before, after = timeline.split("Situation:", 1)
        timeline = before.strip().rstrip(".")
        if "Notes:" in after:
            situation_part, notes_part = after.split("Notes:", 1)
            situation = situation_part.strip().rstrip(".")
            notes = notes_part.strip()
        else:
            situation = after.strip()
    return timeline, situation, notes


def human_intent_labels(signals: list[str]) -> list[str]:
    label_map = {
        "timeline<=30_and_motivation>=6": "Timeline < 30 days + High motivation (6+)",
        "asks_price": "Asked about price / offer",
        "urgency_language": "Urgency language detected",
        "multiple_replies": "Multiple replies",
        "fast_replies": "Fast replies",
    }
    return [label_map.get(s, s.replace("_", " ").title()) for s in signals]


def priority_display(priority_score: int) -> tuple[str, str]:
    if priority_score >= 150:
        return "HIGH", "High urgency — timeline within 30 days + strong motivation"
    if priority_score >= 110:
        return "MEDIUM", "Moderate urgency — active interest with some constraints"
    return "LOW", "Lower urgency — monitor and continue follow-up cadence"


st.markdown(THEME_CSS, unsafe_allow_html=True)
db.init_db()
db.seed_sample_data()
result = engine.evaluate_all_leads()
all_leads = db.fetch_leads()

st.markdown("## Warm Minimal Operator Dashboard <span class='plant'>🌿</span>", unsafe_allow_html=True)
st.caption(f"Approval mode only • {date.today().isoformat()} • Hot leads are always manual")

summary = result["summary"]
col1, col2, col3, col4, col5 = st.columns(5)
for c, label, value in [
    (col1, "Hot", summary["hot"]),
    (col2, "Warm", summary["warm"]),
    (col3, "Cold", summary["cold"]),
    (col4, "Follow-ups", summary["follow_ups"]),
    (col5, "Risk Leads", summary["risk_leads"]),
]:
    c.markdown(f"<div class='metric'><div>{label}</div><h3>{value}</h3></div>", unsafe_allow_html=True)

left, center, right = st.columns([1, 1.4, 1])

with left:
    st.markdown("### Pipeline Overview")
    for status in ["hot", "warm", "cold", "dead", "contract"]:
        count = len(db.fetch_leads(where="status=?", params=(status,)))
        st.markdown(
            f"<div class='card'><span class='badge'>{status.upper()}</span> <h4 style='display:inline;margin-left:8px'>{count}</h4></div>",
            unsafe_allow_html=True,
        )

with center:
    st.markdown("### Follow-up Approval Queue")
    if not result["followup_queue"]:
        st.info("No follow-ups due today.")
    for item in result["followup_queue"]:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"**{item['lead_name']}** · Objective: `{item['objective']}`")
        st.write(f"Last message: {item['last_message'] or '<none>'}")
        st.write(f"Suggested: {item['new_message']}")
        st.caption(item["reasoning"])
        st.caption(item["touch_notice"])
        if item["ghosting_signal"]:
            st.warning(item["ghosting_signal"])
            st.write("Suggestions: call again later · pattern break message · tone change · multi-channel")

        action = st.radio(
            f"Approval options for {item['lead_id']}",
            item["options"],
            horizontal=True,
            key=f"approval_{item['lead_id']}",
            label_visibility="collapsed",
        )
        st.write("Selected:", action)

        if action in {"approve", "call instead"}:
            st.markdown("**Update lead status?**")
            status_choice = st.selectbox(
                "Status update",
                ["keep warm", "upgrade to hot", "downgrade to cold"],
                key=f"status_choice_{item['lead_id']}",
                label_visibility="collapsed",
            )
            if st.button("Apply status update", key=f"apply_status_{item['lead_id']}"):
                lead_record = db.fetch_lead(item["lead_id"])
                if lead_record:
                    new_status = lead_record["status"]
                    if status_choice == "upgrade to hot":
                        new_status = "hot"
                    elif status_choice == "downgrade to cold":
                        new_status = "cold"
                    elif status_choice == "keep warm":
                        new_status = "warm"
                    db.update_lead(item["lead_id"], {"status": new_status})
                    st.success(f"Lead status updated to {new_status.upper()}.")

        st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown("### Call List (All HOT Leads)")
    if not result["call_list"]:
        st.info("No hot leads right now.")
    for entry in result["call_list"]:
        brief = result["call_briefs"][entry["lead_id"]]
        priority_label, reason = priority_display(entry["priority_score"])
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"**{entry['name']}** · `{entry['phone']}`")
        st.write("Priority:", priority_label)
        st.caption(reason)
        st.caption("Why hot: " + brief["why_hot"])
        st.caption("Objective: " + brief["objective"])
        st.markdown("</div>", unsafe_allow_html=True)

st.markdown("### Lead View")
status_filter = st.multiselect("Filter by status", ["hot", "warm", "cold"], default=["hot", "warm", "cold"])
search_value = st.text_input("Search name or phone", placeholder="Type a name or phone...").strip().lower()

filtered_leads = []
for lead in all_leads:
    if lead["status"] not in status_filter:
        continue
    if search_value and search_value not in lead["name"].lower() and search_value not in lead["phone"].lower():
        continue
    interactions = db.fetch_interactions(lead["id"])
    last_interaction = interactions[0]["timestamp"] if interactions else "No interactions"
    timeline_short, _, _ = parse_timeline_parts(lead["timeline"])
    filtered_leads.append(
        {
            "id": lead["id"],
            "Name": lead["name"],
            "Status": lead["status"],
            "Timeline": timeline_short,
            "Motivation": lead["motivation_score"],
            "Last interaction": last_interaction,
        }
    )

if not filtered_leads:
    st.info("No leads match your filters.")
else:
    st.dataframe(filtered_leads, use_container_width=True, hide_index=True)

    st.markdown("#### Select a lead")
    cols = st.columns(3)
    for idx, row in enumerate(filtered_leads):
        with cols[idx % 3]:
            if st.button(f"{row['Name']} ({row['Status'].upper()})", key=f"lead_pick_{row['id']}"):
                st.session_state["selected_lead_id"] = row["id"]

selected_lead_id = st.session_state.get("selected_lead_id")
if not selected_lead_id and filtered_leads:
    selected_lead_id = filtered_leads[0]["id"]
    st.session_state["selected_lead_id"] = selected_lead_id

if selected_lead_id:
    lead = db.fetch_lead(selected_lead_id)
    interactions = db.fetch_interactions(selected_lead_id)
    timeline_text, situation_text, notes_text = parse_timeline_parts(lead["timeline"])

    lead_col, history_col, suggest_col = st.columns([1, 1.2, 1])
    with lead_col:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader(lead["name"])
        st.write("Phone:", lead["phone"])
        st.write("Stage:", lead["conversation_stage"])
        st.write("Touch count:", lead["touch_count"])
        st.write("Situation:", situation_text or "Not recorded")

        edited_status = st.selectbox("Status", ["hot", "warm", "cold"], index=["hot", "warm", "cold"].index(lead["status"]))
        edited_motivation = st.slider("Motivation score", 1, 10, int(lead["motivation_score"]))
        edited_timeline = st.text_input("Timeline", value=timeline_text)
        edited_notes = st.text_area("Notes", value=notes_text or "")

        if st.button("Save changes", key=f"save_lead_{lead['id']}"):
            composed_timeline = edited_timeline.strip()
            if situation_text:
                composed_timeline += f". Situation: {situation_text.strip()}"
            if edited_notes.strip():
                composed_timeline += f". Notes: {edited_notes.strip()}"
            db.update_lead(
                lead["id"],
                {
                    "status": edited_status,
                    "motivation_score": edited_motivation,
                    "timeline": composed_timeline,
                },
            )
            if edited_notes.strip():
                db.add_interaction(lead["id"], "note", f"Updated notes: {edited_notes.strip()}", "outbound")
            st.success("Lead changes saved.")
        st.markdown("</div>", unsafe_allow_html=True)

    with history_col:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**History**")
        if interactions:
            for i in interactions[:15]:
                st.write(f"{i['timestamp']} · {i['direction']} {i['type']}: {i['content']}")
        else:
            st.write("No interactions yet")
        st.markdown("</div>", unsafe_allow_html=True)

    with suggest_col:
        ev = engine.evaluate_lead(lead, interactions)
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**System Suggestions**")
        st.write("Suggested status:", ev.suggested_status)
        st.write("Next action date:", ev.next_action_date)
        st.markdown("Intent signals:")
        labels = human_intent_labels(ev.intent_signals)
        if labels:
            st.markdown("".join([f"<span class='pill'>{label}</span>" for label in labels]), unsafe_allow_html=True)
        else:
            st.caption("No intent signals currently detected.")
        st.markdown("</div>", unsafe_allow_html=True)

st.markdown("### Performance")
perf = db.performance_summary()
insights = engine.performance_insights(perf)
perf_cols = st.columns(3)
perf_cols[0].metric("Logged touches", sum(x["sent"] for x in perf["by_touch"]) if perf["by_touch"] else 0)
perf_cols[1].metric("Best message type", insights["best_message_type"] or "N/A")
perf_cols[2].metric("Touch buckets", len(insights["response_rate_by_touch"]))

st.caption("Time optimization: " + insights["timing_hint"])
