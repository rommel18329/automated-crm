from __future__ import annotations

from datetime import date, datetime
import base64
import time

import streamlit as st

from crm_engine import db, engine
from crm_engine.messaging_gateway import send_sms

st.set_page_config(page_title="Silverline Investment Group", layout="wide")
st.markdown("""
<style>
div[data-testid="stAppViewContainer"] .main .block-container {
    max-width: 100% !important;
    padding-top: 1rem !important;
}

.big-title {
    font-size: 48px !important;
    font-weight: 800 !important;
    line-height: 1.1 !important;
    margin-bottom: 0.5rem !important;
}
</style>
""", unsafe_allow_html=True)
st.markdown(
    '<div class="big-title">Silverline Investment Group 🌿</div>',
    unsafe_allow_html=True
)

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
.tooltip-wrap{position:relative;display:inline-flex;align-items:center;gap:6px;}
.tooltip-icon{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border:1px solid #8b8b8b;border-radius:50%;font-size:11px;line-height:1;color:#5d5d5d;cursor:default;}
.tooltip-tip{visibility:hidden;opacity:0;transition:opacity .15s;position:absolute;z-index:20;left:20px;top:-6px;background:#222;color:#fff;padding:6px 8px;border-radius:6px;font-size:12px;white-space:nowrap;}
.tooltip-wrap:hover .tooltip-tip{visibility:visible;opacity:1;}
.quick-search-wrap {margin-top: -34px;}
[data-testid="stSidebar"] [data-baseweb="tab-list"]{
  gap:2px;
  display:flex;
  flex-direction:column;
  align-items:stretch;
}
[data-testid="stSidebar"] [data-baseweb="tab"]{
  justify-content:flex-start !important;
  text-align:left !important;
  color:#2f2f2f !important;
  background:transparent !important;
  border:none !important;
  border-radius:0 !important;
  box-shadow:none !important;
  padding:6px 4px !important;
  margin:0 !important;
  transition:background .15s ease,color .15s ease;
}
[data-testid="stSidebar"] [data-baseweb="tab"]:hover{
  background:#efefef !important;
  cursor:pointer !important;
}
[data-testid="stSidebar"] [aria-selected="true"]{
  background:#ececec !important;
  color:#1f1f1f !important;
  font-weight:600 !important;
  border-left:2px solid #6B8F71 !important;
}
[data-testid="stToggle"] [data-baseweb="switch"]{height:34px !important;width:78px !important;}
[data-testid="stToggle"] [data-baseweb="switch"] > div{background:#d64b4b !important;}
[data-testid="stToggle"] input:checked + div{background:#54a96a !important;}
[data-testid="stToggle"] [data-baseweb="switch"] > div::before{content:'RJ';position:absolute;left:12px;top:8px;color:#fff;font-size:12px;font-weight:700;}
[data-testid="stToggle"] input:checked + div::before{content:'GL';left:44px;}
.note-actions button{border:none !important;background:transparent !important;padding:0 !important;min-height:20px !important;font-size:0.85rem !important;}
.note-bubble{background:#f4f1ec;border:1px solid #e7e2d9;border-radius:12px;padding:8px 10px;}
.typing-dot{display:inline-block;animation:blink 1.2s infinite;}
.typing-dot:nth-child(2){animation-delay:.2s;}
.typing-dot:nth-child(3){animation-delay:.4s;}
@keyframes blink{0%,80%,100%{opacity:.3;}40%{opacity:1;}}
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


def autoplay_audio() -> None:
    # tiny embedded mp3 to avoid binary asset files in repo/PR
    b64 = (
        "/+MYxAAEaAIEeUAQAgBgNgP/////KQQ/////Lvrg+lcWYHgtjadzsbTq+yREu495tq9c6v/7vt/of7mna9v6/"
        "btUnU17Jun9/+MYxCkT26KW+YGBAj9v6vUh+zab//v/96C3/pu6H+pv//r/ycIIP4pcWWTRBBBAMXgNdbRaABQ"
        "AAABRWKwgjQVX0ECmrb///+MYxBQSM0sWWYI4A++Z/////////////0rOZ3MP//7H44QEgxgdvRVMXHZseL//5"
        "40B4JAvMPEgaA4/0nHjxLhRgAoAYAgA/+MYxAYIAAJfGYEQAMAJAIAQMAwX936/q/tWtv/2f/+v//6v/+7qTEFN"
        "RTMuOTkuNVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    )
    st.markdown(
        f"""
        <audio autoplay>
        <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
        </audio>
        """,
        unsafe_allow_html=True,
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
    page_map = {
        "Dashboard": "dashboard",
        "Leads": "leads",
        "Call List": "call_list",
        "Follow-ups": "followups",
    }
    normalized = page_map.get(page, page)
    st.session_state["page"] = normalized
    if normalized == "leads" and lead_id is None:
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
        placeholder="Search leads...",
        label_visibility="collapsed",
        key="quick_search_value",
    ).lower()
    if not q:
        return

    matches = []
    for lead in leads:
        _, address_text, _, _ = parse_timeline_parts(lead["timeline"])
        searchable = f"{lead['name']} {lead['phone']} {address_text}".lower()
        if q in searchable:
            matches.append((lead, address_text))
        if len(matches) >= 8:
            break

    with st.container(height=180):
        if not matches:
            st.caption("No matches")
            return
        for lead in matches:
            item, address_text = lead
            if st.button(f"{item['name']} · {address_text}", key=f"quick_pick_{item['id']}"):
                nav_to("Leads", item["id"])


def generate_sample_followups_from_leads(leads: list[dict]) -> tuple[list[dict], list[dict]]:
    warm_items: list[dict] = []
    cold_items: list[dict] = []
    for lead in leads:
        timeline_text, _, situation_text, notes_text = parse_timeline_parts(lead.get("timeline", ""))
        context_hint = notes_text or situation_text or timeline_text
        if lead["status"] == "warm" and len(warm_items) < 5:
            warm_items.append(
                {
                    "lead_id": lead["id"],
                    "lead_name": lead["name"],
                    "new_message": (
                        f"Hey {lead['name']}, just wanted to check in — are you still thinking about selling? "
                        f"{context_hint[:80]}"
                    ).strip(),
                }
            )
        if lead["status"] == "cold" and len(cold_items) < 5:
            cold_items.append(
                {
                    "lead_id": lead["id"],
                    "lead_name": lead["name"],
                    "new_message": (
                        f"Hey {lead['name']}, circling back — let me know if timing changes on your end. "
                        f"{context_hint[:80]}"
                    ).strip(),
                }
            )
        if len(warm_items) >= 5 and len(cold_items) >= 5:
            break
    return warm_items, cold_items


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


def hot_not_contacted_today(leads: list[dict], interactions: list[dict]) -> list[dict]:
    today = date.today()
    touched_today: set[int] = set()
    for item in interactions:
        if item["type"] not in {"call", "text"}:
            continue
        if datetime.fromisoformat(item["timestamp"]).date() == today:
            touched_today.add(item["lead_id"])
    return [lead for lead in leads if lead["status"] == "hot" and lead["id"] not in touched_today]


def followups_waiting_approval(queue: list[dict], interactions: list[dict]) -> int:
    approved_ids = {
        item["lead_id"]
        for item in interactions
        if item["type"] == "text" and "suggested message approved" in item["content"].lower()
    }
    return sum(1 for item in queue if item["lead_id"] not in approved_ids)


def progression_metrics(leads: list[dict], interactions: list[dict]) -> dict[str, float]:
    warm = [l for l in leads if l["status"] == "warm"]
    hot = [l for l in leads if l["status"] == "hot"]
    contracts = [l for l in leads if l["status"] == "contract"]
    offer_lead_ids = {i["lead_id"] for i in interactions if "offer made" in i["content"].lower()}
    closed_deal_ids = {
        i["lead_id"] for i in interactions
        if "deal closed" in i["content"].lower() or "closed deal" in i["content"].lower()
    }
    closed_deal_ids |= {lead["id"] for lead in leads if lead["status"] == "closed"}

    warm_hot_num = len(hot)
    warm_hot_den = len(warm)
    hot_offer_num = len({lead["id"] for lead in hot} & offer_lead_ids)
    hot_offer_den = len(hot)
    offer_contract_num = len({lead["id"] for lead in contracts} & offer_lead_ids)
    offer_contract_den = len(offer_lead_ids)
    contract_deal_num = len({lead["id"] for lead in contracts} & closed_deal_ids)
    contract_deal_den = len(contracts)

    warm_hot = (warm_hot_num / max(1, warm_hot_den)) * 100
    hot_offer = (hot_offer_num / max(1, hot_offer_den)) * 100
    offer_contract = (offer_contract_num / max(1, offer_contract_den)) * 100
    contract_deal = (contract_deal_num / max(1, contract_deal_den)) * 100
    return {
        "Warm→Hot": (warm_hot, warm_hot_num, warm_hot_den),
        "Hot→Offer": (hot_offer, hot_offer_num, hot_offer_den),
        "Offer→Contract": (offer_contract, offer_contract_num, offer_contract_den),
        "Contract→Deal": (contract_deal, contract_deal_num, contract_deal_den),
    }


def stuck_leads(leads: list[dict]) -> list[dict]:
    cutoff = date.today().toordinal() - 3
    out: list[dict] = []
    for lead in leads:
        if lead["status"] not in {"warm", "hot"}:
            continue
        last_contact = lead.get("last_contact_date")
        if not last_contact:
            continue
        if date.fromisoformat(last_contact).toordinal() < cutoff:
            out.append(lead)
    return out


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


def lead_conversation_items(lead_id: int) -> list[dict]:
    interactions = db.fetch_interactions(lead_id)
    return [
        i for i in interactions
        if i["type"] == "call" or (i["type"] == "text" and "suggested message approved" in i["content"].lower())
    ]


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
        user_dot = "🔴" if st.session_state.get("user", "RJ") == "RJ" else "🟢"
        db.add_interaction(lead_id, "note", f"{fmt_stamp(datetime.utcnow())} — {user_dot} {new_note.strip()}", "outbound")
        st.toast("Note added")
        st.rerun()


def render_lead_detail(lead_id: int, lead_lookup: dict[int, dict]) -> None:
    lead = lead_lookup.get(lead_id)
    if not lead:
        return
    interactions = db.fetch_interactions(lead_id)
    conversation = lead_conversation_items(lead_id)
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
            user_dot = "🔴" if st.session_state.get("user", "RJ") == "RJ" else "🟢"
            content = f"{fmt_stamp(ts)} — {user_dot} {note.strip() or 'Call completed'}"
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
    st.session_state["page"] = "dashboard"
query_params = st.query_params
if "page" in query_params:
    st.session_state["page"] = query_params["page"]
if "lead_list_collapsed" not in st.session_state:
    st.session_state["lead_list_collapsed"] = False
if "call_completed" not in st.session_state:
    st.session_state["call_completed"] = {}
head_left, head_right = st.columns([4, 1.7])
with head_left:
    st.empty()
with head_right:
    st.markdown("<div class='quick-search-wrap'>", unsafe_allow_html=True)
    if "user" not in st.session_state:
        st.session_state["user"] = "RJ"
    is_gl = st.toggle("", value=st.session_state["user"] == "GL", key="user_toggle", label_visibility="collapsed")
    st.session_state["user"] = "GL" if is_gl else "RJ"
    st.markdown("##### Quick Search")
    render_global_search(all_leads)
    st.markdown("</div>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("""
    <style>
    .nav-item {
        font-size: 16px;
        padding: 6px 0px;
        cursor: pointer;
        color: #444;
    }
    .nav-item:hover {
        color: black;
        font-weight: 600;
    }
    .nav-active {
        font-weight: 700;
        color: black;
    }
    </style>
    """, unsafe_allow_html=True)
    st.markdown("### 🌿 Navigation")

    def nav_item(label: str, key: str) -> None:
        is_active = st.session_state["page"] == key
        class_name = "nav-item nav-active" if is_active else "nav-item"
        st.markdown(
            f"""
            <div class="{class_name}">
                <a href="/?page={key}" target="_self" style="text-decoration:none; color:inherit;">
                    {label}
                </a>
            </div>
            """,
            unsafe_allow_html=True
        )

    nav_item("Dashboard", "dashboard")
    nav_item("Leads", "leads")
    nav_item("Call List", "call_list")
    nav_item("Follow-ups", "followups")

st.divider()
page = st.session_state["page"]

if page == "dashboard":
    calls_completed_today = sum(
        1 for item in all_interactions
        if item["type"] == "call" and datetime.fromisoformat(item["timestamp"]).date() == date.today()
    )
    hot_total = sum(1 for lead in all_leads if lead["status"] == "hot")
    calls_remaining_today = max(0, hot_total - calls_completed_today)
    overdue_items = overdue_followups(all_leads)
    overdue_count = len(overdue_items)
    hot_not_contacted = hot_not_contacted_today(all_leads, all_interactions)
    waiting_approval = followups_waiting_approval(result["followup_queue"], all_interactions)

    st.markdown("### Today’s Priorities")
    pcols = st.columns(5)
    if pcols[0].button(f"Calls remaining today: {calls_remaining_today}", key="prio_calls_remaining"):
        nav_to("Call List")
    if pcols[1].button(f"Calls completed today: {calls_completed_today}", key="prio_calls_completed"):
        nav_to("Call List")
    overdue_label = f"🔴 Overdue follow-ups: {overdue_count}" if overdue_count > 0 else f"Overdue follow-ups: {overdue_count}"
    if pcols[2].button(overdue_label, key="prio_overdue"):
        st.session_state["followup_overdue_only"] = True
        nav_to("Follow-ups")
    if pcols[3].button(f"Hot leads not contacted today: {len(hot_not_contacted)}", key="prio_hot_not_contacted"):
        st.session_state["lead_focus_ids"] = [lead["id"] for lead in hot_not_contacted]
        nav_to("Leads")
    if pcols[4].button(f"Follow-ups waiting approval: {waiting_approval}", key="prio_waiting_approval"):
        st.session_state["followup_overdue_only"] = False
        nav_to("Follow-ups")

    stuck = stuck_leads(all_leads)
    st.markdown("### Leads Stuck")
    if st.button(f"Stuck leads: {len(stuck)}", key="stuck_leads_btn"):
        st.session_state["lead_focus_ids"] = [lead["id"] for lead in stuck]
        nav_to("Leads")

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
    for c, (k, values) in zip(pc, prog.items()):
        v, num, den = values
        c.markdown(
            f"<div class='card'><b>{info_icon(k, 'How effectively warm leads are escalated.')}</b><br>{v:.1f}% ({num} / {den})</div>",
            unsafe_allow_html=True,
        )

    with st.expander("Advanced Metrics"):
        adv = advanced_metrics(all_leads, all_interactions)
        for k, v in adv.items():
            st.markdown(f"**{info_icon(k, 'Operational efficiency benchmark for consistent pipeline progress')}:** {v}", unsafe_allow_html=True)


elif page == "leads":
    st.subheader("Leads")
    q = st.text_input("Search", placeholder="Type name or phone...").strip().lower()
    status_filter = st.multiselect("Optional filters", ["hot", "warm", "cold"], default=[])
    matches = [
        l for l in all_leads
        if (not status_filter or l["status"] in status_filter)
        and (not q or q in l["name"].lower() or q in l["phone"].lower())
    ]
    focus_ids = st.session_state.pop("lead_focus_ids", [])
    if focus_ids:
        focus_set = set(focus_ids)
        matches = [lead for lead in matches if lead["id"] in focus_set]

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

elif page == "call_list":
    st.subheader("Call List")
    call_list = result["call_list"]
    calls_today = sum(1 for i in all_interactions if i["type"] == "call" and datetime.fromisoformat(i["timestamp"]).date() == date.today())
    st.caption(f"Total calls today: {calls_today}")

    completed, remaining = completion_counts(call_list)
    total_calls_today = len(call_list)
    completed_calls_today = completed
    pct_complete = int((completed_calls_today / max(1, total_calls_today)) * 100)
    st.progress(completed_calls_today / max(1, total_calls_today))
    st.caption(f"{completed_calls_today} / {total_calls_today} completed ({pct_complete}%)")
    with st.container(height=640):
        for entry in call_list:
            lid = entry["lead_id"]
            done_key = f"done_{lid}"
            lead = lead_lookup.get(lid)
            _, address_text, situation, _ = parse_timeline_parts(lead["timeline"] if lead else "")
            row_left, row_right = st.columns([5, 1])
            if row_left.button(entry["name"], key=f"open_name_{lid}", type="tertiary", use_container_width=True):
                nav_to("Leads", lid)
            row_left.caption(address_text)
            checked = row_right.checkbox("", key=done_key, label_visibility="collapsed")
            completed, remaining = completion_counts(call_list)
            completed_calls_today = completed
            pct_complete = int((completed_calls_today / max(1, total_calls_today)) * 100)
            st.caption(f"Address: {address_text}")
            st.caption(situation)
            labels = human_intent_labels(entry.get("intent_signals", []))
            st.markdown("".join([f"<span class='pill'>{x}</span>" for x in labels]), unsafe_allow_html=True)
            st.divider()

    completed, remaining = completion_counts(call_list)
    st.write(f"Completed: {completed} / {len(call_list)}")
    st.write(f"Remaining: {remaining}")
    if call_list and completed == len(call_list):
        st.balloons()

elif page == "followups":
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
        sample_warm, sample_cold = generate_sample_followups_from_leads(all_leads)
        warm_items = [(item, lead_lookup[item["lead_id"]]) for item in sample_warm if item["lead_id"] in lead_lookup]
        cold_items = [(item, lead_lookup[item["lead_id"]]) for item in sample_cold if item["lead_id"] in lead_lookup]

    if overdue_only:
        st.markdown("#### Overdue Follow-ups")
        warm_items.sort(key=lambda pair: pair[1].get("next_action_date") or "9999-12-31")
        cold_items.sort(key=lambda pair: pair[1].get("next_action_date") or "9999-12-31")

    for section_name, items in [("Warm", warm_items), ("Cold", cold_items)]:
        st.markdown(f"### {section_name}")
        if not items:
            st.caption("No items")
        for item, lead in items:
            if item["lead_id"] > 0:
                conversation = lead_conversation_items(item["lead_id"])
                history = conversation[-10:]
            else:
                history = []
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
                if history:
                    render_imessage(history, max_items=10)
                else:
                    st.caption("No messages yet")

            key = f"f_msg_{item['lead_id']}"
            if key not in st.session_state:
                st.session_state[key] = item["new_message"]
            st.text_area("Suggestion", key=key, height=80)

            c1, c2, c3 = st.columns(3)
            if c1.button("Approve", key=f"f_app_{item['lead_id']}"):
                if item["lead_id"] > 0:
                    typing_box = st.empty()
                    ts_now = datetime.utcnow().isoformat(timespec="seconds")
                    typing_box.markdown(
                        f"<div class='msg-row msg-left'><div><div class='msg-time'>{fmt_date_time(ts_now)[0]} • {fmt_date_time(ts_now)[1]}</div><div class='msg-bubble-in'><span class='typing-dot'>.</span><span class='typing-dot'>.</span><span class='typing-dot'>.</span></div></div></div>",
                        unsafe_allow_html=True,
                    )
                    time.sleep(0.25)
                    typing_box.empty()
                    ts = datetime.utcnow()
                    send_sms(item["lead_id"], st.session_state[key])
                    approved_text = st.session_state[key]
                    user_value = st.session_state.get("user", "RJ")
                    user_dot = "🔴" if user_value == "RJ" else "🟢"
                    db.add_interaction(item["lead_id"], "text", f"Suggested message approved: {user_dot} {user_value}: approved message \"{approved_text}\"", "outbound", ts=ts)
                    db.add_interaction(item["lead_id"], "note", f"{user_value}: approved message \"{approved_text}\"", "outbound", ts=ts)
                    st.session_state[key] = ""
                    autoplay_audio()
                    st.success("Message sent.")
                st.rerun()
            if c2.button("Edit", key=f"f_edit_{item['lead_id']}"):
                st.info("Edit in suggestion box.")
            if c3.button("Regenerate", key=f"f_regen_{item['lead_id']}"):
                st.session_state[key] = st.session_state[key] + " Just checking if a quick call would be easier."
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
