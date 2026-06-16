from __future__ import annotations

from datetime import date, datetime, timedelta
import base64
import time

import streamlit as st

from crm_engine import db, engine
from crm_engine.followup_engine import generate_followup_message
from crm_engine.messaging_gateway import send_sms, place_call

st.set_page_config(page_title="Silverline Investment Group", layout="wide")
st.markdown("""
<style>
div[data-testid="stAppViewContainer"] .main .block-container {
    max-width: 100% !important;
    padding-top: 1rem !important;
}

div[data-testid="stButton"] button[kind="secondary"] {
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
}

div[data-testid="stButton"] button#home_dashboard_button {
    font-size: 24px !important;
    font-weight: 700 !important;
    text-align: left !important;
    color: black !important;
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
    cursor: pointer !important;
}
</style>
""", unsafe_allow_html=True)

title_col1, title_col2 = st.columns([8, 1])

with title_col1:
    if st.button(
        "Silverline Investment Group 🌿",
        key="home_dashboard_button",
        use_container_width=False
    ):
        st.session_state["current_page"] = "dashboard"
        st.session_state["page"] = "dashboard"
        st.rerun()

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
.msg-bubble-in{background:#e5e5ea;padding:8px 12px;border-radius:18px 18px 18px 4px;max-width:75%;color:#000;font-size:0.95rem;line-height:1.4;}
.msg-bubble-out{background:#0b93f6;padding:8px 12px;border-radius:18px 18px 4px 18px;max-width:75%;color:#fff;font-size:0.95rem;line-height:1.4;}
.msg-time{font-size:.70rem;color:#8e8e93;margin-bottom:3px;text-align:center;width:100%;}
h1 a, h2 a, h3 a, h4 a, h5 a, h6 a {display:none !important;}
[data-testid="stSidebar"] {background-color: #EDEAE5 !important;}
[data-testid="stSidebar"], [data-testid="stSidebar"] p,
[data-testid="stSidebar"] span, [data-testid="stSidebar"] a,
[data-testid="stSidebar"] div {color: #2E2E2E !important;}
input, textarea, [data-baseweb="select"] * {color: #2E2E2E !important;}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea {
  background-color: #ffffff !important;
  color: #2E2E2E !important;
}
.tooltip-wrap{position:relative;display:inline-flex;align-items:center;gap:6px;}
.tooltip-icon{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border:1px solid #8b8b8b;border-radius:50%;font-size:11px;line-height:1;color:#5d5d5d;cursor:default;}
.tooltip-tip{visibility:hidden;opacity:0;transition:opacity .15s;position:absolute;z-index:20;left:20px;top:-6px;background:#222;color:#fff;padding:6px 8px;border-radius:6px;font-size:12px;white-space:nowrap;}
.tooltip-wrap:hover .tooltip-tip{visibility:visible;opacity:1;}
.quick-search-wrap {margin-top: -34px;}
[data-testid="stSidebar"] [data-testid="stButton"] button {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  color: #444 !important;
  font-size: 16px !important;
  font-weight: 400 !important;
  padding: 4px 0 !important;
  text-align: left !important;
  width: 100% !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
  color: #000 !important;
  font-weight: 600 !important;
  background: transparent !important;
}
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
.thread-sel{background:#e8f0ea!important;border-left:3px solid #6B8F71!important;border-radius:6px!important;font-weight:700!important;}
.call-row{display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid #eee;}
.call-icon{font-size:1.3rem;flex-shrink:0;}
.call-meta{flex:1;min-width:0;}
.call-name{font-weight:600;font-size:.95rem;}
.call-preview{font-size:.82rem;color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.call-ts{font-size:.78rem;color:#888;white-space:nowrap;flex-shrink:0;}
</style>
"""

PAGES = ["Dashboard", "Leads", "Call List", "Follow-ups", "Messages"]


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
    st.query_params["page"] = normalized
    if normalized == "leads" and lead_id is None:
        st.session_state["lead_list_collapsed"] = False
        st.session_state.pop("selected_lead_id", None)
    if lead_id is not None:
        st.session_state["selected_lead_id"] = lead_id
        st.session_state["lead_list_collapsed"] = True
    st.rerun()


def go_to_call_list() -> None:
    st.session_state["current_page"] = "call_list"
    st.session_state["page"] = "call_list"


def open_lead_from_search(lead_id: int) -> None:
    st.session_state["current_page"] = "leads"
    st.session_state["page"] = "leads"
    st.session_state["selected_lead_id"] = lead_id
    st.session_state["lead_list_collapsed"] = True


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
            st.button(
                item["name"],
                key=f"quick_search_{item['id']}",
                on_click=open_lead_from_search,
                args=(item["id"],),
            )


def get_all_leads() -> list[dict]:
    return db.fetch_leads()


def get_lead_by_id(lead_id: int | None, leads: list[dict]) -> dict | None:
    if lead_id is None:
        return None
    for lead in leads:
        if lead["id"] == lead_id:
            return lead
    return None


def build_followups(leads: list[dict]) -> tuple[list[dict], list[dict]]:
    warm_items: list[dict] = []
    cold_items: list[dict] = []
    for lead in leads:
        if lead["status"] not in {"warm", "cold"}:
            continue
        interactions = db.fetch_interactions(lead["id"])
        eval_ = engine.evaluate_lead(lead, interactions)
        texts = [i for i in interactions if i["type"] == "text"]
        last_message = texts[0]["content"] if texts else ""
        draft = generate_followup_message(
            lead=lead,
            last_message=last_message,
            ignored_texts=eval_.ignored_outbound_texts,
            strong_intent=eval_.strong_intent,
        )
        entry = {
            "lead_id": lead["id"],
            "lead_name": lead["name"],
            "new_message": draft.message,
        }
        if lead["status"] == "warm":
            warm_items.append(entry)
        else:
            cold_items.append(entry)
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


def toggle_call_completed(lead_id: int) -> None:
    completed = st.session_state.get(f"call_complete_{lead_id}", False)
    st.session_state["completed_calls"][lead_id] = completed
    db.set_daily_call_completion(lead_id, date.today().isoformat(), completed)


def completion_counts(call_list: list[dict]) -> tuple[int, int]:
    completed = sum(1 for entry in call_list if st.session_state["completed_calls"].get(entry["lead_id"], False))
    remaining = max(0, len(call_list) - completed)
    return completed, remaining


def render_imessage(items: list[dict], max_items: int | None = None) -> None:
    if max_items is None:
        max_items = len(items)
    # Show the most recent max_items (items are sorted oldest→newest)
    recent = items[-max_items:]
    last_ts = None
    for msg in recent:
        try:
            msg_dt = datetime.fromisoformat(msg["timestamp"])
        except Exception:
            msg_dt = None

        # Show centered timestamp when gap > 5 min or first message
        if msg_dt:
            show_ts = (last_ts is None) or ((msg_dt - last_ts).total_seconds() > 300)
            last_ts = msg_dt
        else:
            show_ts = True

        if show_ts and msg_dt:
            d, t = fmt_date_time(msg["timestamp"])
            ts_label = f"{d} • {t}"
            st.markdown(
                f"<div style='text-align:center;font-size:0.70rem;color:#8e8e93;"
                f"margin:8px 0 4px 0'>{ts_label}</div>",
                unsafe_allow_html=True,
            )

        outgoing = msg["direction"] == "outbound"
        bubble = "msg-bubble-out" if outgoing else "msg-bubble-in"
        row = "msg-right" if outgoing else "msg-left"
        if msg["type"] == "call":
            prefix = "Outbound Call" if outgoing else "Inbound Call"
            label = f"{prefix} - {msg['content']}"
        else:
            label = msg["content"]

        # Audio player for recorded calls / voicemails
        rec_url = msg.get("recording_url") or ""
        audio_html = ""
        if rec_url:
            # Extract recording SID (last path segment, strip any extension)
            rec_sid = rec_url.rstrip("/").split("/")[-1].split(".")[0]
            audio_html = (
                f"<br><audio controls style='width:100%;margin-top:6px;height:32px'>"
                f"<source src='http://localhost:5001/recordings/{rec_sid}' type='audio/mpeg'>"
                f"</audio>"
            )

        st.markdown(
            f"<div class='msg-row {row}'><div class='{bubble}'>{label}{audio_html}</div></div>",
            unsafe_allow_html=True,
        )


def lead_conversation_items(lead_id: int) -> list[dict]:
    interactions = db.fetch_interactions(lead_id)
    # Calls and texts only — notes live in the Notes panel, not the conversation
    items = [i for i in interactions if i["type"] in ("call", "text")]
    return sorted(items, key=lambda x: x["timestamp"])


def update_status(lead_id: int, key: str) -> None:
    db.update_lead(lead_id, {"status": st.session_state[key]})


def auto_save_field(lead_id: int, field: str, key: str) -> None:
    db.update_lead(lead_id, {field: st.session_state[key]})


def _build_display_result() -> dict:
    """
    Read-only dashboard data builder.
    Uses DB-stored lead status as source of truth so manual overrides
    (e.g. demoting a lead from hot → warm) are never overwritten on render.
    engine.evaluate_all_leads() writes suggested_status back to DB on every
    call — this version evaluates for signals/call-list/follow-ups without
    any DB writes.
    """
    from collections import Counter

    leads = db.fetch_leads()
    all_ixns: dict = {l["id"]: db.fetch_interactions(l["id"]) for l in leads}
    leads_with_eval = [
        (lead, engine.evaluate_lead(lead, all_ixns.get(lead["id"], [])))
        for lead in leads
    ]

    # Call list: DB-hot leads only, priority-sorted
    hot_pairs = [(l, e) for l, e in leads_with_eval if l["status"] == "hot"]
    sorted_hot = sorted(hot_pairs, key=lambda t: engine._priority_score(*t), reverse=True)
    call_list = [
        {
            "lead_id": l["id"],
            "name": l["name"],
            "phone": l["phone"],
            "priority_score": engine._priority_score(l, e),
            "intent_signals": e.intent_signals,
        }
        for l, e in sorted_hot
    ]

    # Follow-up queue: non-hot DB leads that are due today or overdue
    today_str = date.today().isoformat()
    followups: list = []
    for lead, evaluation in leads_with_eval:
        if lead["status"] == "hot":
            continue
        next_action = lead.get("next_action_date") or today_str
        if next_action > today_str:
            continue
        ixns = all_ixns.get(lead["id"], [])
        ignored = engine.ignored_outbound_texts(ixns)
        last_outbound = next((i for i in ixns if i["direction"] == "outbound"), None)
        # No AI call here — suggestions are generated lazily on the Follow-ups page
        followups.append({
            "lead_id": lead["id"],
            "lead_name": lead["name"],
            "last_message": last_outbound["content"] if last_outbound else "",
            "ignored_texts": ignored,
            "strong_intent": bool(evaluation.intent_signals),
            "options": ["approve", "edit", "skip", "call instead"],
            "touch_notice": evaluation.touch_message,
            "ghosting_signal": evaluation.ghosting_signal,
        })

    counts = Counter(l["status"] for l in leads if l["status"] in ("hot", "warm", "cold"))
    risk_leads = [
        {"lead_id": l["id"], "name": l["name"], "risk": e.ghosting_signal}
        for l, e in leads_with_eval if e.ghosting_signal
    ]
    return {
        "summary": {
            "hot": counts["hot"],
            "warm": counts["warm"],
            "cold": counts["cold"],
            "follow_ups": len(followups),
            "risk_leads": len(risk_leads),
        },
        "followup_queue": followups,
        "call_list": call_list,
        "risk_leads": risk_leads,
    }


def save_address(lead_id: int, key: str) -> None:
    new_address = st.session_state[key].strip()
    lead = db.fetch_lead(lead_id)
    if not lead:
        return
    timeline_prefix, _, situation, notes = parse_timeline_parts(lead["timeline"] or "")
    parts = [timeline_prefix] if timeline_prefix else []
    if new_address:
        parts.append(f"Address: {new_address}")
    if situation:
        parts.append(f"Situation: {situation}")
    if notes:
        parts.append(f"Notes: {notes}")
    db.update_lead(lead_id, {"timeline": ". ".join(parts)})


def save_timeline(lead_id: int, key: str) -> None:
    """Save the editable timeline prefix while preserving Address/Situation/Notes parts."""
    new_prefix = st.session_state[key].strip()
    lead = db.fetch_lead(lead_id)
    if not lead:
        return
    _, address, situation, notes = parse_timeline_parts(lead["timeline"] or "")
    parts = [new_prefix] if new_prefix else []
    if address:
        parts.append(f"Address: {address}")
    if situation:
        parts.append(f"Situation: {situation}")
    if notes:
        parts.append(f"Notes: {notes}")
    db.update_lead(lead_id, {"timeline": ". ".join(parts)})


def render_notes(lead_id: int, interactions: list[dict]) -> None:
    st.markdown("**Notes**")
    notes = sorted(
        [i for i in interactions if i["type"] == "note"],
        key=lambda x: x["timestamp"],
        reverse=True,
    )
    if not notes:
        st.caption("No notes yet")

    for note in notes:
        nid = note["id"]
        edit_key = f"edit_note_{nid}"
        # Format the stored timestamp as a readable date header
        try:
            dt = datetime.fromisoformat(note["timestamp"])
            note_date = dt.strftime("%m/%d/%Y")
            note_time = dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            note_date = note["timestamp"][:10]
            note_time = ""

        if st.session_state.get(edit_key):
            new_val = st.text_area("", value=note["content"], key=f"note_input_{nid}", height=100)
            if st.button("Save", key=f"note_save_{nid}"):
                with db.get_conn() as conn:
                    conn.execute("UPDATE interactions SET content=? WHERE id=?", (new_val, nid))
                st.session_state[edit_key] = False
                st.rerun()
        else:
            st.markdown(
                f"<div style='font-size:0.75rem;color:#888;margin-bottom:2px'>"
                f"📅 {note_date}"
                f"{(' · ' + note_time) if note_time else ''}"
                f"</div>",
                unsafe_allow_html=True,
            )
            row_left, row_edit, row_del = st.columns([14, 1, 1])
            row_left.markdown(
                f"<div class='note-bubble' style='white-space:pre-wrap'>{note['content']}</div>",
                unsafe_allow_html=True,
            )
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
            st.markdown("<hr style='margin:4px 0;border-color:#eee'>", unsafe_allow_html=True)

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
        st.warning("Lead not found — data mismatch")
        st.stop()
        return
    interactions = db.fetch_interactions(lead_id)
    conversation = lead_conversation_items(lead_id)
    timeline_text, address_text, situation_text, _ = parse_timeline_parts(lead["timeline"])

    st.markdown(f"### {lead['name']}")
    c_top1, c_top2 = st.columns(2)
    c_top1.markdown(f"**Phone:** {lead['phone']}")
    c_top2.markdown(f"**Stage:** {human_stage(lead['conversation_stage'])}")

    c_edit1, c_edit2, c_edit3, c_edit4 = st.columns(4)
    _status_opts = ["hot", "warm", "cold", "dead", "contract"]
    _status_idx = _status_opts.index(lead["status"]) if lead["status"] in _status_opts else 0
    c_edit1.selectbox(
        "Status",
        _status_opts,
        index=_status_idx,
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
        on_change=save_timeline,
        args=(lead_id, f"timeline_{lead_id}"),
    )
    c_edit4.text_input(
        "Address",
        value=address_text,
        key=f"address_{lead_id}",
        on_change=save_address,
        args=(lead_id, f"address_{lead_id}"),
    )

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"**Conversation** ({len(conversation)} messages)")
        with st.container(height=420):
            render_imessage(conversation, max_items=len(conversation))
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

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Send a Text**")
        manual_msg_key = f"manual_text_{lead_id}"
        st.text_area("Message", key=manual_msg_key, height=80, label_visibility="collapsed",
                     placeholder="Type a custom message to send right now…")
        send_col1, send_col2 = st.columns([1, 1])
        if send_col1.button("Send Text", key=f"manual_send_{lead_id}"):
            text_to_send = st.session_state.get(manual_msg_key, "").strip()
            if text_to_send:
                result = send_sms(lead_id, text_to_send)
                if result.get("sent"):
                    st.session_state.pop(manual_msg_key, None)
                    st.success("Sent!")
                else:
                    st.error(f"Send failed: {result.get('error', 'unknown error')}")
                st.rerun()
            else:
                st.warning("Type a message first.")
        if send_col2.button("Call This Lead", key=f"click_to_call_{lead_id}"):
            result = place_call(lead_id)
            if result.get("called"):
                st.success("Calling your phone now — answer it to connect.")
            else:
                st.error(f"Call failed: {result.get('error', 'unknown error')}")
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
result = _build_display_result()
all_leads = get_all_leads()
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
if "completed_calls" not in st.session_state:
    today_iso = date.today().isoformat()
    st.session_state["completed_calls"] = {
        lid: True
        for lid in db.get_daily_call_completions(today_iso)
    }
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

    def nav_item(label: str, page_key: str) -> None:
        is_active = st.session_state["page"] == page_key
        btn_style = "primary" if is_active else "secondary"
        if st.button(label, key=f"nav_btn_{page_key}", use_container_width=True, type=btn_style):
            nav_to(page_key)

    nav_item("Dashboard", "dashboard")
    nav_item("Leads", "leads")
    nav_item("Call List", "call_list")
    nav_item("Follow-ups", "followups")

    # Messages nav item with unread badge
    _last_seen = st.session_state.get("messages_last_seen_ts") or (
        datetime.utcnow() - timedelta(hours=24)
    ).isoformat()
    _unread_count = sum(
        1 for _i in all_interactions
        if _i["direction"] == "inbound"
        and _i["type"] in ("text", "call")
        and _i["timestamp"] > _last_seen
    )
    _msg_nav_col, _msg_badge_col = st.columns([5, 1])
    with _msg_nav_col:
        nav_item("Messages", "messages")
    with _msg_badge_col:
        if _unread_count > 0:
            _badge_label = str(_unread_count) if _unread_count < 100 else "99+"
            st.markdown(
                f"<div style='background:#ff3b30;color:#fff;border-radius:50%;"
                f"min-width:20px;height:20px;display:flex;align-items:center;"
                f"justify-content:center;font-size:11px;font-weight:700;"
                f"padding:0 4px;margin-top:6px'>{_badge_label}</div>",
                unsafe_allow_html=True,
            )

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
    waiting_approval = followups_waiting_approval(result["followup_queue"], all_interactions)

    # ── New replies banner ────────────────────────────────────────────────────
    recent_replies = db.fetch_recent_inbound_texts(since_hours=24)
    if recent_replies:
        # Dedupe by lead
        seen_leads: dict[int, dict] = {}
        for r in recent_replies:
            if r["lead_id"] not in seen_leads:
                seen_leads[r["lead_id"]] = r
        reply_leads = list(seen_leads.values())
        reply_word = "reply" if len(reply_leads) == 1 else "replies"
        st.markdown(
            f"<div style='background:#edf7ed;border-left:4px solid #4caf50;"
            f"padding:10px 14px;border-radius:6px;margin-bottom:12px'>"
            f"<b>💬 {len(reply_leads)} new {reply_word} in the last 24 hours</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
        reply_cols = st.columns(min(len(reply_leads), 4))
        for i, r in enumerate(reply_leads[:4]):
            with reply_cols[i]:
                preview = r["content"][:40] + ("..." if len(r["content"]) > 40 else "")
                label = r["name"] + "\n" + preview
                if st.button(label, key="reply_badge_" + str(r["lead_id"])):
                    nav_to("leads", r["lead_id"])

    # ── Missed calls banner ───────────────────────────────────────────────────
    recent_missed_calls = db.fetch_unacknowledged_missed_calls()
    if recent_missed_calls:
        call_word = "call" if len(recent_missed_calls) == 1 else "calls"
        st.markdown(
            f"<div style='background:#fdecea;border-left:4px solid #e53935;"
            f"padding:10px 14px;border-radius:6px;margin-bottom:12px'>"
            f"<b>📞 {len(recent_missed_calls)} missed {call_word} you haven't followed up on</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
        missed_cols = st.columns(min(len(recent_missed_calls), 4))
        for i, c in enumerate(recent_missed_calls[:4]):
            with missed_cols[i]:
                d, t = fmt_date_time(c["timestamp"])
                label = f"{c['name']}\n{c['phone']}\n{d} {t}"
                if st.button(label, key="missed_call_badge_" + str(c["id"])):
                    nav_to("leads", c["lead_id"])

    # ── Unmatched inbox (texts/voicemails from unknown numbers) ──────────────
    unmatched = db.fetch_unmatched_inbox()
    if unmatched:
        word = "message" if len(unmatched) == 1 else "messages"
        with st.expander(f"📥 {len(unmatched)} unmatched {word} from unknown numbers", expanded=False):
            for entry in unmatched:
                d, t = fmt_date_time(entry["timestamp"])
                icon = "🎙️" if entry["type"] == "voicemail" else "💬"
                st.markdown(f"{icon} **{entry['phone']}** · {d} {t}")
                st.caption(entry["content"])
                u_col1, u_col2, u_col3 = st.columns([2, 1, 1])
                name_key = f"unmatched_name_{entry['id']}"
                u_col1.text_input("Name", key=name_key, placeholder="Add as lead — enter name", label_visibility="collapsed")
                if u_col2.button("Add as lead", key=f"unmatched_convert_{entry['id']}"):
                    new_name = st.session_state.get(name_key, "").strip()
                    if new_name:
                        new_lead_id = db.convert_unmatched_to_lead(entry["id"], new_name)
                        st.success(f"Created lead: {new_name}")
                        nav_to("Leads", new_lead_id)
                    else:
                        st.warning("Enter a name first.")
                if u_col3.button("Dismiss", key=f"unmatched_dismiss_{entry['id']}"):
                    db.mark_unmatched_handled(entry["id"])
                    st.rerun()
                st.markdown("---")

    st.markdown("### Today’s Priorities")
    pcols = st.columns(5)
    pcols[0].button(
        f"Calls remaining today: {calls_remaining_today}",
        key="calls_remaining_today_button",
        on_click=go_to_call_list,
    )
    if pcols[1].button(f"Calls completed today: {calls_completed_today}", key="prio_calls_completed"):
        nav_to("Call List")
    overdue_label = f"🔴 Overdue follow-ups: {overdue_count}" if overdue_count > 0 else f"Overdue follow-ups: {overdue_count}"
    if pcols[2].button(overdue_label, key="prio_overdue"):
        st.session_state["followup_overdue_only"] = True
        nav_to("Follow-ups")
    pcols[3].empty()
    if pcols[4].button(f"Follow-ups waiting approval: {waiting_approval}", key="prio_waiting_approval"):
        st.session_state["followup_overdue_only"] = False
        nav_to("Follow-ups")

    stuck = stuck_leads(all_leads)
    st.markdown("### Leads Stuck")
    if st.button(f"Stuck leads: {len(stuck)}", key="stuck_leads_btn"):
        st.session_state["lead_focus_ids"] = [lead["id"] for lead in stuck]
        nav_to("Leads")

    summary = result["summary"]
    warm_followups = sum(1 for i in result["followup_queue"]
                        if lead_lookup.get(i["lead_id"], {}).get("status") == "warm")
    cold_followups = sum(1 for i in result["followup_queue"]
                        if lead_lookup.get(i["lead_id"], {}).get("status") == "cold")

    cols = st.columns(6)
    metric_items = [
        ("Hot",               summary["hot"],    "leads", "hot"),
        ("Warm",              summary["warm"],   "leads", "warm"),
        ("Cold",              summary["cold"],   "leads", "cold"),
        ("Warm Follow-ups",   warm_followups,    "followups", None),
        ("Cold Follow-ups",   cold_followups,    "followups", None),
        ("Risk Leads",        summary["risk_leads"], "leads", None),
    ]
    for c, (label, value, page, status_filter) in zip(cols, metric_items):
        c.markdown(f"<div class='metric'><div>{label}</div><h3>{value}</h3></div>", unsafe_allow_html=True)
        btn_key = f"metric_btn_{label.replace(' ', '_').lower()}"
        if c.button("View", key=btn_key):
            if status_filter:
                st.session_state["lead_status_filter"] = [status_filter]
            else:
                st.session_state.pop("lead_status_filter", None)
            nav_to(page)

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
    preset_filter = st.session_state.pop("lead_status_filter", None)
    status_filter = st.multiselect(
        "Filter by status",
        ["hot", "warm", "cold"],
        default=preset_filter or [],
        key="lead_status_multiselect",
    )
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
                    if st.button(lead["name"], key=f"lead_{lead['id']}"):
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
            lead = lead_lookup.get(lid)
            _, address_text, _, _ = parse_timeline_parts(lead["timeline"] if lead else "")
            # Strip junk from address (remove "Notes: ..." and "Folder=..." parts)
            clean_address = address_text.split(". Notes:")[0].split(". Folder")[0].strip()

            _call_key = f"call_complete_{lid}"
            if _call_key not in st.session_state:
                st.session_state[_call_key] = st.session_state["completed_calls"].get(lid, False)
            is_done = st.session_state["completed_calls"].get(lid, False)

            name_col, check_col = st.columns([11, 1])
            with name_col:
                # Name + phone on same line
                st.markdown(
                    f"<div style='display:flex;align-items:baseline;gap:12px'>"
                    f"<span style='font-size:1.05rem;font-weight:600;"
                    f"{'color:#aaa;text-decoration:line-through' if is_done else 'color:#1a1a1a'}'>"
                    f"{entry['name']}</span>"
                    f"<span style='font-size:0.9rem;color:#555'>{lead['phone'] if lead else ''}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if clean_address:
                    st.markdown(
                        f"<div style='font-size:0.82rem;color:#777;margin:1px 0 4px 0'>"
                        f"📍 {clean_address}</div>",
                        unsafe_allow_html=True,
                    )
                labels = human_intent_labels(entry.get("intent_signals", []))
                if labels:
                    st.markdown(
                        "".join([f"<span class='pill'>{x}</span>" for x in labels]),
                        unsafe_allow_html=True,
                    )
                if st.button("Open lead", key=f"open_name_{lid}", type="tertiary"):
                    nav_to("Leads", lid)

            with check_col:
                st.checkbox(
                    "",
                    key=_call_key,
                    on_change=toggle_call_completed,
                    args=(lid,),
                    label_visibility="collapsed",
                )
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
        lead = get_lead_by_id(item["lead_id"], all_leads)
        if not lead:
            continue
        if overdue_only and lead.get("next_action_date") and lead["next_action_date"] >= date.today().isoformat():
            continue
        (warm_items if lead["status"] == "warm" else cold_items).append((item, lead))

    if not warm_items and not cold_items:
        warm_built, cold_built = build_followups(all_leads)
        warm_items = [(item, lead_lookup[item["lead_id"]]) for item in warm_built if item["lead_id"] in lead_lookup]
        cold_items = [(item, lead_lookup[item["lead_id"]]) for item in cold_built if item["lead_id"] in lead_lookup]

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
                history = conversation
            else:
                history = []
            _, address, situation, _ = parse_timeline_parts(lead["timeline"])
            lid = item["lead_id"]
            msg_key = f"f_msg_{lid}"
            regen_key = f"f_regen_flag_{lid}"
            flags_key = f"f_flags_{lid}"
            ctx_key = f"f_ctx_{lid}"

            def _run_generation(target_lead, target_item, target_msg_key, target_flags_key, force=False, extra_context=""):
                import json as _json
                ixns_g = db.fetch_interactions(target_lead["id"])
                ixn_count = len(ixns_g)

                # Use cached suggestion if interactions haven't changed (unless forced)
                if not force:
                    cached = db.get_followup_suggestion(target_lead["id"])
                    if cached and cached["interaction_count"] == ixn_count:
                        st.session_state[target_msg_key] = cached["message"]
                        st.session_state[target_flags_key] = _json.loads(cached["flags"])
                        return

                draft = generate_followup_message(
                    lead=target_lead,
                    last_message=target_item["last_message"],
                    ignored_texts=target_item["ignored_texts"],
                    strong_intent=target_item["strong_intent"],
                    conversation=ixns_g,
                    extra_context=extra_context,
                )
                st.session_state[target_msg_key] = draft.message
                st.session_state[target_flags_key] = draft.flags
                db.save_followup_suggestion(
                    target_lead["id"], draft.message, draft.flags,
                    draft.tone, ixn_count,
                )

            # Handle regenerate (force=True skips cache)
            if st.session_state.pop(regen_key, False):
                with st.spinner("Regenerating..."):
                    _run_generation(lead, item, msg_key, flags_key, force=True,
                                    extra_context=st.session_state.get(ctx_key, ""))

            # Lazy-generate on first view (uses cache if available)
            if msg_key not in st.session_state:
                with st.spinner(f"Analyzing {item['lead_name']}..."):
                    _run_generation(lead, item, msg_key, flags_key)

            flags = st.session_state.get(flags_key, [])

            st.markdown("<div class='card'>", unsafe_allow_html=True)
            if lid > 0 and st.button(f"{item['lead_name']}", key=f"f_name_{lid}", type="tertiary"):
                nav_to("Leads", lid)
            if lid <= 0:
                st.markdown(f"**{item['lead_name']}**")
            st.caption(f"Phone: {lead.get('phone', 'N/A')}  ·  {address}")

            if flags:
                for flag in flags:
                    st.markdown(
                        f"<div style='font-size:0.82rem;padding:4px 8px;margin:2px 0;"
                        f"background:#fafafa;border-left:3px solid #b0976a;"
                        f"border-radius:4px'>{flag}</div>",
                        unsafe_allow_html=True,
                    )

            with st.container(height=180):
                if history:
                    render_imessage(history, max_items=len(history))
                else:
                    st.caption("No conversation yet")

            st.text_area("Suggested text", key=msg_key, height=75)

            c1, c2, c_ctx, c3 = st.columns([2, 1, 3, 1])
            _SNOOZE_DAYS = {"warm": 5, "cold": 30}

            if c1.button("Approve & Send", key=f"f_app_{lid}"):
                if lid > 0:
                    ts = datetime.utcnow()
                    approved_text = st.session_state[msg_key]
                    send_sms(lid, approved_text)  # logs the text interaction internally
                    user_value = st.session_state.get("user", "RJ")
                    user_dot = "🔴" if user_value == "RJ" else "🟢"
                    db.add_interaction(lid, "note", f"{user_dot} {user_value} approved & sent follow-up", "outbound", ts=ts)
                    # Snooze: hide lead until next follow-up window
                    lead_status = lead.get("status", "warm")
                    snooze = _SNOOZE_DAYS.get(lead_status, 5)
                    next_date = (date.today() + timedelta(days=snooze)).isoformat()
                    db.update_lead(lid, {"next_action_date": next_date})
                    st.session_state.pop(msg_key, None)
                    st.session_state.pop(flags_key, None)
                    autoplay_audio()
                    st.success(f"Sent! Snoozed {snooze} days.")
                st.rerun()
            if c2.button("Edit", key=f"f_edit_{lid}"):
                st.info("Edit the text above, then hit Approve & Send.")

            c_ctx.text_input(
                "Context",
                key=ctx_key,
                placeholder="e.g. she just called about the shed price",
                label_visibility="collapsed",
            )

            def _request_regen(lead_id=lid):
                st.session_state[f"f_regen_flag_{lead_id}"] = True

            c3.button("Regenerate", key=f"f_regen_{lid}", on_click=_request_regen)
            st.markdown("</div>", unsafe_allow_html=True)

elif page == "messages":
    # Mark all current inbound messages as seen (clears the badge)
    st.session_state["messages_last_seen_ts"] = datetime.utcnow().isoformat()

    # ── header ────────────────────────────────────────────────────────────────
    hdr_left, hdr_right = st.columns([6, 1])
    hdr_left.subheader("💬 Messages & Calls")
    if hdr_right.button("🔄 Refresh", key="msg_refresh_btn"):
        st.rerun()

    # ── search bar ────────────────────────────────────────────────────────────
    msg_q = st.text_input(
        "Search",
        placeholder="Search by name, phone, or message content...",
        label_visibility="collapsed",
        key="msg_search_q",
    ).strip().lower()

    # ── build thread list from all_interactions ───────────────────────────────
    _texts_by_lead: dict[int, list[dict]] = {}
    for _ixn in all_interactions:
        if _ixn["type"] == "text":
            _texts_by_lead.setdefault(_ixn["lead_id"], []).append(_ixn)

    _threads: list[dict] = []
    for _lid, _txts in _texts_by_lead.items():
        _lead = lead_lookup.get(_lid)
        if not _lead:
            continue
        _latest = max(_txts, key=lambda x: x["timestamp"])
        _threads.append({
            "lead_id": _lid,
            "lead_name": _lead["name"],
            "phone": _lead["phone"],
            "status": _lead["status"],
            "last_text": _latest["content"],
            "last_ts": _latest["timestamp"],
            "direction": _latest["direction"],
        })
    _threads.sort(key=lambda t: t["last_ts"], reverse=True)

    if msg_q:
        _threads = [
            t for t in _threads
            if msg_q in t["lead_name"].lower()
            or msg_q in t["phone"].lower()
            or msg_q in t["last_text"].lower()
        ]

    # ── session state for selected conversation ───────────────────────────────
    if "msg_selected_lead" not in st.session_state:
        st.session_state["msg_selected_lead"] = None

    # ── two-column layout: thread list | conversation ─────────────────────────
    thread_col, convo_col = st.columns([1, 2.4])

    with thread_col:
        st.markdown("**Conversations**")
        with st.container(height=540):
            if not _threads:
                st.caption("No text conversations yet.")
            for _t in _threads:
                _is_sel = st.session_state["msg_selected_lead"] == _t["lead_id"]
                _preview = (_t["last_text"][:48] + "…") if len(_t["last_text"]) > 48 else _t["last_text"]
                _d, _tm = fmt_date_time(_t["last_ts"])
                _dir_icon = "← " if _t["direction"] == "inbound" else "→ "
                _btn_type = "secondary" if _is_sel else "tertiary"
                _label = ("● " if _is_sel else "") + _t["lead_name"]
                if st.button(_label, key=f"msg_thread_{_t['lead_id']}", use_container_width=True, type=_btn_type):
                    st.session_state["msg_selected_lead"] = _t["lead_id"]
                    st.rerun()
                st.caption(f"{_dir_icon}{_preview}  ·  {_d} {_tm}")

    with convo_col:
        _sel_lid = st.session_state.get("msg_selected_lead")
        if _sel_lid is None:
            st.info("← Select a conversation to open it.")
        else:
            _sel_lead = lead_lookup.get(_sel_lid)
            if not _sel_lead:
                st.warning("Lead not found.")
            else:
                # Conversation header
                _ch_left, _ch_right = st.columns([4, 1])
                _ch_left.markdown(f"**{_sel_lead['name']}** · `{_sel_lead['phone']}`")
                if _ch_right.button("Open Lead →", key=f"msg_open_lead_{_sel_lid}", type="secondary"):
                    nav_to("Leads", _sel_lid)

                # All texts + calls for this lead, oldest-first for iMessage display
                _convo_ixns = db.fetch_interactions(_sel_lid)
                _convo_items = [i for i in _convo_ixns if i["type"] in ("text", "call")]
                _convo_asc = list(reversed(_convo_items))  # fetch returns newest-first; reverse for display

                with st.container(height=420):
                    if _convo_asc:
                        render_imessage(_convo_asc, max_items=len(_convo_asc))
                    else:
                        st.caption("No messages yet.")

                # Compose row
                st.markdown("---")
                _compose_key = f"msg_compose_{_sel_lid}"
                _msg_text = st.text_area(
                    "Compose",
                    key=_compose_key,
                    height=72,
                    placeholder="iMessage  ✉",
                    label_visibility="collapsed",
                )
                _sc1, _sc2 = st.columns([3, 1])
                if _sc1.button("Send ↑", key=f"msg_send_{_sel_lid}", type="primary", use_container_width=True):
                    if (_msg_text or "").strip():
                        _res = send_sms(_sel_lid, _msg_text.strip())
                        if _res.get("sent"):
                            st.session_state.pop(_compose_key, None)
                            st.toast(f"✓ Sent to {_sel_lead['name']}")
                        else:
                            st.error(f"Send failed: {_res.get('error')}")
                        st.rerun()
                    else:
                        st.warning("Type a message first.")
                if _sc2.button("📞 Call", key=f"msg_call_{_sel_lid}", use_container_width=True):
                    _cr = place_call(_sel_lid)
                    if _cr.get("called"):
                        st.toast("📞 Calling — your phone will ring first, then bridges to lead.")
                    else:
                        st.error(f"Call failed: {_cr.get('error')}")

    # ── Recent Calls panel ────────────────────────────────────────────────────
    st.divider()
    st.markdown("### 📞 Recent Calls")

    _all_calls = sorted(
        [i for i in all_interactions if i["type"] == "call"],
        key=lambda x: x["timestamp"],
        reverse=True,
    )[:60]

    if not _all_calls:
        st.caption("No calls logged yet.")
    else:
        for _c in _all_calls:
            _cl = lead_lookup.get(_c["lead_id"])
            _cl_name = _cl["name"] if _cl else f"Lead #{_c['lead_id']}"
            _cd, _ct = fmt_date_time(_c["timestamp"])
            _lcontent = _c["content"].lower()
            if "missed" in _lcontent:
                _icon = "❌"
            elif "voicemail" in _lcontent:
                _icon = "📨"
            elif _c["direction"] == "inbound":
                _icon = "📲"
            else:
                _icon = "📤"
            _call_preview = (_c["content"][:100] + "…") if len(_c["content"]) > 100 else _c["content"]
            _if_search = not msg_q or (
                msg_q in _cl_name.lower()
                or msg_q in _c["content"].lower()
            )
            if not _if_search:
                continue

            _rec_url = _c.get("recording_url") or ""
            _has_rec = bool(_rec_url)

            # Row: icon + name/preview + timestamp + open-thread button
            _cr1, _cr2, _cr3 = st.columns([0.4, 6, 1.6])
            _cr1.markdown(f"<div style='font-size:1.4rem;padding-top:4px'>{_icon}</div>", unsafe_allow_html=True)
            with _cr2:
                st.markdown(
                    f"<div style='font-weight:600;font-size:.95rem'>{_cl_name}</div>"
                    f"<div style='font-size:.82rem;color:#555'>{_call_preview}</div>",
                    unsafe_allow_html=True,
                )
                if _has_rec:
                    _rec_sid = _rec_url.rstrip("/").split("/")[-1].split(".")[0]
                    st.markdown(
                        f"<audio controls style='width:100%;height:32px;margin-top:4px'>"
                        f"<source src='http://localhost:5001/recordings/{_rec_sid}' type='audio/mpeg'>"
                        f"</audio>",
                        unsafe_allow_html=True,
                    )
            with _cr3:
                st.caption(f"{_cd} {_ct}")
                if _cl:
                    if st.button("Open thread", key=f"msg_call_open_{_c['id']}", type="tertiary"):
                        st.session_state["msg_selected_lead"] = _cl["id"]
                        st.rerun()

            st.divider()
