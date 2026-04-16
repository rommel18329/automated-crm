from __future__ import annotations

from datetime import date, datetime
import time

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
.block-container {padding-top: 2.4rem;}
.card {
  border-top: 1px solid #1f1f1f;
  padding-top: 12px;
  margin-top: 12px;
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
.msg-row{display:flex;margin:8px 0;}
.msg-left{justify-content:flex-start;}
.msg-right{justify-content:flex-end;}
.msg-bubble-in{background:#fff;border:1px solid #eee;padding:8px 10px;border-radius:14px;max-width:78%;}
.msg-bubble-out{background:#dff0ff;padding:8px 10px;border-radius:14px;max-width:78%;}
.msg-time{font-size:.73rem;color:#808080;margin-bottom:2px;}
h1 a, h2 a, h3 a, h4 a, h5 a, h6 a {display:none !important;}
.st-key-home_title button {font-size: 2.0rem !important; font-weight: 700 !important; border: none !important; background: transparent !important; padding-left: 0 !important;}
.st-key-home_title button {font-size: 2.3rem !important; font-weight: 800 !important; border: none !important; background: transparent !important; padding-left: 0 !important; width: 100% !important; text-align: left !important;}
.tooltip-wrap{position:relative;display:inline-flex;align-items:center;gap:6px;}
.tooltip-icon{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border:1px solid #8b8b8b;border-radius:50%;font-size:11px;line-height:1;color:#5d5d5d;cursor:default;}
.tooltip-tip{visibility:hidden;opacity:0;transition:opacity .15s;position:absolute;z-index:20;left:20px;top:-6px;background:#222;color:#fff;padding:6px 8px;border-radius:6px;font-size:12px;white-space:nowrap;}
.tooltip-wrap:hover .tooltip-tip{visibility:visible;opacity:1;}
.big-title {font-size: 36px !important; font-weight: 800 !important; margin-bottom: 10px;}
.big-title-wrap {margin-bottom: 2px !important;}
.quick-search-wrap {margin-top: -34px;}
.nav-item button{color:#2e2e2e !important;text-decoration:none !important;background:transparent !important;border:none !important;justify-content:flex-start !important;padding:6px 8px !important;cursor:pointer !important;border-radius:8px !important;}
.nav-item button:hover{background:#ece7df !important;}
.nav-item.active button{color:#2f4f35 !important;font-weight:600 !important;background:#e7f0e8 !important;border-left:2px solid #6B8F71 !important;padding-left:10px !important;border-radius:8px !important;}
[data-testid="stSidebar"] .stButton > button{color:#333 !important;text-decoration:none !important;}
.note-actions button{border:none !important;background:transparent !important;padding:0 !important;min-height:20px !important;font-size:0.85rem !important;}
.note-bubble{background:#f4f1ec;border:1px solid #e7e2d9;border-radius:12px;padding:8px 10px;}
</style>
"""

PAGES = ["Dashboard", "Leads", "Call List", "Follow-ups"]


def info_icon(text: str, tip: str) -> str:
    return (
        f"<span class='tooltip-wrap'>{text}"
        f"<span class='tooltip-icon'>i</span>"
        f"<span class='tooltip-tip'>{tip}</span>"
        "</span>"
    )


def parse_timeline_parts(timeline_text: str) -> tuple[str, str, str, str]:
    timeline = timeline_text or ""
    address = ""
    situation = ""
    notes = ""
    if "Address:" in timeline:
        before_addr, after_addr = timeline.split("Address:", 1)
        timeline = before_addr.strip().rstrip(".")
        if "Situation:" in after_addr:
            addr_part, after_situation = after_addr.split("Situation:", 1)
            address = addr_part.strip().rstrip(".")
            after = f"Situation:{after_situation}"
        else:
            address = after_addr.strip()
            after = ""
    else:
        after = timeline

    if "Situation:" in after:
        _, tail = after.split("Situation:", 1)
        after = tail
        if "Notes:" in after:
            situation_part, notes_part = after.split("Notes:", 1)
            situation = situation_part.strip().rstrip(".")
            notes = notes_part.strip()
        else:
            situation = after.strip()
    return timeline, address, situation, notes


def human_stage(stage: str) -> str:
    return stage.replace("_", " ").title()


def human_intent_labels(signals: list[str]) -> list[str]:
    label_map = {
        "timeline<=30_and_motivation>=6": "Timeline < 30 days + High motivation",
        "asks_price": "Asked about price",
        "urgency_language": "Urgency detected",
        "multiple_replies": "Multiple replies",
        "fast_replies": "Fast responder",
    }
    return [label_map.get(s, s.replace("_", " ").title()) for s in signals]


def fmt_stamp(dt: datetime) -> str:
    return dt.strftime("%m-%d • %I:%M %p").replace(" 0", " ")


def fmt_date_time(iso_value: str) -> tuple[str, str]:
    dt = datetime.fromisoformat(iso_value)
    return dt.strftime("%m-%d"), dt.strftime("%I:%M %p").lstrip("0")


def nav_to(page: str, lead_id: int | None = None) -> None:
    st.session_state["page"] = page
    if page == "Leads" and lead_id is None:
        st.session_state["lead_list_collapsed"] = False
    if lead_id is not None:
        st.session_state["selected_lead_id"] = lead_id
        st.session_state["lead_list_collapsed"] = True
    st.rerun()


def render_global_search(leads: list[dict]) -> None:
    if "quick_search_value" not in st.session_state:
        st.session_state["quick_search_value"] = ""
    q = st.text_input(
        "Quick Search",
        placeholder="Name or phone",
        label_visibility="collapsed",
        key="quick_search_value",
    ).strip().lower()
    if not q:
        return
    matches = [l for l in leads if q in l["name"].lower() or q in l["phone"].lower()][:8]
    with st.container(height=150):
        for lead in matches:
            if st.button(f"{lead['name']} · {lead['phone']}", key=f"quick_pick_{lead['id']}"):
                nav_to("Leads", lead["id"])


def seed_followup_examples() -> tuple[list[tuple[dict, dict]], list[tuple[dict, dict]]]:
    warm = [
        (
            {"lead_id": -1001, "lead_name": "Maya Porter", "new_message": "Would a quick value range help you decide next steps?"},
            {"timeline": "Considering options this quarter. Address: 214 Willow Brook Dr, Dallas, TX 75201. Situation: Downsizing soon. Notes: Responsive but cautious.", "status": "warm", "phone": "555-240-1101"},
        ),
        (
            {"lead_id": -1002, "lead_name": "Brent Lawson", "new_message": "No pressure—want me to text a simple path so you can compare?"}, 
            {"timeline": "Could move in 60 days. Address: 88 Maple Crest Ln, Dallas, TX 75202. Situation: Job relocation possible. Notes: Asked about speed.", "status": "warm", "phone": "555-240-1102"},
        ),
    ]
    cold = [
        (
            {"lead_id": -2001, "lead_name": "Tina Ramirez", "new_message": "Happy to check back later—still okay if I circle back next month?"},
            {"timeline": "No urgent timeline. Address: 17 Cedar Point Way, Dallas, TX 75203. Situation: Monitoring market. Notes: Low urgency.", "status": "cold", "phone": "555-240-2101"},
        ),
        (
            {"lead_id": -2002, "lead_name": "Paul Everett", "new_message": "If timing changed, I can keep this simple with one quick option."},
            {"timeline": "Maybe next year. Address: 502 Garden Row St, Dallas, TX 75204. Situation: Renovation planning. Notes: Minimal replies.", "status": "cold", "phone": "555-240-2102"},
        ),
    ]
    return warm, cold


def interaction_windows(interactions: list[dict]) -> dict[str, list[dict]]:
    now = datetime.utcnow()
    start_day = datetime.combine(date.today(), datetime.min.time())
    start_week = start_day.replace(hour=0)  # same day base
    start_week = start_week.replace(day=start_week.day - start_week.weekday())
    start_month = start_day.replace(day=1)

    def in_window(item: dict, start: datetime) -> bool:
        return datetime.fromisoformat(item["timestamp"]) >= start

    return {
        "daily": [i for i in interactions if in_window(i, start_day)],
        "weekly": [i for i in interactions if in_window(i, start_week)],
        "monthly": [i for i in interactions if in_window(i, start_month)],
    }


def activity_counts(interactions: list[dict]) -> dict[str, dict[str, int]]:
    windows = interaction_windows(interactions)

    def count_set(items: list[dict]) -> dict[str, int]:
        calls = sum(1 for i in items if i["type"] == "call")
        texts = sum(1 for i in items if i["type"] == "text" and i["direction"] == "outbound")
        followups = sum(1 for i in items if i["type"] == "text" and "suggested message approved" in i["content"].lower())
        offers = sum(1 for i in items if "offer made" in i["content"].lower())
        return {"calls": calls, "texts": texts, "followups": followups, "offers": offers}

    return {k: count_set(v) for k, v in windows.items()}


def overdue_followups(leads: list[dict]) -> list[dict]:
    today = date.today().isoformat()
    return [
        l for l in leads
        if l["status"] in {"warm", "cold"}
        and l.get("next_action_date")
        and l["next_action_date"] < today
    ]


def progression_metrics(leads: list[dict], interactions: list[dict]) -> dict[str, float]:
    warm = [l for l in leads if l["status"] == "warm"]
    hot = [l for l in leads if l["status"] == "hot"]
    contract = [l for l in leads if l["status"] == "contract"]

    offer_lead_ids = {i["lead_id"] for i in interactions if "offer made" in i["content"].lower()}
    hot_ids = {l["id"] for l in hot}
    contract_ids = {l["id"] for l in contract}

    warm_hot = (len(hot) / max(1, len(warm) + len(hot))) * 100
    hot_offer = (len(hot_ids & offer_lead_ids) / max(1, len(hot_ids))) * 100
    offer_contract = (len(contract_ids & offer_lead_ids) / max(1, len(offer_lead_ids))) * 100
    contract_deal = 100.0 if contract else 0.0
    return {
        "Warm→Hot": warm_hot,
        "Hot→Offer": hot_offer,
        "Offer→Contract": offer_contract,
        "Contract→Deal": contract_deal,
    }


def advanced_metrics(leads: list[dict], interactions: list[dict]) -> dict[str, str]:
    warm_to_hot_count = max(1, len([l for l in leads if l["status"] == "hot"]))
    warm_followups = sum(1 for i in interactions if i["type"] == "text" and i["direction"] == "outbound")
    fup_per_conv = warm_followups / warm_to_hot_count

    calls = sum(1 for i in interactions if i["type"] == "call")
    deals = max(1, len([l for l in leads if l["status"] == "contract"]))
    calls_per_deal = calls / deals

    lead_first_ts: dict[int, datetime] = {}
    lead_offer_ts: dict[int, datetime] = {}
    for i in sorted(interactions, key=lambda x: x["timestamp"]):
        ts = datetime.fromisoformat(i["timestamp"])
        lead_first_ts.setdefault(i["lead_id"], ts)
        if "offer made" in i["content"].lower() and i["lead_id"] not in lead_offer_ts:
            lead_offer_ts[i["lead_id"]] = ts

    deltas = []
    for lid, offer_ts in lead_offer_ts.items():
        if lid in lead_first_ts:
            deltas.append((offer_ts - lead_first_ts[lid]).days)
    avg_days = sum(deltas) / len(deltas) if deltas else 0

    return {
        "Follow-ups per Warm→Hot conversion": f"{fup_per_conv:.1f} (target: < 6)",
        "Calls per Deal": f"{calls_per_deal:.1f} (target: < 12)",
        "Time first contact→offer": f"{avg_days:.1f} days (target: < 14 days)",
    }


def completion_counts(call_list: list[dict]) -> tuple[int, int]:
    completed = sum(1 for entry in call_list if st.session_state.get(f"done_{entry['lead_id']}", False))
    remaining = max(0, len(call_list) - completed)
    return completed, remaining


def render_imessage(items: list[dict], max_items: int = 10) -> None:
    for msg in items[:max_items]:
        d, t = fmt_date_time(msg["timestamp"])
        ts = f"{d} • {t}"
        outgoing = msg["direction"] == "outbound"
        bubble = "msg-bubble-out" if outgoing else "msg-bubble-in"
        row = "msg-right" if outgoing else "msg-left"
        if msg["type"] == "call":
            prefix = "Outbound Call" if outgoing else "Inbound Call"
            label = f"{prefix} — {msg['content']}"
        else:
            label = msg["content"]
        st.markdown(
            f"<div class='msg-row {row}'><div><div class='msg-time'>{ts}</div><div class='{bubble}'>{label}</div></div></div>",
            unsafe_allow_html=True,
        )


def update_status(lead_id: int, key: str) -> None:
    db.update_lead(lead_id, {"status": st.session_state[key]})


def auto_save_field(lead_id: int, field: str, key: str) -> None:
    db.update_lead(lead_id, {field: st.session_state[key]})


def render_notes(lead_id: int, interactions: list[dict]) -> None:
    st.markdown("**Notes**")
    notes = [i for i in interactions if i["type"] == "note"]
    if not notes:
        st.caption("No notes yet")

    for note in notes[:12]:
        nid = note["id"]
        edit_key = f"edit_note_{nid}"
        if st.session_state.get(edit_key):
            new_val = st.text_input("", value=note["content"], key=f"note_input_{nid}")
            if st.button("Save", key=f"note_save_{nid}"):
                with db.get_conn() as conn:
                    conn.execute("UPDATE interactions SET content=? WHERE id=?", (new_val, nid))
                st.session_state[edit_key] = False
                st.rerun()
        else:
            row_left, row_edit, row_del = st.columns([14, 1, 1])
            row_left.markdown(f"<div class='note-bubble'>{note['content']}</div>", unsafe_allow_html=True)
            row_edit.markdown("<div class='note-actions'>", unsafe_allow_html=True)
            if row_edit.button("✏️", key=f"note_edit_{nid}", help="Edit", type="tertiary"):
                st.session_state[edit_key] = True
            row_edit.markdown("</div>", unsafe_allow_html=True)
            row_del.markdown("<div class='note-actions'>", unsafe_allow_html=True)
            if row_del.button("❌", key=f"note_del_{nid}", help="Delete", type="tertiary"):
                st.session_state[f"confirm_del_{nid}"] = True
            row_del.markdown("</div>", unsafe_allow_html=True)
            if st.session_state.get(f"confirm_del_{nid}"):
                st.warning("Delete this note?")
                y, n = st.columns(2)
                if y.button("Confirm", key=f"note_confirm_{nid}"):
                    with db.get_conn() as conn:
                        conn.execute("DELETE FROM interactions WHERE id=?", (nid,))
                    st.rerun()
                if n.button("Cancel", key=f"note_cancel_{nid}"):
                    st.session_state[f"confirm_del_{nid}"] = False

    with st.form(key=f"note_form_{lead_id}", clear_on_submit=True):
        new_note = st.text_input("Add note", key=f"new_note_{lead_id}")
        submitted = st.form_submit_button("Add Note")
    if submitted and new_note.strip():
        user_initials = st.session_state.get("current_user_initials", "RJ")
        db.add_interaction(lead_id, "note", f"{fmt_stamp(datetime.utcnow())} — {user_initials} {new_note.strip()}", "outbound")
        st.toast("Note added")
        st.rerun()


def render_lead_detail(lead_id: int, lead_lookup: dict[int, dict]) -> None:
    lead = lead_lookup.get(lead_id)
    if not lead:
        return
    interactions = db.fetch_interactions(lead_id)
    conversation = [
        i for i in interactions
        if i["type"] == "call" or (i["type"] == "text" and "suggested message approved" in i["content"].lower())
    ]
    timeline_text, address_text, situation_text, _ = parse_timeline_parts(lead["timeline"])

    st.markdown(f"### {lead['name']}")
    c_top1, c_top2, c_top3 = st.columns(3)
    c_top1.markdown(f"**Phone:** {lead['phone']}")
    c_top2.markdown(f"**Stage:** {human_stage(lead['conversation_stage'])}")
    c_top3.markdown(f"**Address:** {address_text}")

    c_edit1, c_edit2, c_edit3 = st.columns(3)
    c_edit1.selectbox(
        "Status",
        ["hot", "warm", "cold"],
        index=["hot", "warm", "cold"].index(lead["status"]),
        key=f"status_{lead_id}",
        on_change=update_status,
        args=(lead_id, f"status_{lead_id}"),
    )
    c_edit2.slider(
        "Motivation",
        1,
        10,
        int(lead["motivation_score"]),
        key=f"mot_{lead_id}",
        on_change=auto_save_field,
        args=(lead_id, "motivation_score", f"mot_{lead_id}"),
    )
    c_edit3.text_input(
        "Timeline",
        value=timeline_text,
        key=f"timeline_{lead_id}",
        on_change=auto_save_field,
        args=(lead_id, "timeline", f"timeline_{lead_id}"),
    )

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Conversation**")
        render_imessage(conversation, max_items=10)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Log Call**")
        mode = st.radio("When?", ["Now", "Select date/time"], horizontal=True, key=f"call_mode_{lead_id}")
        note = st.text_input("Call note", key=f"call_note_{lead_id}")
        offer_made = st.checkbox("Offer Made", key=f"offer_{lead_id}")
        ts = datetime.utcnow()
        if mode == "Select date/time":
            c1, c2, c3, c4 = st.columns(4)
            d = c1.date_input("Date", value=date.today(), key=f"date_{lead_id}")
            h = c2.selectbox("Hour", list(range(1, 13)), key=f"hour_{lead_id}")
            m = c3.selectbox("Min", [f"{i:02d}" for i in range(0, 60, 5)], key=f"min_{lead_id}")
            ap = c4.selectbox("AM/PM", ["AM", "PM"], key=f"ap_{lead_id}")
            h24 = h % 12 + (12 if ap == "PM" else 0)
            ts = datetime.combine(d, datetime.min.time()).replace(hour=h24, minute=int(m))

        if st.button("Log Call", key=f"log_call_{lead_id}"):
            content = f"{fmt_stamp(ts)} — {note.strip() or 'Call completed'}"
            db.add_interaction(lead_id, "call", content, "outbound", ts=ts)
            if offer_made:
                db.add_interaction(lead_id, "note", f"{fmt_stamp(ts)} — Offer made", "outbound", ts=ts)
            db.update_lead(lead_id, {"touch_count": lead["touch_count"] + 1, "last_contact_date": ts.date().isoformat()})
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.write("Situation:", situation_text or "Not recorded")
        labels = human_intent_labels(engine.evaluate_lead(lead, interactions).intent_signals)
        st.markdown("".join([f"<span class='pill'>{x}</span>" for x in labels]), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        render_notes(lead_id, interactions)
        st.markdown("</div>", unsafe_allow_html=True)


st.markdown(THEME_CSS, unsafe_allow_html=True)
st.markdown("""
<style>
.big-title {
    font-size: 36px !important;
    font-weight: 800 !important;
    margin-bottom: 10px;
}
</style>
<div class="big-title-wrap"><h1 class="big-title">Silverline Investment Group 🌿</h1></div>
""", unsafe_allow_html=True)
db.init_db()
result = engine.evaluate_all_leads()
all_leads = db.fetch_leads()
lead_lookup = {lead["id"]: lead for lead in all_leads}
selected_id = st.session_state.get("selected_lead_id")
if selected_id is not None and selected_id not in lead_lookup:
    st.session_state["selected_lead_id"] = None
all_interactions = []
for ld in all_leads:
    all_interactions.extend(db.fetch_interactions(ld["id"]))

if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"
if "lead_list_collapsed" not in st.session_state:
    st.session_state["lead_list_collapsed"] = False
if "call_completed" not in st.session_state:
    st.session_state["call_completed"] = {}
head_left, head_right = st.columns([4, 1.7])
with head_left:
    st.empty()
with head_right:
    st.markdown("<div class='quick-search-wrap'>", unsafe_allow_html=True)
    user_choice = st.selectbox("User", ["Rommel (RJ)", "Gabby (GL)"], key="user_picker")
    st.session_state["current_user_initials"] = "RJ" if user_choice.startswith("Rommel") else "GL"
    st.markdown("##### Quick Search")
    render_global_search(all_leads)
    st.markdown("</div>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### 🌿 Navigation")
    for item in PAGES:
        active = "active" if st.session_state["page"] == item else ""
        st.markdown(f"<div class='nav-item {active}'>", unsafe_allow_html=True)
        if st.button(item, key=f"nav_{item}", type="tertiary", use_container_width=True):
            st.session_state["page"] = item
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

st.divider()
page = st.session_state["page"]

if page == "Dashboard":
    summary = result["summary"]
    cols = st.columns(5)
    for c, (label, value) in zip(
        cols,
        [
            ("Hot", summary["hot"]),
            ("Warm", summary["warm"]),
            ("Cold", summary["cold"]),
            ("Follow-ups (Today)", len(result["followup_queue"])),
            ("Risk leads", summary["risk_leads"]),
        ],
    ):
        c.markdown(f"<div class='metric'><div>{label}</div><h3>{value}</h3></div>", unsafe_allow_html=True)

    st.markdown("### Call List")
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    done_count, remaining_count = completion_counts(result["call_list"])
    if st.button(f"Call List (Done: {done_count} | Remaining: {remaining_count})", key="call_list_nav"):
        nav_to("Call List")
    st.markdown("</div>", unsafe_allow_html=True)

    activity = activity_counts(all_interactions)
    st.markdown("### Activity")
    ac = st.columns(3)
    for idx, key in enumerate(["daily", "weekly", "monthly"]):
        m = activity[key]
        ac[idx].markdown(
            "<div class='card'>"
            f"<b>{key.title()}</b><br>"
            f"Calls: {m['calls']}<br>"
            f"Texts: {m['texts']}<br>"
            f"Follow-ups: {m['followups']}<br>"
            f"Offers: {m['offers']}"
            "</div>",
            unsafe_allow_html=True,
        )

    overdue = overdue_followups(all_leads)
    st.markdown("### Overdue Follow-ups")
    if st.button(f"🔴 {len(overdue)} overdue", key="overdue_btn"):
        st.session_state["followup_overdue_only"] = True
        nav_to("Follow-ups")

    st.markdown("### Lead Progression")
    prog = progression_metrics(all_leads, all_interactions)
    pc = st.columns(4)
    for c, (k, v) in zip(pc, prog.items()):
        c.markdown(
            f"<div class='card'><b>{info_icon(k, 'How effectively warm leads are escalated.')}</b><br>{v:.1f}%</div>",
            unsafe_allow_html=True,
        )

    with st.expander("Advanced Metrics"):
        adv = advanced_metrics(all_leads, all_interactions)
        for k, v in adv.items():
            st.markdown(f"**{info_icon(k, 'Operational efficiency benchmark for consistent pipeline progress')}:** {v}", unsafe_allow_html=True)


elif page == "Leads":
    st.subheader("Leads")
    q = st.text_input("Search", placeholder="Type name or phone...").strip().lower()
    status_filter = st.multiselect("Optional filters", ["hot", "warm", "cold"], default=[])
    matches = [
        l for l in all_leads
        if (not status_filter or l["status"] in status_filter)
        and (not q or q in l["name"].lower() or q in l["phone"].lower())
    ]

    if not st.session_state.get("lead_list_collapsed"):
        left, right = st.columns([1, 1.8])
    else:
        left, right = st.columns([0.14, 2.86])

    with left:
        if not st.session_state.get("lead_list_collapsed"):
            if st.button("◀", key="collapse_list_arrow", help="Collapse lead list"):
                st.session_state["lead_list_collapsed"] = True
                st.rerun()
            with st.container(height=620):
                for lead in matches:
                    _, address_text, _, _ = parse_timeline_parts(lead["timeline"])
                    if st.button(f"{lead['name']}\n{address_text}", key=f"lead_{lead['id']}"):
                        st.session_state["selected_lead_id"] = lead["id"]
                        st.session_state["lead_list_collapsed"] = True
                        st.rerun()
        else:
            if st.button("▶", key="expand_list_arrow", help="Expand lead list"):
                st.session_state["lead_list_collapsed"] = False
                st.rerun()

    with right:
        if st.session_state.get("selected_lead_id"):
            render_lead_detail(st.session_state["selected_lead_id"], lead_lookup)
        else:
            st.info("Select a lead to begin.")

elif page == "Call List":
    st.subheader("Call List")
    call_list = result["call_list"]
    calls_today = sum(1 for i in all_interactions if i["type"] == "call" and datetime.fromisoformat(i["timestamp"]).date() == date.today())
    st.caption(f"Total calls today: {calls_today}")

    completed, remaining = completion_counts(call_list)
    with st.container(height=640):
        for entry in call_list:
            lid = entry["lead_id"]
            done_key = f"done_{lid}"
            lead = lead_lookup.get(lid)
            _, address_text, situation, _ = parse_timeline_parts(lead["timeline"] if lead else "")
            checked = st.checkbox(f"{entry['name']} · {address_text}", key=done_key)
            completed, remaining = completion_counts(call_list)
            st.caption(f"Address: {address_text}")
            st.caption(situation)
            labels = human_intent_labels(entry.get("intent_signals", []))
            st.markdown("".join([f"<span class='pill'>{x}</span>" for x in labels]), unsafe_allow_html=True)
            if st.button(f"Open {entry['name']}", key=f"open_{lid}"):
                nav_to("Leads", lid)
            st.divider()

    completed, remaining = completion_counts(call_list)
    st.write(f"Completed: {completed} / {len(call_list)}")
    st.write(f"Remaining: {remaining}")
    if call_list and completed == len(call_list):
        st.balloons()

elif page == "Follow-ups":
    st.subheader("Follow-up Suggestions")
    overdue_only = st.session_state.get("followup_overdue_only", False)
    queue = result["followup_queue"]

    warm_items, cold_items = [], []
    for item in queue:
        lead = lead_lookup.get(item["lead_id"])
        if not lead:
            continue
        if overdue_only and lead.get("next_action_date") and lead["next_action_date"] >= date.today().isoformat():
            continue
        (warm_items if lead["status"] == "warm" else cold_items).append((item, lead))

    if not warm_items and not cold_items:
        warm_items, cold_items = seed_followup_examples()

    for section_name, items in [("Warm", warm_items), ("Cold", cold_items)]:
        st.markdown(f"### {section_name}")
        if not items:
            st.caption("No items")
        for item, lead in items:
            if item["lead_id"] > 0:
                history = [i for i in db.fetch_interactions(item["lead_id"]) if i["type"] == "text"][:6]
            else:
                history = [
                    {"timestamp": datetime.utcnow().isoformat(timespec="seconds"), "direction": "outbound", "type": "text", "content": "Quick check-in on your timeline."},
                    {"timestamp": datetime.utcnow().isoformat(timespec="seconds"), "direction": "inbound", "type": "text", "content": "Still deciding, open to options."},
                ]
            _, address, situation, _ = parse_timeline_parts(lead["timeline"])
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            if item["lead_id"] > 0 and st.button(f"{item['lead_name']}", key=f"f_name_{item['lead_id']}", type="tertiary"):
                nav_to("Leads", item["lead_id"])
            if item["lead_id"] <= 0:
                st.markdown(f"**{item['lead_name']}**")
            st.caption(f"Phone: {lead.get('phone', 'N/A')}")
            st.caption(f"Address: {address}")
            st.write("Situation:", situation or "Not recorded")
            with st.container(height=120):
                render_imessage(history, max_items=6)

            key = f"f_msg_{item['lead_id']}"
            if key not in st.session_state:
                st.session_state[key] = item["new_message"]
            sent_key = f"sent_preview_{item['lead_id']}"
            if sent_key not in st.session_state:
                st.session_state[sent_key] = []
            st.text_area("Suggestion", key=key, height=80)
            for sent_msg in st.session_state[sent_key]:
                ts = datetime.utcnow().isoformat(timespec="seconds")
                render_imessage([{"timestamp": ts, "direction": "outbound", "type": "text", "content": sent_msg}], max_items=1)

            c1, c2, c3 = st.columns(3)
            if c1.button("Approve", key=f"f_app_{item['lead_id']}"):
                if item["lead_id"] > 0:
                    typing_box = st.empty()
                    ts_now = datetime.utcnow().isoformat(timespec="seconds")
                    typing_box.markdown(
                        f"<div class='msg-row msg-left'><div><div class='msg-time'>{fmt_date_time(ts_now)[0]} • {fmt_date_time(ts_now)[1]}</div><div class='msg-bubble-in'>...</div></div></div>",
                        unsafe_allow_html=True,
                    )
                    time.sleep(0.25)
                    typing_box.empty()
                    ts = datetime.utcnow()
                    send_sms(item["lead_id"], st.session_state[key])
                    approved_text = st.session_state[key]
                    st.session_state[sent_key].append(approved_text)
                    user_initials = st.session_state.get("current_user_initials", "RJ")
                    db.add_interaction(item["lead_id"], "text", f"Suggested message approved: {user_initials} approved message: {approved_text}", "outbound", ts=ts)
                    db.add_interaction(item["lead_id"], "note", f"{user_initials} approved message: {approved_text}", "outbound", ts=ts)
                    st.session_state[key] = ""
                    st.markdown(
                        "<audio autoplay><source src='https://notificationsounds.com/storage/sounds/file-sounds-1152-pristine.mp3' type='audio/mpeg'></audio>",
                        unsafe_allow_html=True,
                    )
                    st.success("Message sent.")
                st.rerun()
            if c2.button("Edit", key=f"f_edit_{item['lead_id']}"):
                st.info("Edit in suggestion box.")
            if c3.button("Regenerate", key=f"f_regen_{item['lead_id']}"):
                st.session_state[key] = st.session_state[key] + " Just checking if a quick call would be easier."
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
