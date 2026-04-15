from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from crm_engine import db, engine
from crm_engine.messaging_gateway import send_sms

st.set_page_config(page_title="Silverline Investment Group", layout="wide")

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
.block-container {padding-top: 2.7rem;}
.card {
  background: var(--card);
  border-radius: 16px;
  padding: 14px 16px;
  margin-bottom: 12px;
  box-shadow: 0 8px 22px rgba(46,46,46,0.06);
}
.metric {
  background: var(--highlight);
  border-radius: 14px;
  padding: 10px;
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
h1 a, h2 a, h3 a, h4 a, h5 a, h6 a {display: none !important;}
</style>
"""

PAGES = ["Dashboard", "Leads", "Call List", "Follow-ups"]


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


def nav_to(page: str, lead_id: int | None = None) -> None:
    st.session_state["page"] = page
    if lead_id is not None:
        st.session_state["selected_lead_id"] = lead_id
        st.session_state["scroll_to_selected"] = True
    st.rerun()


def note_stamp() -> str:
    return datetime.utcnow().strftime("%m-%d • %I:%M %p").replace(" 0", " ")


def format_dt(iso_value: str) -> tuple[str, str]:
    dt = datetime.fromisoformat(iso_value)
    return dt.strftime("%m-%d"), dt.strftime("%I:%M %p").lstrip("0")


def human_stage(stage: str) -> str:
    return stage.replace("_", " ").title()


def update_status(lead_id: int, key: str) -> None:
    status = st.session_state.get(key)
    if status:
        db.update_lead(lead_id, {"status": status})


def render_global_search(all_leads: list[dict]) -> None:
    search = st.text_input(
        "Quick Search",
        placeholder="Name or phone",
        label_visibility="collapsed",
        key="global_quick_search",
    ).strip().lower()
    if not search:
        return
    matches = [
        lead
        for lead in all_leads
        if search in lead["name"].lower() or search in lead["phone"].lower()
    ][:8]
    if not matches:
        st.caption("No leads found.")
        return
    st.caption("Select a lead to open in Leads page")
    with st.container(height=160):
        for lead in matches:
            if st.button(f"{lead['name']} · {lead['phone']}", key=f"global_pick_{lead['id']}"):
                nav_to("Leads", lead["id"])


def mock_followups() -> list[dict]:
    return [
        {
            "lead_id": -9001,
            "lead_name": "Maria Benson",
            "situation": "Relocating for work in 45 days",
            "history": [
                ("04-11", "9:49 AM", "outbound", "Hey Maria, still considering selling this spring?"),
                ("04-11", "10:02 AM", "inbound", "Possibly. Still looking at options."),
                ("04-13", "1:15 PM", "outbound", "Want a quick range so you can compare?"),
            ],
            "suggestion": "I can send a quick no-pressure range so you can compare options—want me to?",
        },
        {
            "lead_id": -9002,
            "lead_name": "Derek Hall",
            "situation": "Needs clarity before making repairs",
            "history": [
                ("04-10", "11:05 AM", "outbound", "Checking in Derek—any updates on your timeline?"),
                ("04-10", "12:27 PM", "inbound", "Not sure yet. Maybe in a month."),
                ("04-12", "3:40 PM", "outbound", "Would a quick call help simplify next steps?"),
            ],
            "suggestion": "Totally fine if timing is still open—would a short 5-minute call help you decide next steps?",
        },
    ]


def render_lead_detail(lead_id: int) -> None:
    lead = db.fetch_lead(lead_id)
    interactions = db.fetch_interactions(lead_id)
    timeline_text, situation_text, notes_text = parse_timeline_parts(lead["timeline"])

    c1, c2, c3 = st.columns([1, 1.15, 1])

    highlight_style = "style='border:2px solid #6B8F71;'" if st.session_state.get("scroll_to_selected") else ""
    st.markdown(f"<div id='selected-lead' {highlight_style}></div>", unsafe_allow_html=True)

    with c1:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader(lead["name"])
        st.write("Phone:", lead["phone"])
        st.write("Stage:", human_stage(lead["conversation_stage"]))
        st.write("Touch count:", lead["touch_count"])
        st.write("Situation:", situation_text or "Not recorded")

        skey = f"status_{lead_id}"
        st.selectbox(
            "Status",
            ["hot", "warm", "cold"],
            index=["hot", "warm", "cold"].index(lead["status"]),
            key=skey,
            on_change=update_status,
            args=(lead_id, skey),
        )
        st.caption("Status saves immediately.")

        motivation = st.slider("Motivation", 1, 10, int(lead["motivation_score"]), key=f"mot_{lead_id}")
        timeline = st.text_input("Timeline", value=timeline_text, key=f"timeline_{lead_id}")
        if st.button("Save lead fields", key=f"save_fields_{lead_id}"):
            merged = timeline.strip()
            if situation_text:
                merged += f". Situation: {situation_text}"
            if notes_text:
                merged += f". Notes: {notes_text}"
            db.update_lead(lead_id, {"motivation_score": motivation, "timeline": merged})
            st.success("Lead fields saved.")
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Conversation Log**")
        logs = interactions[:10]
        if not logs:
            st.write("No conversation activity logged")
        for msg in logs:
            d, t = format_dt(msg["timestamp"])
            st.caption(f"{d} • {t}")
            if msg["type"] == "call":
                st.write("Outbound Call" if msg["direction"] == "outbound" else "Inbound Call")
                st.write(msg["content"])
            else:
                label = "Text Sent" if msg["direction"] == "outbound" else "Text Received"
                st.write(f"{label}: {msg['content']}")
            st.divider()

        st.markdown("---")
        st.markdown("**Log Call**")
        mode = st.radio("When?", ["Now", "Select date + time"], horizontal=True, key=f"call_mode_{lead_id}")
        call_note = st.text_input("Call note", key=f"call_note_{lead_id}", placeholder="Spoke, wants callback tomorrow")

        manual_ts = None
        if mode == "Select date + time":
            c_date, c_hour, c_min, c_ampm = st.columns([1.3, 1, 1, 1])
            call_date = c_date.date_input("Date", key=f"call_date_{lead_id}", value=date.today())
            hour = c_hour.selectbox("Hour", list(range(1, 13)), key=f"call_hour_{lead_id}")
            minute = c_min.selectbox("Min", [f"{m:02d}" for m in range(0, 60, 5)], key=f"call_min_{lead_id}")
            ampm = c_ampm.selectbox("AM/PM", ["AM", "PM"], key=f"call_ampm_{lead_id}")
            hour_24 = hour % 12 + (12 if ampm == "PM" else 0)
            manual_ts = datetime.combine(call_date, datetime.min.time()).replace(hour=hour_24, minute=int(minute))

        add_text_log = st.checkbox("Also add as text note", key=f"call_text_{lead_id}", value=False)
        if st.button("Log Call", key=f"call_btn_{lead_id}"):
            ts = manual_ts or datetime.utcnow()
            content = f"{note_stamp()} — {call_note.strip() or 'Call completed'}"
            db.add_interaction(lead_id, "call", content, "outbound", ts=ts)
            if add_text_log:
                db.add_interaction(lead_id, "text", f"Call logged: {call_note.strip() or 'Completed'}", "outbound", ts=ts)
            db.update_lead(lead_id, {"touch_count": lead["touch_count"] + 1, "last_contact_date": ts.date().isoformat()})
            st.success("Call logged.")
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    with c3:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Notes History**")
        note_items = [i for i in interactions if i["type"] == "note"]
        if note_items:
            for n in note_items[:12]:
                st.write(n["content"])
        else:
            st.caption("No notes yet")

        new_note = st.text_input("Add note", key=f"new_note_{lead_id}")
        if st.button("Save Note", key=f"save_note_{lead_id}") and new_note.strip():
            db.add_interaction(lead_id, "note", f"{note_stamp()} — {new_note.strip()}", "outbound")
            st.success("Note saved.")
            st.rerun()

        ev = engine.evaluate_lead(lead, interactions)
        labels = human_intent_labels(ev.intent_signals)
        st.markdown("**Intent Signals**")
        if labels:
            st.markdown("".join([f"<span class='pill'>{x}</span>" for x in labels]), unsafe_allow_html=True)
        else:
            st.caption("No intent signals")
        st.markdown("</div>", unsafe_allow_html=True)


st.markdown(THEME_CSS, unsafe_allow_html=True)
db.init_db()
result = engine.evaluate_all_leads()
all_leads = db.fetch_leads()

if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"

left_head, right_search = st.columns([4, 1.7])
with left_head:
    st.markdown("# Silverline Investment Group 🌿")
    st.caption("")
with right_search:
    st.markdown("##### Quick Search")
    render_global_search(all_leads)

with st.sidebar:
    st.markdown("### 🌿 Navigation")
    for section in PAGES:
        active = st.session_state["page"] == section
        prefix = "• " if active else ""
        if st.button(f"{prefix}{section}", use_container_width=True, type="tertiary", key=f"nav_{section}"):
            nav_to(section)
st.divider()

page = st.session_state["page"]

if page == "Dashboard":
    summary = result["summary"]
    cols = st.columns(5)
    stats = [
        ("Hot", summary["hot"]),
        ("Warm", summary["warm"]),
        ("Cold", summary["cold"]),
        ("Follow-ups (Today)", len(result["followup_queue"])),
        ("Risk leads", summary["risk_leads"]),
    ]
    for c, (label, value) in zip(cols, stats):
        c.markdown(f"<div class='metric'><div>{label}</div><h3>{value}</h3></div>", unsafe_allow_html=True)

    st.markdown("### Call List Preview")
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    call_total = len(result["call_list"])
    if st.button(f"Call List ({call_total} calls today)", key="goto_calls_page"):
        nav_to("Call List")
    for entry in result["call_list"][:5]:
        reason_labels = human_intent_labels(entry.get("intent_signals", []))
        reason = ", ".join(reason_labels) if reason_labels else "High urgency"
        if st.button(f"{entry['name']} · {entry['phone']}", key=f"dash_call_pick_{entry['lead_id']}"):
            nav_to("Leads", entry["lead_id"])
        st.caption(reason)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("### Follow-up Preview")
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    pending = len(result["followup_queue"])
    if st.button(f"Follow-ups ({pending} pending)", key="dashboard_view_followups"):
        nav_to("Follow-ups")
    if result["followup_queue"]:
        sample = result["followup_queue"][0]
        st.markdown(f"**{sample['lead_name']}** · {sample['objective']}")
        st.caption(sample["new_message"])
    else:
        st.caption("No pending follow-ups.")
    st.markdown("</div>", unsafe_allow_html=True)

elif page == "Call List":
    st.subheader("Call List")
    call_list = result["call_list"]
    if not call_list:
        st.info("No hot leads right now.")
    with st.container(height=560):
        for entry in call_list:
            labels = human_intent_labels(entry.get("intent_signals", [])) or ["High intent"]
            if st.button(f"{entry['name']} · {entry['phone']}", key=f"call_pick_{entry['lead_id']}"):
                nav_to("Leads", entry["lead_id"])
            st.markdown("".join([f"<span class='pill'>{x}</span>" for x in labels]), unsafe_allow_html=True)

elif page == "Leads":
    st.subheader("Leads")
    search = st.text_input("Search name or phone", placeholder="Type to filter instantly...").strip().lower()
    status_filter = st.multiselect("Optional filters", ["hot", "warm", "cold"], default=[])

    matches = []
    for lead in all_leads:
        if status_filter and lead["status"] not in status_filter:
            continue
        if search and search not in lead["name"].lower() and search not in lead["phone"].lower():
            continue
        matches.append(lead)

    left, right = st.columns([1, 1.7])
    with left:
        st.caption(f"{len(matches)} results")
        with st.container(height=620):
            for lead in matches:
                short_timeline, _, _ = parse_timeline_parts(lead["timeline"])
                if st.button(
                    f"{lead['name']} · {lead['status'].upper()} · {lead['phone']} · {short_timeline}",
                    key=f"lead_pick_{lead['id']}",
                ):
                    st.session_state["selected_lead_id"] = lead["id"]
                    st.session_state["scroll_to_selected"] = True
                    st.rerun()
    with right:
        selected_id = st.session_state.get("selected_lead_id")
        if selected_id:
            render_lead_detail(selected_id)
        else:
            st.info("Select a lead from the list.")

    selected_id = st.session_state.get("selected_lead_id")
    if selected_id and st.session_state.get("scroll_to_selected"):
        st.markdown(
            """
<script>
const el = window.parent.document.getElementById('selected-lead');
if (el) { el.scrollIntoView({behavior:'smooth', block:'start'}); }
</script>
""",
            unsafe_allow_html=True,
        )
        st.session_state["scroll_to_selected"] = False

elif page == "Follow-ups":
    st.subheader("Follow-ups")
    queue = result["followup_queue"]
    if not queue:
        st.info("No follow-up approvals pending. Showing sample follow-ups.")
    sample_items = mock_followups()

    for item in queue:
        lead = db.fetch_lead(item["lead_id"])
        history = [i for i in db.fetch_interactions(item["lead_id"]) if i["type"] == "text"][:5]
        _, situation, _ = parse_timeline_parts(lead["timeline"] if lead else "")

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"**{item['lead_name']}**")
        st.write("Situation:", situation or "Not recorded")
        st.markdown("**Last messages**")
        if history:
            for h in history:
                d, t = format_dt(h["timestamp"])
                st.caption(f"{d} {t} · {h['direction']} · {h['content']}")
        else:
            st.caption("No prior messages")

        msg_key = f"msg_{item['lead_id']}"
        if msg_key not in st.session_state:
            st.session_state[msg_key] = item["new_message"]

        st.write("Suggested message:")
        st.write(st.session_state[msg_key])

        c1, c2, c3 = st.columns(3)
        if c1.button("Approve", key=f"approve_{item['lead_id']}"):
            send_sms(item["lead_id"], st.session_state[msg_key])
            st.success("Sent via send_sms() placeholder.")
            st.rerun()
        if c2.button("Edit", key=f"edit_{item['lead_id']}"):
            st.session_state[f"editing_{item['lead_id']}"] = True
        if c3.button("Skip", key=f"skip_{item['lead_id']}"):
            st.info("Skipped")

        if st.session_state.get(f"editing_{item['lead_id']}"):
            st.text_area("Edit message", key=msg_key, height=80)

        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("### Sample Follow-ups")
    for sample in sample_items:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"**{sample['lead_name']}**")
        st.write("Situation:", sample["situation"])
        st.markdown("**Last messages**")
        with st.container(height=120):
            for d, t, direction, content in sample["history"]:
                st.caption(f"{d}")
                st.caption(f"{t} · {direction} · {content}")

        msg_key = f"sample_msg_{sample['lead_id']}"
        if msg_key not in st.session_state:
            st.session_state[msg_key] = sample["suggestion"]
        st.text_area("Suggestion", key=msg_key, height=80)

        c1, c2, c3 = st.columns(3)
        if c1.button("Approve", key=f"sample_approve_{sample['lead_id']}"):
            st.success("Approved (sample).")
        if c2.button("Edit", key=f"sample_edit_{sample['lead_id']}"):
            st.info("Edit directly in suggestion box.")
        if c3.button("Regenerate", key=f"sample_regen_{sample['lead_id']}"):
            st.session_state[msg_key] = sample["suggestion"] + " If it helps, we can do a 5-minute call."
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
