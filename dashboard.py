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
.h-title {color: var(--accent); font-weight: 700;}
.plant {font-size: 1.3rem; opacity: 0.9;}
</style>
"""

st.markdown(THEME_CSS, unsafe_allow_html=True)
db.init_db()
db.seed_sample_data()
result = engine.evaluate_all_leads()

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
        st.markdown(f"<div class='card'><span class='badge'>{status.upper()}</span> <h4 style='display:inline;margin-left:8px'>{count}</h4></div>", unsafe_allow_html=True)

with center:
    st.markdown("### Follow-up Approval Queue")
    if not result["followup_queue"]:
        st.info("No follow-ups due today.")
    for item in result["followup_queue"]:
        with st.container(border=False):
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
            st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown("### Call List (All HOT Leads)")
    if not result["call_list"]:
        st.info("No hot leads right now.")
    for entry in result["call_list"]:
        brief = result["call_briefs"][entry["lead_id"]]
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"**{entry['name']}** · `{entry['phone']}`")
        st.write("Priority:", entry["priority_score"])
        st.caption("Why hot: " + brief["why_hot"])
        st.caption("Objective: " + brief["objective"])
        st.markdown("</div>", unsafe_allow_html=True)

st.markdown("### Lead View")
lead_ids = [l["id"] for l in db.fetch_leads()]
selected = st.selectbox("Choose lead", lead_ids)
lead = db.fetch_lead(selected)
interactions = db.fetch_interactions(selected)

lead_col, history_col, suggest_col = st.columns([1, 1.2, 1])
with lead_col:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader(lead["name"])
    st.write("Status:", lead["status"])
    st.write("Stage:", lead["conversation_stage"])
    st.write("Touch count:", lead["touch_count"])
    st.write("Timeline:", lead["timeline"])
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
    st.write("Intent signals:", ", ".join(ev.intent_signals) if ev.intent_signals else "None")
    st.write("Suggested status:", ev.suggested_status)
    st.write("Next action date:", ev.next_action_date)
    st.write("Post interaction:", ", ".join(engine.post_interaction_options()))
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("### Performance")
perf = db.performance_summary()
insights = engine.performance_insights(perf)
perf_cols = st.columns(3)
perf_cols[0].metric("Logged touches", sum(x["sent"] for x in perf["by_touch"]) if perf["by_touch"] else 0)
perf_cols[1].metric("Best message type", insights["best_message_type"] or "N/A")
perf_cols[2].metric("Touch buckets", len(insights["response_rate_by_touch"]))

st.caption("Time optimization: " + insights["timing_hint"])
