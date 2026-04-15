from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from crm_engine import db, engine
from crm_engine.messaging_gateway import send_sms

st.set_page_config(page_title="Silverline Investment Group Dashboard", layout="wide")

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
.block-container {padding-top: 3rem;}
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
.pill {
  display: inline-block;
  border-radius: 999px;
  padding: 4px 10px;
  margin: 0 6px 6px 0;
  background: #edf3ee;
  color: #3c5f42;
  font-size: 0.82rem;
}
</style>
"""

PAGE_OPTIONS = ["Dashboard", "Leads", "Call List", "Follow-ups"]


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
        "timeline<=30_and_motivation>=6": "Timeline < 30 days + High motivation",
        "asks_price": "Asked about price",
        "urgency_language": "Urgency detected",
        "multiple_replies": "Multiple replies",
        "fast_replies": "Fast responder",
    }
    return [label_map.get(s, s.replace("_", " ").title()) for s in signals]


def go_to(page: str) -> None:
    st.session_state["page"] = page
    st.rerun()


def stamp() -> str:
    return datetime.utcnow().strftime("%b %d • %I:%M %p").replace(" 0", " ")


def update_status_immediately(lead_id: int, select_key: str) -> None:
    new_status = st.session_state.get(select_key)
    if new_status:
        db.update_lead(lead_id, {"status": new_status})


def render_lead_detail(lead_id: int) -> None:
    lead = db.fetch_lead(lead_id)
    interactions = db.fetch_interactions(lead_id)
    timeline_text, situation_text, notes_text = parse_timeline_parts(lead["timeline"])

    left, mid, right = st.columns([1, 1.1, 1])

    with left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader(lead["name"])
        st.write("Phone:", lead["phone"])
        st.write("Stage:", lead["conversation_stage"])
        st.write("Touch count:", lead["touch_count"])
        st.write("Situation:", situation_text or "Not recorded")

        status_key = f"status_select_{lead_id}"
        st.selectbox(
            "Status",
            ["hot", "warm", "cold"],
            index=["hot", "warm", "cold"].index(lead["status"]),
            key=status_key,
            on_change=update_status_immediately,
            args=(lead_id, status_key),
        )

        new_motivation = st.slider("Motivation", 1, 10, int(lead["motivation_score"]), key=f"mot_{lead_id}")
        new_timeline = st.text_input("Timeline", value=timeline_text, key=f"timeline_{lead_id}")
        new_notes = st.text_area("Notes", value=notes_text, key=f"notes_{lead_id}")

        if st.button("Save lead fields", key=f"save_lead_{lead_id}"):
            composed = new_timeline.strip()
            if situation_text:
                composed += f". Situation: {situation_text}"
            if new_notes.strip():
                composed += f". Notes: {new_notes.strip()}"
            db.update_lead(
                lead_id,
                {"motivation_score": new_motivation, "timeline": composed},
            )
            st.success("Lead updated.")
            st.rerun()

        note_input = st.text_input("Add note", key=f"add_note_{lead_id}")
        if st.button("Add timestamped note", key=f"note_btn_{lead_id}") and note_input.strip():
            db.add_interaction(lead_id, "note", f"{stamp()} — {note_input.strip()}", "outbound")
            st.success("Note logged.")
            st.rerun()

        st.markdown("**Touch logging**")
        t1, t2, t3 = st.columns(3)
        if t1.button("Log Call", key=f"log_call_{lead_id}"):
            db.add_interaction(lead_id, "call", f"{stamp()} — Call completed", "outbound")
            db.update_lead(lead_id, {"touch_count": lead["touch_count"] + 1, "last_contact_date": date.today().isoformat()})
            st.rerun()
        if t2.button("Log Text Sent", key=f"log_text_{lead_id}"):
            db.add_interaction(lead_id, "text", f"{stamp()} — Text sent", "outbound")
            db.update_lead(lead_id, {"touch_count": lead["touch_count"] + 1, "last_contact_date": date.today().isoformat()})
            st.rerun()
        if t3.button("Log Response", key=f"log_resp_{lead_id}"):
            db.add_interaction(lead_id, "text", f"{stamp()} — Response received", "inbound")
            db.update_lead(lead_id, {"touch_count": lead["touch_count"] + 1})
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    with mid:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Conversation History**")
        if interactions:
            for i in interactions[:20]:
                st.write(f"{i['timestamp']} · {i['direction']} {i['type']}: {i['content']}")
        else:
            st.write("No interactions yet")
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        evaluation = engine.evaluate_lead(lead, interactions)
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Intent Signals**")
        labels = human_intent_labels(evaluation.intent_signals)
        if labels:
            st.markdown("".join([f"<span class='pill'>{lab}</span>" for lab in labels]), unsafe_allow_html=True)
        else:
            st.caption("No intent signals detected")
        st.write("Suggested status:", evaluation.suggested_status)
        st.write("Next action date:", evaluation.next_action_date)
        st.markdown("</div>", unsafe_allow_html=True)


st.markdown(THEME_CSS, unsafe_allow_html=True)
db.init_db()
result = engine.evaluate_all_leads()
all_leads = db.fetch_leads()

if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"

with st.sidebar:
    st.title("Silverline Operator")
    selected = st.radio("Navigate", PAGE_OPTIONS, index=PAGE_OPTIONS.index(st.session_state["page"]))
    if selected != st.session_state["page"]:
        st.session_state["page"] = selected
        st.rerun()

st.markdown("# Silverline Investment Group Dashboard")
st.caption(f"Approval mode only • {date.today().isoformat()} • Hot leads remain manual")

page = st.session_state["page"]

if page == "Dashboard":
    summary = result["summary"]
    cols = st.columns(5)
    stats = [
        ("Hot", summary["hot"]),
        ("Warm", summary["warm"]),
        ("Cold", summary["cold"]),
        ("Follow-ups (Today)", len(result["followup_queue"])),
        ("Risk Leads", summary["risk_leads"]),
    ]
    for c, (label, value) in zip(cols, stats):
        c.markdown(f"<div class='metric'><div>{label}</div><h3>{value}</h3></div>", unsafe_allow_html=True)

    a, b = st.columns(2)
    with a:
        st.subheader("Call List Preview")
        for entry in result["call_list"][:5]:
            brief = result["call_briefs"][entry["lead_id"]]
            reason = ", ".join(human_intent_labels(entry.get("intent_signals", []))) or brief["why_hot"]
            st.markdown(f"- **{entry['name']}** · {entry['phone']}  ")
            st.caption(reason)
        if st.button("View All Calls"):
            go_to("Call List")

    with b:
        st.subheader("Lead Preview")
        rank = sorted(
            all_leads,
            key=lambda l: ((l["status"] == "hot") * 2 + (l["status"] == "warm"), l["motivation_score"], l["deal_probability"]),
            reverse=True,
        )
        for lead in rank[:5]:
            t, _, _ = parse_timeline_parts(lead["timeline"])
            st.markdown(f"- **{lead['name']}** · {lead['status'].upper()} · {lead['phone']}")
            st.caption(t)
        if st.button("View All Leads"):
            go_to("Leads")

    st.subheader("Follow-up Preview")
    for item in result["followup_queue"][:5]:
        st.markdown(f"- **{item['lead_name']}** · {item['objective']}")
        st.caption(item["new_message"])
    if st.button("View All Follow-ups"):
        go_to("Follow-ups")

elif page == "Call List":
    st.subheader("Call List — All Hot Leads")
    if not result["call_list"]:
        st.info("No hot leads currently.")
    else:
        for entry in result["call_list"]:
            brief = result["call_briefs"][entry["lead_id"]]
            labels = human_intent_labels(entry.get("intent_signals", []))
            reason = labels if labels else ["High intent"]
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            st.markdown(f"**{entry['name']}** · `{entry['phone']}`")
            st.markdown("".join([f"<span class='pill'>{r}</span>" for r in reason]), unsafe_allow_html=True)
            st.caption(brief["objective"])
            st.markdown("</div>", unsafe_allow_html=True)

elif page == "Leads":
    st.subheader("Leads")
    search = st.text_input("Search name or phone", placeholder="Type to search...").lower().strip()
    status_filter = st.multiselect("Optional status filter", ["hot", "warm", "cold"], default=[])

    matches = []
    for lead in all_leads:
        if status_filter and lead["status"] not in status_filter:
            continue
        if search and search not in lead["name"].lower() and search not in lead["phone"].lower():
            continue
        matches.append(lead)

    st.caption(f"{len(matches)} leads")
    for lead in matches:
        timeline, _, _ = parse_timeline_parts(lead["timeline"])
        if st.button(f"{lead['name']} · {lead['status'].upper()} · {lead['phone']} · {timeline}", key=f"pick_{lead['id']}"):
            st.session_state["selected_lead_id"] = lead["id"]

    selected_lead_id = st.session_state.get("selected_lead_id")
    if selected_lead_id:
        st.divider()
        render_lead_detail(selected_lead_id)

elif page == "Follow-ups":
    st.subheader("Follow-up Approvals")
    queue = result["followup_queue"]
    if not queue:
        st.info("No follow-up approvals pending.")
    for item in queue:
        lead = db.fetch_lead(item["lead_id"])
        interactions = db.fetch_interactions(item["lead_id"])[:8]
        _, situation, _ = parse_timeline_parts(lead["timeline"] if lead else "")

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"**{item['lead_name']}**")
        st.write("Situation:", situation or "Not recorded")
        st.write("AI suggested message:", item["new_message"])
        st.markdown("**Conversation history**")
        if interactions:
            for i in interactions:
                st.write(f"{i['timestamp']} · {i['direction']} {i['type']}: {i['content']}")
        else:
            st.write("No prior conversation logged")

        edit_key = f"edit_msg_{item['lead_id']}"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = item["new_message"]

        c1, c2, c3 = st.columns(3)
        if c1.button("Approve", key=f"approve_{item['lead_id']}"):
            send_sms(item["lead_id"], st.session_state[edit_key])
            st.success("Message sent via placeholder send interface.")
            st.rerun()
        if c2.button("Edit", key=f"edit_{item['lead_id']}"):
            st.session_state[f"editing_{item['lead_id']}"] = True
        if c3.button("Skip", key=f"skip_{item['lead_id']}"):
            st.info("Skipped for now.")

        if st.session_state.get(f"editing_{item['lead_id']}"):
            st.text_area("Edit message", key=edit_key, height=80)

        st.markdown("</div>", unsafe_allow_html=True)
