from __future__ import annotations

from datetime import date, datetime, timedelta
import base64
import html
import io
import os
import re
import time
import urllib.parse

import streamlit as st
import streamlit.components.v1 as components

from crm_engine import db, engine
from crm_engine.followup_engine import generate_followup_message
from crm_engine.messaging_gateway import send_sms, place_call, place_call_number
from crm_engine.dialer_import import parse_contacts
from crm_engine import caller_id_pool
from crm_engine import buyer_match
from crm_engine.email_ui import render_email_review, catch_up_if_due
from crm_engine.gmail_client import MAILBOX as EMAIL_MAILBOX

st.set_page_config(page_title="Silverline CRM", page_icon="🌿", layout="wide")

# Auto-reconnect: if the Streamlit server restarts (which severs this tab's
# live connection and silently freezes it), detect the drop-then-recovery via
# the health endpoint and reload the tab so it never shows a stale view.
components.html(
    """<script>
(function() {
  var wasDown = false;
  function ping() {
    fetch('/_stcore/health', {cache:'no-store'})
      .then(function(r){ if(r.ok){ if(wasDown){ try{window.top.location.reload();}catch(e){location.reload();} } wasDown=false; } else { wasDown=true; } })
      .catch(function(){ wasDown=true; });
  }
  setInterval(ping, 4000);
})();
</script>""",
    height=0,
)

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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ═══════════════ SILVERLINE 2026 DESIGN SYSTEM ═══════════════ */
:root {
  --bg: #F5F7F4;
  --card: #FFFFFF;
  --surface-2: #EFF3EE;
  --border: #E3E8E1;
  --border-strong: #D2DACF;
  --ink: #1B211C;
  --ink-2: #556057;
  --ink-3: #93A096;
  --accent: #56815F;
  --accent-strong: #3D6247;
  --accent-soft: #E9F1EA;
  --highlight: #EDE7DD;
  --text: #1B211C;
  --hot: #DC4B45;  --hot-soft: #FCEDEC;
  --warm: #DA9520; --warm-soft: #FBF3E3;
  --cold: #4E86C9; --cold-soft: #EAF1FA;
  --r-lg: 16px; --r-md: 12px; --r-sm: 10px;
  --sh-sm: 0 1px 2px rgba(20,32,22,.05);
  --sh-md: 0 6px 20px -6px rgba(20,32,22,.12);
  --sh-lift: 0 10px 26px -8px rgba(20,32,22,.16);
}

/* ── Global ─────────────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"], .stApp {
  background-color: var(--bg) !important;
  color: var(--ink);
  font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif !important;
  -webkit-font-smoothing: antialiased;
}
.main .block-container { max-width: 1480px; padding-top: 1.4rem; padding-bottom: 4rem; }
h1, h2, h3 { font-family: 'Inter', sans-serif !important; font-weight: 800 !important;
  letter-spacing: -0.025em !important; color: var(--ink) !important; }
h1 a, h2 a, h3 a, h4 a, h5 a, h6 a { display: none !important; }
[data-testid="stCaptionContainer"], .stCaption { color: var(--ink-3) !important; }
hr { border-color: var(--border) !important; margin: 1.1rem 0 !important; }
::-webkit-scrollbar { width: 9px; height: 9px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #C9D2C8; border-radius: 99px; }
::-webkit-scrollbar-thumb:hover { background: #AEBBAD; }

/* ── Buttons ────────────────────────────────────────────────── */
div[data-testid="stButton"] > button {
  border-radius: var(--r-sm) !important;
  font-weight: 600 !important; font-size: 0.9rem !important;
  letter-spacing: -0.005em;
  transition: transform .12s ease, box-shadow .15s ease, border-color .15s ease,
              background .15s ease !important;
}
/* Flat, minimal: default buttons are quiet text — no bubbles. Only primary
   CTAs are filled. Hover gives a soft tint for affordance. */
div[data-testid="stButton"] > button[kind="secondary"] {
  background: transparent !important;
  border: none !important;
  color: var(--ink-2) !important;
  box-shadow: none !important;
}
div[data-testid="stButton"] > button[kind="secondary"]:hover {
  background: var(--accent-soft) !important;
  color: var(--accent-strong) !important;
  transform: none; box-shadow: none !important;
}
div[data-testid="stButton"] > button[kind="primary"] {
  background: linear-gradient(180deg, #5E8A67 0%, #49764F 100%) !important;
  border: none !important; color: #fff !important;
  box-shadow: 0 4px 14px -4px rgba(73,118,79,.55), inset 0 1px 0 rgba(255,255,255,.18) !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 22px -6px rgba(73,118,79,.6), inset 0 1px 0 rgba(255,255,255,.18) !important;
  filter: brightness(1.05);
}
div[data-testid="stButton"] > button[kind="tertiary"] {
  background: transparent !important; border: none !important;
  color: var(--accent-strong) !important; padding: 0 4px !important;
  box-shadow: none !important;
}
div[data-testid="stButton"] > button[kind="tertiary"]:hover { text-decoration: underline; }
div[data-testid="stButton"] > button:active { transform: translateY(0) scale(.99); }

/* Brand title button (top-left, acts as home link) — plain bold wordmark */
.st-key-home_dashboard_button div[data-testid="stButton"] > button,
.st-key-home_dashboard_button div[data-testid="stButton"] > button[kind="secondary"] {
  font-size: 1.45rem !important; font-weight: 800 !important;
  letter-spacing: -0.03em !important; text-align: left !important;
  color: var(--ink) !important; background: transparent !important;
  border: none !important; box-shadow: none !important; padding: 0 !important;
}
.st-key-home_dashboard_button div[data-testid="stButton"] > button:hover {
  color: var(--accent-strong) !important; background: transparent !important;
  transform: none !important;
}

/* ── Inputs ─────────────────────────────────────────────────── */
input, textarea, [data-baseweb="select"] * { color: var(--ink) !important; }
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
  background: var(--card) !important;
  border-radius: var(--r-sm) !important;
  border-color: var(--border-strong) !important;
  transition: border-color .15s ease, box-shadow .15s ease;
}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea {
  background: var(--card) !important; color: var(--ink) !important;
}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px var(--accent-soft) !important;
}

/* ── Metrics ────────────────────────────────────────────────── */
[data-testid="stMetric"] {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 14px 18px 12px;
  box-shadow: var(--sh-sm);
}
[data-testid="stMetricLabel"] { color: var(--ink-3) !important; }
[data-testid="stMetricLabel"] p { font-size: .78rem !important; font-weight: 600 !important;
  text-transform: uppercase; letter-spacing: .06em; }
[data-testid="stMetricValue"] { font-weight: 800 !important; letter-spacing: -0.02em; }

/* ── Expanders / cards ──────────────────────────────────────── */
[data-testid="stExpander"] {
  background: var(--card); border: 1px solid var(--border) !important;
  border-radius: var(--r-md) !important; box-shadow: var(--sh-sm);
  overflow: hidden;
}
[data-testid="stExpander"] summary { font-weight: 600 !important; padding: .8rem 1rem !important; }
[data-testid="stExpander"] summary:hover { color: var(--accent-strong) !important; }
[data-testid="stExpander"] > details { border: none !important; }

/* ── Tabs ───────────────────────────────────────────────────── */
.main [data-baseweb="tab-list"] { gap: 6px; }
.main [data-baseweb="tab"] {
  border-radius: 8px !important; padding: 6px 14px !important;
  background: transparent !important; font-weight: 600 !important;
}
.main [data-baseweb="tab"][aria-selected="true"] {
  background: var(--accent-soft) !important; color: var(--accent-strong) !important;
}
.main [data-baseweb="tab-highlight"], .main [data-baseweb="tab-border"] { display: none !important; }

/* ── Progress / dataframe / alerts ──────────────────────────── */
.stProgress > div > div > div { background: var(--surface-2) !important; border-radius: 99px; }
.stProgress > div > div > div > div {
  background: linear-gradient(90deg, #6B9873, #49764F) !important; border-radius: 99px;
}
[data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: var(--r-md); overflow: hidden; }
[data-testid="stAlert"] { border-radius: var(--r-md) !important; }

/* ── Sidebar: dark forest ───────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #1C2B20 0%, #16221A 100%) !important;
  border-right: 1px solid rgba(255,255,255,.06);
}
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] a, [data-testid="stSidebar"] div,
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
  color: #E7EEE8 !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button,
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"],
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="tertiary"] {
  background: transparent !important; border: none !important; box-shadow: none !important;
  color: rgba(231,238,232,.85) !important;
  font-size: .95rem !important; font-weight: 500 !important;
  padding: 9px 12px !important; text-align: left !important; width: 100% !important;
  justify-content: flex-start !important;
  border-radius: 9px !important; transform: none !important;
  transition: background .15s ease, color .15s ease !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover,
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"]:hover {
  background: rgba(255,255,255,.08) !important; color: #FFFFFF !important;
  transform: none !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"] {
  background: linear-gradient(90deg, rgba(107,152,115,.30), rgba(107,152,115,.10)) !important;
  border: none !important; color: #FFFFFF !important; font-weight: 700 !important;
  box-shadow: inset 3px 0 0 #7FB389 !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"]:hover {
  filter: none; transform: none !important;
  background: linear-gradient(90deg, rgba(107,152,115,.38), rgba(107,152,115,.14)) !important;
}
/* Force EVERY sidebar nav label hard-left. The label <p> sits inside a nested
   FLEX div whose justify-content centers it — text-align can't fix flex
   positioning, the inner div's justify-content must be overridden too. */
[data-testid="stSidebar"] [data-testid="stButton"] button,
[data-testid="stSidebar"] [data-testid="stButton"] button > div,
[data-testid="stSidebar"] [data-testid="stButton"] button [data-testid="stMarkdownContainer"] {
  justify-content: flex-start !important;
  text-align: left !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button p {
  text-align: left !important; margin: 0 !important;
}
.sl-brand { display: flex; align-items: center; gap: 12px; padding: 6px 4px 18px;
  border-bottom: 1px solid rgba(255,255,255,.08); margin-bottom: 14px; }
.sl-brand-mark { width: 40px; height: 40px; border-radius: 12px; display: flex;
  align-items: center; justify-content: center; font-size: 20px;
  background: linear-gradient(135deg, #6B9873, #3D6247);
  box-shadow: 0 4px 12px -2px rgba(0,0,0,.4); }
.sl-brand-name { font-size: 1.05rem; font-weight: 800; letter-spacing: -0.02em; color: #fff !important; }
.sl-brand-sub { font-size: .72rem; font-weight: 600; letter-spacing: .14em;
  text-transform: uppercase; color: rgba(231,238,232,.5) !important; }

/* ── Status + intent pills ──────────────────────────────────── */
.pill {
  display: inline-block; border-radius: 999px; padding: 3px 11px; margin: 0 6px 6px 0;
  background: var(--accent-soft); color: var(--accent-strong);
  font-size: .78rem; font-weight: 600; border: 1px solid #D8E5DA;
}
.badge-hot  { background: var(--hot-soft);  color: var(--hot);  }
.badge-warm { background: var(--warm-soft); color: var(--warm); }
.badge-cold { background: var(--cold-soft); color: var(--cold); }
.badge-hot, .badge-warm, .badge-cold {
  display: inline-block; border-radius: 999px; padding: 3px 11px;
  font-size: .78rem; font-weight: 700;
}

/* ── Legacy components, restyled ────────────────────────────── */
.card { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 12px; }
.metric { background: var(--card); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 12px; text-align: center; box-shadow: var(--sh-sm); }
.msg-row{display:flex;margin:8px 0;}
.msg-left{justify-content:flex-start;}
.msg-right{justify-content:flex-end;}
/* iPhone / iMessage look — intentionally kept faithful to iOS */
.msg-bubble-in{background:#e5e5ea;padding:9px 13px;border-radius:18px 18px 18px 5px;max-width:75%;
  color:#000;font-size:.95rem;line-height:1.4;}
.msg-bubble-out{background:linear-gradient(180deg,#41A2FF 0%,#0b84fe 100%);padding:9px 13px;
  border-radius:18px 18px 5px 18px;max-width:75%;color:#fff;font-size:.95rem;line-height:1.4;}
.msg-time{font-size:.70rem;color:var(--ink-3);margin-bottom:3px;text-align:center;width:100%;}
.tooltip-wrap{position:relative;display:inline-flex;align-items:center;gap:6px;}
.tooltip-icon{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;
  border:1px solid var(--border-strong);border-radius:50%;font-size:11px;line-height:1;
  color:var(--ink-3);cursor:default;background:var(--card);}
.tooltip-tip{visibility:hidden;opacity:0;transition:opacity .15s;position:absolute;z-index:20;
  left:20px;top:-6px;background:#1B211C;color:#fff;padding:7px 10px;border-radius:8px;
  font-size:12px;white-space:nowrap;box-shadow:var(--sh-md);}
.tooltip-wrap:hover .tooltip-tip{visibility:visible;opacity:1;}
.quick-search-wrap {margin-top: -34px;}
[data-testid="stToggle"] [data-baseweb="switch"]{height:34px !important;width:78px !important;}
[data-testid="stToggle"] [data-baseweb="switch"] > div{background:#B65B4E !important;border-radius:99px !important;}
[data-testid="stToggle"] input:checked + div{background:#56815F !important;}
[data-testid="stToggle"] [data-baseweb="switch"] > div::before{content:'RJ';position:absolute;left:12px;top:8px;color:#fff;font-size:12px;font-weight:700;}
[data-testid="stToggle"] input:checked + div::before{content:'GL';left:44px;}
.note-actions button{border:none !important;background:transparent !important;padding:0 !important;min-height:20px !important;font-size:0.85rem !important;}
.note-bubble{background:#F6F4EF;border:1px solid #E7E2D9;border-radius:var(--r-md);padding:9px 12px;}
.typing-dot{display:inline-block;animation:blink 1.2s infinite;}
.typing-dot:nth-child(2){animation-delay:.2s;}
.typing-dot:nth-child(3){animation-delay:.4s;}
@keyframes blink{0%,80%,100%{opacity:.3;}40%{opacity:1;}}
.thread-sel{background:var(--accent-soft)!important;box-shadow:inset 3px 0 0 var(--accent)!important;
  border-radius:8px!important;font-weight:700!important;}
.call-row{display:flex;align-items:flex-start;gap:12px;padding:10px 12px;margin:2px 0;
  border:1px solid var(--border);border-radius:var(--r-md);background:var(--card);
  box-shadow:var(--sh-sm);transition:box-shadow .15s ease;}
.call-row:hover{box-shadow:var(--sh-md);}
.call-icon{font-size:1.25rem;flex-shrink:0;}
.call-meta{flex:1;min-width:0;}
.call-name{font-weight:700;font-size:.94rem;letter-spacing:-0.01em;}
.call-preview{font-size:.82rem;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.call-ts{font-size:.76rem;color:var(--ink-3);white-space:nowrap;flex-shrink:0;}

/* ── Page components (used across every page) ───────────────── */
.sl-banner{padding:12px 16px;border-radius:var(--r-md);margin-bottom:12px;
  font-size:.95rem;box-shadow:var(--sh-sm);border:1px solid var(--border);background:var(--card);}
.sl-banner.green{background:#EDF6EE;border-color:#CDE5D0;box-shadow:inset 4px 0 0 #4E9C58;}
.sl-banner.red{background:#FCEDEC;border-color:#F3D2D0;box-shadow:inset 4px 0 0 #DC4B45;}
.sl-flag{font-size:.82rem;padding:6px 10px;margin:3px 0;background:var(--surface-2);
  border-radius:8px;box-shadow:inset 3px 0 0 #C4A46E;color:var(--ink-2);}
.sl-coach{font-size:.84rem;padding:10px 14px;margin:6px 0 4px;background:#EDF3FB;
  border-radius:var(--r-sm);box-shadow:inset 3px 0 0 #3B6FC4;color:#1E3A5F;}
.sl-coach-tag{font-weight:700;font-size:.72rem;letter-spacing:.08em;
  text-transform:uppercase;opacity:.65;}
.sl-row-name{font-size:1.02rem;font-weight:700;letter-spacing:-0.01em;color:var(--ink);}
.sl-row-name.done{color:var(--ink-3);text-decoration:line-through;}
.sl-row-phone{font-size:.88rem;color:var(--ink-2);}
.sl-row-sub{font-size:.82rem;color:var(--ink-3);margin:2px 0 4px;}
.sl-num{font-size:1.02rem;font-weight:700;letter-spacing:-0.01em;}
.sl-chip{color:#fff;padding:2px 10px;border-radius:999px;font-size:.72rem;
  font-weight:700;letter-spacing:.02em;}
.sl-buybox{background:var(--card);border:1px solid var(--border);
  border-radius:var(--r-md);padding:16px 18px;box-shadow:var(--sh-sm);}

/* ── Dialer lead card ───────────────────────────────────────── */
.sl-lead-card{position:relative;border:1px solid var(--border);border-radius:var(--r-lg);
  padding:20px 24px 16px;background:linear-gradient(180deg,#FFFFFF 0%,#FBFCFA 100%);
  box-shadow:var(--sh-md);margin:10px 0 14px;overflow:hidden;}
.sl-lead-card::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;
  background:linear-gradient(90deg,#6B9873,#3D6247);}
.sl-lead-name{font-size:1.5rem;font-weight:800;letter-spacing:-0.025em;color:var(--ink);}
.sl-lead-phone{display:inline-block;font-size:.95rem;font-weight:600;color:var(--accent-strong);
  background:var(--accent-soft);border-radius:8px;padding:3px 10px;margin:6px 8px 2px 0;}
.sl-lead-addr{font-size:.88rem;color:var(--ink-2);margin-top:8px;}
</style>
"""

PAGES = ["Dashboard", "Leads", "Call List", "Dialer", "Caller ID Health", "Follow-ups", "Messages"]


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


_IL_AREA_CODES = {
    "217","224","309","312","331","618","630","708","773","779","815","847"
}
_FROM_OPTS = {"515": "+15154618235", "773": "+17732503445"}

def _auto_caller_id(phone: str) -> str:
    """Return '773' for IL area codes, '515' otherwise."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    area = digits[:3] if len(digits) == 10 else digits[1:4]
    return "773" if area in _IL_AREA_CODES else "515"


def lead_phones(lead: dict) -> list[str]:
    """All dialable numbers for a lead, in order (primary first, then any
    'Phones:' list the dialer stored in the timeline note). Deduped, max 5."""
    phones: list[str] = []
    primary = (lead.get("phone") or "").strip()
    if primary:
        phones.append(primary)
    m = re.search(r"Phones:\s*([0-9,\s\-()]+)", lead.get("timeline", "") or "")
    if m:
        for chunk in m.group(1).split(","):
            p = chunk.strip()
            if p and p not in phones:
                phones.append(p)
    return phones[:5]


def fmt_stamp(dt: datetime) -> str:
    return dt.strftime("%m-%d • %I:%M %p").replace(" 0", " ")


def fmt_date_time(iso_value: str) -> tuple[str, str]:
    dt = datetime.fromisoformat(iso_value)
    current_year = datetime.now().year
    date_str = dt.strftime("%m-%d") if dt.year == current_year else dt.strftime("%m-%d-%Y")
    return date_str, dt.strftime("%I:%M %p").lstrip("0")


def flag_sent(name: str, message: str) -> None:
    """Queue the verified-send popup for the run AFTER st.rerun(). Call this
    ONLY when send_sms returned sent=True (Twilio accepted the message) —
    never optimistically."""
    st.session_state["_sent_popup"] = {"name": name, "preview": message[:110]}


def render_sent_popup() -> None:
    """Render the one-shot 'Message sent' card (top-right, slides in, fades
    out). Shows exactly once, only after a server-verified send."""
    data = st.session_state.pop("_sent_popup", None)
    if not data:
        return
    _name = html.escape(data["name"])
    _prev = html.escape(data["preview"])
    st.markdown(
        f"""<div class='sent-pop'>
  <div class='sent-pop-ico'>✓</div>
  <div>
    <div class='sent-pop-title'>Message sent</div>
    <div class='sent-pop-sub'>to {_name}</div>
    <div class='sent-pop-msg'>“{_prev}”</div>
  </div>
</div>
<style>
.sent-pop {{ position:fixed; top:74px; right:26px; z-index:99999;
  display:flex; gap:12px; align-items:flex-start; background:#fff;
  border:1px solid #cfe0d2; border-left:5px solid #2e9e52;
  border-radius:12px; padding:14px 18px; max-width:340px;
  box-shadow:0 10px 34px rgba(18,42,24,.20); pointer-events:none;
  animation: sentpop-in .35s cubic-bezier(.2,.9,.3,1.15),
             sentpop-out .6s ease 4.5s forwards; }}
.sent-pop-ico {{ width:30px; height:30px; border-radius:50%; background:#2e9e52;
  color:#fff; display:flex; align-items:center; justify-content:center;
  font-weight:800; font-size:16px; flex:0 0 30px; }}
.sent-pop-title {{ font-weight:700; color:#183f24; font-size:.95rem; }}
.sent-pop-sub {{ font-size:.85rem; color:#4a5a4e; }}
.sent-pop-msg {{ font-size:.8rem; color:#8a8a8a; margin-top:4px; font-style:italic; }}
@keyframes sentpop-in {{ from {{ transform:translateX(46px); opacity:0 }}
                         to   {{ transform:none; opacity:1 }} }}
@keyframes sentpop-out {{ to {{ opacity:0; transform:translateY(-10px); visibility:hidden }} }}
</style>""",
        unsafe_allow_html=True,
    )


def softphone_call_button(lead: dict, from_number: str, label: str | None = None,
                          height: int = 46) -> None:
    """A call button that dials through the SOFTPHONE (instant connect,
    recorded + transcribed) instead of the phone bridge. Must be an HTML
    button in a component frame: the popup can only open on the same real
    click, and the same click pushes the lead into the agent's dial channel."""
    import json as _j
    _agent = "rom" if st.session_state.get("user", "RJ") == "RJ" else "gabby"
    _base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    _sp_url = f"{_base}/softphone?agent={_agent}"
    _label = label or f"📞 Call {html.escape((lead.get('name') or '').split(' ')[0])}"
    _payload = _j.dumps({
        "agent": _agent, "lead_id": lead["id"], "caller_id": from_number,
        "lead_name": lead.get("name", ""), "lead_phone": lead.get("phone", ""),
    })
    components.html(f"""
<style>body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
#cb {{ display:block; width:100%; padding:9px 6px; border:none; cursor:pointer;
  background:linear-gradient(180deg,#34a853,#288f43); color:#fff; border-radius:8px;
  font-size:13.5px; font-weight:700; }}
#cb:hover {{ filter:brightness(1.06); }}</style>
<button id="cb">{_label}</button>
<script>
document.getElementById('cb').addEventListener('click', function() {{
  window.open('{_sp_url}', 'crm_softphone_{_agent}', 'width=390,height=640,resizable=yes');
  var p = {_payload};
  p.ts = Date.now().toString();
  fetch('{_base}/dialer/current', {{ method: 'POST', body: JSON.stringify(p) }})
    .then(() => {{ this.textContent = '📞 Dialing in softphone…'; }})
    .catch(() => {{ this.textContent = '⚠️ Backend unreachable'; }});
}});
</script>""", height=height + 8)


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
    # Subtract with timedelta — .replace(day=...) blows up with
    # "day is out of range for month" when the week began last month.
    start_week = start_day - timedelta(days=start_day.weekday())
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


_US_STATES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY",
]
_DEAL_TYPES = ["Wholesale", "Fix & Flip", "Buy & Hold", "New Construction", "Land"]
_CONDITIONS = ["Any", "Distressed only", "Light rehab OK", "Move-in ready"]


def _parse_buy_box(lead: dict) -> dict:
    import json as _j
    try:
        return _j.loads(lead.get("buy_box") or "{}")
    except Exception:
        return {}


def render_buy_box(lead_id: int, lead: dict) -> None:
    import json as _j
    bb = _parse_buy_box(lead)
    editing = st.session_state.get(f"bb_edit_{lead_id}", not bool(bb))

    if not editing:
        # ── View card ────────────────────────────────────────────────────
        pts = bb.get("property_types", [])
        pt_pills = "".join(
            f"<span class='pill' style='background:#dff0ff;color:#1a5a8a'>{p.title()}</span>"
            for p in pts
        )
        state_pills = "".join(f"<span class='pill'>{s}</span>" for s in bb.get("states", []))
        deal_pills = "".join(
            f"<span class='pill' style='background:#fff3e0;color:#7a4f00'>{d}</span>"
            for d in bb.get("deal_types", [])
        )
        pmin = bb.get("price_min", 0)
        pmax = bb.get("price_max", 0)
        price_line = ""
        if pmin or pmax:
            price_line = (
                f"<div style='margin:4px 0'>💰 "
                f"<b>${pmin:,}</b> – <b>${pmax:,}</b></div>"
            )
        cond = bb.get("condition", "Any")
        cond_line = f"<div style='margin:4px 0'>🏚️ {cond}</div>" if cond != "Any" else ""
        beds = bb.get("beds_min", 0)
        baths = bb.get("baths_min", 0)
        bed_bath = ""
        if "residential" in pts and (beds or baths):
            parts = []
            if beds: parts.append(f"🛏️ {beds}+ beds")
            if baths: parts.append(f"🛁 {baths}+ baths")
            bed_bath = "<div style='margin:4px 0'>" + " &nbsp;·&nbsp; ".join(parts) + "</div>"
        notes = bb.get("notes", "")
        notes_line = f"<div style='margin:4px 0;font-size:.85rem;color:#555'>📝 {notes}</div>" if notes else ""

        st.markdown(
            f"<div class='sl-buybox'>"
            f"<div style='margin-bottom:6px'>{pt_pills}</div>"
            f"<div style='margin-bottom:6px'>📍 {state_pills}</div>"
            f"<div style='margin-bottom:6px'>💼 {deal_pills}</div>"
            f"{price_line}{cond_line}{bed_bath}{notes_line}"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button("✏️ Edit Buy Box", key=f"bb_edit_btn_{lead_id}", type="secondary"):
            st.session_state[f"bb_edit_{lead_id}"] = True
            st.rerun()
        return

    # ── Edit form ─────────────────────────────────────────────────────────
    st.markdown("**States**")
    states = st.multiselect(
        "States", _US_STATES, default=bb.get("states", []),
        key=f"bb_states_{lead_id}", label_visibility="collapsed",
    )

    st.markdown("**Property Type**")
    _pt_c1, _pt_c2 = st.columns(2)
    res_checked = _pt_c1.checkbox(
        "🏠 Residential", value="residential" in bb.get("property_types", []),
        key=f"bb_res_{lead_id}",
    )
    com_checked = _pt_c2.checkbox(
        "🏢 Commercial", value="commercial" in bb.get("property_types", []),
        key=f"bb_com_{lead_id}",
    )

    st.markdown("**Deal Types**")
    _dt_cols = st.columns(len(_DEAL_TYPES))
    deal_sel = []
    for _i, _dt in enumerate(_DEAL_TYPES):
        if _dt_cols[_i].checkbox(_dt, value=_dt in bb.get("deal_types", []), key=f"bb_dt_{lead_id}_{_i}"):
            deal_sel.append(_dt)

    st.markdown("**Price Range**")
    _pr1, _pr2 = st.columns(2)
    price_min = _pr1.number_input("Min $", value=int(bb.get("price_min", 0)), step=5000,
                                  min_value=0, key=f"bb_pmin_{lead_id}")
    price_max = _pr2.number_input("Max $", value=int(bb.get("price_max", 0)), step=5000,
                                  min_value=0, key=f"bb_pmax_{lead_id}")

    condition = st.selectbox(
        "Condition", _CONDITIONS,
        index=_CONDITIONS.index(bb.get("condition", "Any")) if bb.get("condition") in _CONDITIONS else 0,
        key=f"bb_cond_{lead_id}",
    )

    if res_checked:
        _bd1, _bd2 = st.columns(2)
        beds_min = _bd1.number_input("Min Beds", value=int(bb.get("beds_min", 0)),
                                     min_value=0, step=1, key=f"bb_beds_{lead_id}")
        baths_min = _bd2.number_input("Min Baths", value=int(bb.get("baths_min", 0)),
                                      min_value=0, step=1, key=f"bb_baths_{lead_id}")
    else:
        beds_min = bb.get("beds_min", 0)
        baths_min = bb.get("baths_min", 0)

    notes = st.text_area("Additional criteria / notes", value=bb.get("notes", ""),
                         height=60, key=f"bb_notes_{lead_id}")

    _sv1, _sv2 = st.columns([2, 5])
    if _sv1.button("Save Buy Box", key=f"bb_save_{lead_id}", type="primary"):
        property_types = []
        if res_checked: property_types.append("residential")
        if com_checked: property_types.append("commercial")
        new_bb = {
            "states": states,
            "property_types": property_types,
            "deal_types": deal_sel,
            "price_min": int(price_min),
            "price_max": int(price_max),
            "condition": condition,
            "beds_min": int(beds_min),
            "baths_min": int(baths_min),
            "notes": notes.strip(),
        }
        db.update_lead(lead_id, {"buy_box": _j.dumps(new_bb)})
        st.session_state[f"bb_edit_{lead_id}"] = False
        st.toast("Buy box saved.")
        st.rerun()
    if bb and _sv2.button("Cancel", key=f"bb_cancel_{lead_id}", type="secondary"):
        st.session_state[f"bb_edit_{lead_id}"] = False
        st.rerun()


_STATUS_ICONS = {"hot": "🔥", "warm": "🌤", "cold": "❄️", "dead": "⚫",
                 "contract": "✅", "investor": "💼"}


def fmt_local_ts(ts_iso: str) -> str:
    """UTC-stored timestamp → local human label ('Today 2:31 PM',
    'Yesterday 4:10 PM', 'Jul 8, 4:10 PM'). Old imported rows that were
    saved in local wall-clock will read a few hours off — day-level
    accuracy is what matters for those."""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(ts_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        loc = dt.astimezone()
        days_ago = (datetime.now().astimezone().date() - loc.date()).days
        t = loc.strftime("%I:%M %p").lstrip("0")
        if days_ago == 0:
            return f"Today {t}"
        if days_ago == 1:
            return f"Yesterday {t}"
        return f"{loc.strftime('%b')} {loc.day}, {t}"
    except Exception:
        return (ts_iso or "")[:16]


def classify_callback_intent(lead_id: int, before_ts: str,
                             ixns_by_lead: dict) -> dict:
    """Why is this lead calling us back? Judged from what we did to them
    BEFORE the callback. Drives the missed-call badge so a callback from
    someone who heard our voicemail (real intent) is visually separated from
    a cold lead just returning an unknown missed number (low intent)."""
    prior = [i for i in ixns_by_lead.get(lead_id, [])
             if i["timestamp"] < before_ts and i["direction"] == "outbound"]
    blob = " ".join((i.get("content") or "").lower() for i in prior)
    if "drop message played" in blob:
        return {"label": "\U0001F399\uFE0F Returning your voicemail",
                "hint": "they heard your recorded message \u2014 call this one back",
                "fg": "#1b6e2e", "bg": "#e7f6ea", "rank": 4}
    if "left voicemail" in blob:
        return {"label": "\U0001F4EC Reached their voicemail",
                "hint": "we hit their VM and they called back \u2014 decent sign, "
                        "but a message may not have been left",
                "fg": "#5b3b8c", "bg": "#f0eafb", "rank": 3}
    if "connected, talked" in blob or "wants callback" in blob:
        return {"label": "\U0001F4AC Talked before",
                "hint": "you\u2019ve spoken \u2014 warm callback",
                "fg": "#12508f", "bg": "#e8f1fb", "rank": 2}
    return {"label": "\U0001F4DE Cold callback",
            "hint": "likely just returning the missed number \u2014 low intent",
            "fg": "#8a6d00", "bg": "#fff6e0", "rank": 1}


def lead_conversation_items(lead_id: int) -> list[dict]:
    interactions = db.fetch_interactions(lead_id)
    # Calls and texts only — notes live in the Notes panel, not the conversation
    items = [i for i in interactions if i["type"] in ("call", "text")]
    return sorted(items, key=lambda x: x["timestamp"])


def render_ai_draft_row(lead: dict, stage_key: str, widget_prefix: str) -> None:
    """✨ AI draft button + context box — the same engine as the Follow-ups
    page (full history, strategy rotation, new-number rule). Stages the draft
    into `stage_key`; the caller pops that into its compose box just before
    rendering the text widget."""
    _c1, _c2 = st.columns([1, 2.4])
    ctx_key = f"{widget_prefix}_ctx"
    _c2.text_input(
        "Context for the AI",
        key=ctx_key,
        placeholder="context for the AI — e.g. she just called about the shed price",
        label_visibility="collapsed",
    )
    if _c1.button("✨ AI draft", key=f"{widget_prefix}_btn", use_container_width=True):
        with st.spinner("Checking matched emails and reading the conversation..."):
            _ixns = db.fetch_interactions(lead["id"])
            _last_in = next((i["content"] for i in _ixns
                             if i["type"] == "text" and i["direction"] == "inbound"), "")
            _draft = generate_followup_message(
                lead=lead,
                last_message=_last_in,
                ignored_texts=0,
                strong_intent=False,
                conversation=_ixns,
                extra_context=st.session_state.get(ctx_key, ""),
            )
        st.session_state[stage_key] = _draft.message
        st.session_state[f"{widget_prefix}_coaching"] = _draft.coaching
        st.session_state[f"{widget_prefix}_cost"] = _draft.cost_usd
        st.session_state["ai_cost_session_total"] = (
            st.session_state.get("ai_cost_session_total", 0.0) + _draft.cost_usd)
        st.rerun()
    _cost = st.session_state.get(f"{widget_prefix}_cost")
    if _cost:
        st.caption(f"💰 draft cost ${_cost:.4f} — edit the message above, then send")
    _coaching = st.session_state.get(f"{widget_prefix}_coaching", "")
    if _coaching:
        with st.container(border=True):
            st.caption("Coach · email context and draft guidance")
            st.write(_coaching)


def update_status(lead_id: int, key: str) -> None:
    db.update_lead(lead_id, {"status": st.session_state[key]})


def auto_save_field(lead_id: int, field: str, key: str) -> None:
    db.update_lead(lead_id, {field: st.session_state[key]})


def _build_display_result(leads: list[dict] | None = None,
                          all_ixns: dict | None = None) -> dict:
    """
    Read-only dashboard data builder.
    Uses DB-stored lead status as source of truth so manual overrides
    (e.g. demoting a lead from hot → warm) are never overwritten on render.
    engine.evaluate_all_leads() writes suggested_status back to DB on every
    call — this version evaluates for signals/call-list/follow-ups without
    any DB writes.
    Pass prefetched leads/interactions to avoid re-querying (the startup
    block loads everything in two queries and shares it here).
    """
    from collections import Counter

    if leads is None:
        leads = db.fetch_leads()
    if all_ixns is None:
        all_ixns = {l["id"]: db.fetch_interactions(l["id"]) for l in leads}
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
        if lead["status"] in ("hot", "dead", "contract", "investor"):
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

    _u_qs = "gl" if st.session_state.get("user") == "GL" else "rom"
    st.markdown(
        f"### {html.escape(lead['name'])} "
        f"<a href='?page=leads&lead={lead_id}&u={_u_qs}' target='_blank' "
        f"style='font-size:13px;font-weight:600;text-decoration:none;color:#5E8A67'>"
        f"⧉ new tab</a>",
        unsafe_allow_html=True,
    )
    c_top1, c_top2 = st.columns(2)
    c_top1.markdown(f"**Phone:** {lead['phone']}")
    c_top2.markdown(f"**Stage:** {human_stage(lead['conversation_stage'])}")

    with st.expander("✏️ Rename · 🗑 Delete lead", expanded=False):
        _rn1, _rn2 = st.columns([3, 1])
        _new_name = _rn1.text_input("Lead name", value=lead["name"],
                                    key=f"rename_{lead_id}",
                                    label_visibility="collapsed")
        if _rn2.button("Save name", key=f"rename_save_{lead_id}",
                       use_container_width=True):
            if _new_name.strip() and _new_name.strip() != lead["name"]:
                db.update_lead(lead_id, {"name": _new_name.strip()})
                st.toast(f"Renamed to {_new_name.strip()}")
                st.rerun()
        st.divider()
        st.caption(f"Delete **{lead['name']}** and ALL their history — texts, "
                   "calls, notes, dispositions. crm.db has no backup; this "
                   "cannot be undone.")
        _del_key = f"confirm_del_lead_{lead_id}"
        if st.session_state.get(_del_key):
            _dc1, _dc2 = st.columns(2)
            if _dc1.button("🗑 Yes — delete permanently",
                           key=f"del_lead_yes_{lead_id}", type="primary",
                           use_container_width=True):
                db.delete_lead(lead_id)
                st.session_state.pop(_del_key, None)
                st.session_state.pop("selected_lead_id", None)
                st.session_state["lead_list_collapsed"] = False
                st.toast(f"Deleted {lead['name']} and all their history.")
                st.rerun()
            if _dc2.button("Cancel", key=f"del_lead_no_{lead_id}",
                           use_container_width=True):
                st.session_state.pop(_del_key, None)
                st.rerun()
        else:
            if st.button("🗑 Delete this lead…", key=f"del_lead_{lead_id}"):
                st.session_state[_del_key] = True
                st.rerun()

    c_edit1, c_edit2, c_edit3, c_edit4 = st.columns(4)
    _status_opts = ["hot", "warm", "cold", "dead", "contract", "investor"]
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

    # Buy Box — only shown for investor leads
    if lead["status"] == "investor":
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("### 🏠 Buy Box")
        render_buy_box(lead_id, lead)
        st.markdown("</div>", unsafe_allow_html=True)

    # 🤝 Buyer matching — sellers worth moving get scanned against every
    # investor buy box on file (one cheap AI call for the whole list).
    if lead["status"] in ("warm", "hot", "contract"):
        with st.expander("🤝 Match Buyers — AI scans every investor buy box", expanded=False):
            _bm_key = f"buyer_matches_{lead_id}"
            if st.button("🔎 Find matching buyers", key=f"bm_run_{lead_id}"):
                with st.spinner("Scanning buy boxes..."):
                    _bm_res = buyer_match.match_buyers(lead, interactions)
                st.session_state[_bm_key] = _bm_res
                st.session_state["ai_cost_session_total"] = (
                    st.session_state.get("ai_cost_session_total", 0.0)
                    + _bm_res.get("cost_usd", 0.0))
                st.rerun()
            _bm_res = st.session_state.get(_bm_key)
            if _bm_res:
                if _bm_res.get("error"):
                    st.error(_bm_res["error"])
                elif not _bm_res.get("matches"):
                    st.caption(f"No buyers scored 5+ across "
                               f"{_bm_res.get('buyers_scanned', 0)} buy boxes.")
                else:
                    st.caption(f"Scanned {_bm_res['buyers_scanned']} buy boxes "
                               f"· ${_bm_res.get('cost_usd', 0):.4f}")
                    for m in _bm_res["matches"]:
                        _bmc1, _bmc2 = st.columns([4, 1.4])
                        with _bmc1:
                            st.markdown(f"**{m['name']}** · `{m['phone']}` "
                                        f"· match {m['score']}/10")
                            st.caption(m["reason"])
                        with _bmc2:
                            if st.button("Open", key=f"bm_open_{lead_id}_{m['lead_id']}",
                                         use_container_width=True):
                                nav_to("Leads", m["lead_id"])
                            if st.button("✍️ Draft text",
                                         key=f"bm_draft_{lead_id}_{m['lead_id']}",
                                         use_container_width=True):
                                with st.spinner("Drafting..."):
                                    _bd = buyer_match.draft_buyer_text(
                                        m, lead, interactions, m["reason"],
                                        sender="Rom" if st.session_state.get("user", "RJ") == "RJ" else "Gabby")
                                if _bd.get("message"):
                                    st.session_state[f"bm_edit_{lead_id}_{m['lead_id']}"] = _bd["message"]
                                    st.session_state["ai_cost_session_total"] = (
                                        st.session_state.get("ai_cost_session_total", 0.0)
                                        + _bd.get("cost_usd", 0.0))
                                    st.rerun()
                                else:
                                    st.error(_bd.get("error", "draft failed"))
                        _bm_edit_key = f"bm_edit_{lead_id}_{m['lead_id']}"
                        if _bm_edit_key in st.session_state:
                            st.text_area("Text to buyer", key=_bm_edit_key, height=70,
                                         label_visibility="collapsed")
                            if st.button(f"📤 Send to {m['name'].split()[0]}",
                                         key=f"bm_send_{lead_id}_{m['lead_id']}",
                                         type="primary"):
                                _sms = send_sms(m["lead_id"],
                                                st.session_state[_bm_edit_key].strip())
                                if _sms.get("sent"):
                                    flag_sent(m["name"], st.session_state[_bm_edit_key].strip())
                                    st.session_state.pop(_bm_edit_key, None)
                                    st.rerun()
                                else:
                                    st.error(f"❌ NOT sent to {m['name']}: {_sms.get('error')}")
                        st.divider()

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        _conv_hdr, _conv_btn = st.columns([4, 1])
        _conv_hdr.markdown(f"**Conversation** ({len(conversation)} messages)")
        if _conv_btn.button("🔄", key=f"conv_refresh_{lead_id}", help="Refresh conversation"):
            st.rerun()
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
        if lead.get("phone", "") in _gv_contacts:
            st.markdown(
                "<div class='sl-flag' style='border-color:#8e44ad;color:#6c3483;"
                "background:#f4ecfa'>📱 Reached you on <b>Google Voice</b> — "
                "your reply sends from your Twilio number (a different number on their "
                "end). The first reply should say it's your new office number they can "
                "reach you at more reliably.</div>",
                unsafe_allow_html=True,
            )
        manual_msg_key = f"manual_text_{lead_id}"
        # An AI draft staged on the previous run lands in the compose box here.
        _staged_lead_draft = st.session_state.pop(f"manual_text_stage_{lead_id}", None)
        if _staged_lead_draft is not None:
            st.session_state[manual_msg_key] = _staged_lead_draft
        st.text_area("Message", key=manual_msg_key, height=80, label_visibility="collapsed",
                     placeholder="Type a custom message to send right now…")
        send_col1, send_col2 = st.columns([1, 1])
        if send_col1.button("Send Text", key=f"manual_send_{lead_id}"):
            text_to_send = st.session_state.get(manual_msg_key, "").strip()
            if text_to_send:
                result = send_sms(lead_id, text_to_send)
                if result.get("sent"):
                    flag_sent(lead["name"], text_to_send)
                    st.session_state.pop(manual_msg_key, None)
                    st.rerun()
                else:
                    # No rerun on failure — keep the error (and the draft) visible.
                    st.error(f"❌ NOT sent: {result.get('error', 'unknown error')}")
            else:
                st.warning("Type a message first.")
        _auto_cid = _auto_caller_id(lead.get("phone", ""))
        _cid_pick = send_col2.radio(
            "Call from", ["515", "773"],
            index=0 if _auto_cid == "515" else 1,
            horizontal=True,
            key=f"cid_pick_{lead_id}",
            label_visibility="collapsed",
        )
        with send_col2:
            # Primary: softphone call (instant connect, recorded + transcribed).
            softphone_call_button(lead, _FROM_OPTS[_cid_pick], label="📞 Call This Lead")
            # Fallback: two-leg bridge to the cell (for when away from the PC).
            if st.button("📱 Bridge to my cell instead", key=f"click_to_call_{lead_id}",
                         type="tertiary"):
                result = place_call(lead_id, from_number=_FROM_OPTS[_cid_pick])
                if result.get("called"):
                    st.success(f"Calling from {_cid_pick} — your phone rings first, then bridges to lead.")
                else:
                    st.error(f"Call failed: {result.get('error', 'unknown error')}")
        render_ai_draft_row(lead, f"manual_text_stage_{lead_id}", f"lead_ai_{lead_id}")
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
# Schema setup/migrations once per browser session, not on every click.
if "db_ready" not in st.session_state:
    db.init_db()
    st.session_state["db_ready"] = True

# TWO queries total per rerun (was one query per lead — thousands per click).
all_leads = get_all_leads()
lead_lookup = {lead["id"]: lead for lead in all_leads}
all_interactions = db.fetch_all_interactions()
_ixns_by_lead: dict[int, list[dict]] = {}
for _ix in all_interactions:
    _ixns_by_lead.setdefault(_ix["lead_id"], []).append(_ix)
result = _build_display_result(all_leads, _ixns_by_lead)
try:
    from crm_engine.gv_gmail_import import load_gv_contacts
    _gv_contacts = load_gv_contacts()
except Exception:
    _gv_contacts = set()

selected_id = st.session_state.get("selected_lead_id")
if selected_id is not None and selected_id not in lead_lookup:
    st.session_state["selected_lead_id"] = None

if "page" not in st.session_state:
    st.session_state["page"] = "dashboard"
query_params = st.query_params
if "page" in query_params:
    st.session_state["page"] = query_params["page"]
# Agent (Rom/Gabby) persists in the URL so a new tab / reload keeps whoever
# you are — nav is now real links (full reload), which would otherwise reset it.
if query_params.get("u") in ("rom", "gl"):
    st.session_state["user"] = "GL" if query_params["u"] == "gl" else "RJ"
# Deep-link a specific lead so it can be opened in its own tab.
if "lead" in query_params:
    try:
        _qlead = int(query_params["lead"])
        st.session_state["selected_lead_id"] = _qlead
        st.session_state["lead_list_collapsed"] = True
    except (ValueError, TypeError):
        pass
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
    # Keep the agent in the URL so nav links (and new tabs) stay this agent.
    _u_want = "gl" if is_gl else "rom"
    if st.query_params.get("u") != _u_want:
        st.query_params["u"] = _u_want
    st.markdown("##### Quick Search")
    render_global_search(all_leads)
    st.markdown("</div>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown(
        """
        <div class="sl-brand">
          <div class="sl-brand-mark">🌿</div>
          <div>
            <div class="sl-brand-name">Silverline</div>
            <div class="sl-brand-sub">CRM &nbsp;·&nbsp; 2026</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _NAV_ICONS = {
        "dashboard": "🏠", "leads": "👤", "call_list": "📋", "dialer": "⚡",
        "caller_health": "🛡️", "followups": "🔁", "messages": "💬", "email_review": "📧",
    }
    # Nav items are REAL links now — middle-click / ctrl-click opens the page
    # in a new tab so you can watch several pages at once. Left-click still
    # navigates in place. The agent (?u) rides along so new tabs stay you.
    _u_qs = "gl" if st.session_state.get("user") == "GL" else "rom"
    st.markdown("""
<style>
.sl-nav-link { display:flex; align-items:center; gap:10px; width:100%;
  padding:9px 13px; margin:3px 0; border-radius:9px; text-decoration:none;
  font-size:14px; font-weight:600; color:#dfeae1 !important;
  transition:background .12s; }
.sl-nav-link:hover { background:rgba(255,255,255,.08); }
.sl-nav-link.active { background:linear-gradient(180deg,#5E8A67,#49764F);
  color:#fff !important; box-shadow:0 2px 8px rgba(20,50,26,.25); }
.sl-nav-ico { font-size:16px; }
.sl-nav-badge { margin-left:auto; background:#ff3b30; color:#fff;
  border-radius:50%; min-width:20px; height:20px; display:inline-flex;
  align-items:center; justify-content:center; font-size:11px; font-weight:700;
  padding:0 5px; }
</style>""", unsafe_allow_html=True)

    _unread_msg_leads = db.unread_message_lead_ids()
    _missed_calls_open = db.fetch_unacknowledged_missed_calls()
    _unread_count = len(_unread_msg_leads) + len(_missed_calls_open)

    def nav_link(label: str, page_key: str, badge: int = 0) -> None:
        active = " active" if st.session_state["page"] == page_key else ""
        icon = _NAV_ICONS.get(page_key, "•")
        badge_html = (f"<span class='sl-nav-badge'>{badge if badge < 100 else '99+'}</span>"
                      if badge > 0 else "")
        st.markdown(
            f"<a class='sl-nav-link{active}' href='?page={page_key}&u={_u_qs}' "
            f"target='_self'><span class='sl-nav-ico'>{icon}</span>"
            f"<span>{label}</span>{badge_html}</a>",
            unsafe_allow_html=True,
        )

    nav_link("Dashboard", "dashboard")
    nav_link("Leads", "leads")
    nav_link("Call List", "call_list")
    nav_link("Dialer", "dialer")
    nav_link("Caller ID Health", "caller_health")
    nav_link("Follow-ups", "followups")
    nav_link("Messages", "messages", badge=_unread_count)
    nav_link("Opportunities", "email_review", badge=db.email_opportunity_count(EMAIL_MAILBOX))
    st.caption("💡 Middle-click (or Ctrl-click) any page to open it in a new tab.")

st.divider()
page = st.session_state["page"]
render_sent_popup()   # one-shot verified-send confirmation (any page)

if page == "email_review":
    catch_up_if_due()
    render_email_review()

elif page == "dashboard":
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
            f"<div class='sl-banner green'>"
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
            f"<div class='sl-banner red'>"
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
                _intent = classify_callback_intent(c["lead_id"], c["timestamp"], _ixns_by_lead)
                st.markdown(
                    f"<div style='background:{_intent['bg']};color:{_intent['fg']};"
                    f"border-radius:7px;padding:5px 9px;font-size:12px;font-weight:700;"
                    f"margin-bottom:6px'>{_intent['label']}"
                    f"<div style='font-weight:400;font-size:11px;opacity:.85'>"
                    f"{_intent['hint']}</div></div>",
                    unsafe_allow_html=True,
                )
                _mc1, _mc2 = st.columns(2)
                with _mc1:
                    # Softphone callback — the outbound call it logs clears
                    # this missed-call banner automatically.
                    _mc_lead = lead_lookup.get(c["lead_id"])
                    if _mc_lead:
                        softphone_call_button(
                            _mc_lead, _FROM_OPTS[_auto_caller_id(c.get("phone", ""))],
                            label="📞 Call back", height=40)
                if _mc2.button("Mark read", key=f"missed_read_{c['id']}",
                               use_container_width=True):
                    db.set_thread_mark(c["lead_id"], "call", "read")
                    st.rerun()

    # ── Unmatched inbox (texts/voicemails from unknown numbers) ──────────────
    unmatched = db.fetch_unmatched_inbox()
    if unmatched:
        word = "message" if len(unmatched) == 1 else "messages"
        _gv_un = sum(1 for u in unmatched if (u.get("content") or "").startswith("[Google Voice]"))
        _gv_note = f" · {_gv_un} from Google Voice" if _gv_un else ""
        st.markdown(
            f"<div class='sl-banner red'><b>📥 {len(unmatched)} {word} from unknown "
            f"numbers need a look{_gv_note}</b> — could be new sellers. Add them as leads below so "
            f"they don't slip through.</div>",
            unsafe_allow_html=True,
        )
        with st.expander(f"📥 Review {len(unmatched)} unmatched {word}", expanded=True):
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
                        _res = db.convert_unmatched_to_lead(entry["id"], new_name)
                        _verb = "Updated" if _res.get("reused") else "Created"
                        _addr = f" · address: {_res['address']}" if _res.get("address") else ""
                        st.session_state["_sent_popup"] = {
                            "name": new_name,
                            "preview": f"{_verb} lead — {_res.get('messages_added', 0)} "
                                       f"message(s) pulled in{_addr}",
                        }
                        st.toast(f"✅ {_verb} lead: {new_name} "
                                 f"({_res.get('messages_added', 0)} msgs)")
                        st.rerun()
                    else:
                        st.warning("Enter a name first.")
                if u_col3.button("Dismiss", key=f"unmatched_dismiss_{entry['id']}"):
                    db.mark_unmatched_handled(entry["id"])
                    st.rerun()
                st.markdown("---")

    # 🧠 AI call coaching — reads the week's transcripts and reports what's
    # working, what's costing calls, and the objections heard.
    with st.expander("🧠 Call Coaching — AI review of this week's transcripts", expanded=False):
        if st.button("Generate this week's report", key="coach_gen"):
            _cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            _tx = [i["content"].replace("📝 Call transcript:\n", "")
                   for i in all_interactions
                   if i["type"] == "note"
                   and i["content"].startswith("📝 Call transcript:")
                   and i["timestamp"] >= _cutoff]
            with st.spinner(f"Reading {len(_tx)} transcripts..."):
                from crm_engine.ai_message_generator import generate_coaching_report
                _report, _ccost = generate_coaching_report(_tx)
            st.session_state["coach_report"] = (_report, _ccost, len(_tx))
            st.session_state["ai_cost_session_total"] = (
                st.session_state.get("ai_cost_session_total", 0.0) + _ccost)
            st.rerun()
        if "coach_report" in st.session_state:
            _report, _ccost, _n = st.session_state["coach_report"]
            st.caption(f"Based on {_n} transcripts · ${_ccost:.4f}")
            st.markdown(_report)

    st.markdown("### Today’s Priorities")
    pcols = st.columns(5)
    # Hot-lead call LIST progress (the checklist on the Call List page) —
    # not the dialer. Dialer volume is the ☎️ metric next to it.
    pcols[0].button(
        f"Call list left today: {calls_remaining_today}",
        key="calls_remaining_today_button",
        on_click=go_to_call_list,
        help="Hot leads on today's Call List you haven't checked off yet.",
    )
    try:
        _pool_now = caller_id_pool.pool_status()
        _dials_label = f"☎️ Dials today: {_pool_now['used_today']}/{_pool_now['cap_today']}"
    except Exception:
        _dials_label = "☎️ Dials today: —"
    if pcols[1].button(_dials_label, key="prio_dials_today",
                       help="Every line the multi-dialer fired today vs the pool's "
                            "spam-safe hard cap. Includes unanswered/canceled lines — "
                            "that's what carriers see."):
        nav_to("caller_health")
    overdue_label = f"🔴 Overdue follow-ups: {overdue_count}" if overdue_count > 0 else f"Overdue follow-ups: {overdue_count}"
    if pcols[2].button(overdue_label, key="prio_overdue"):
        st.session_state["followup_overdue_only"] = True
        nav_to("Follow-ups")
    if pcols[3].button(f"Calls logged today: {calls_completed_today}", key="prio_calls_completed",
                       help="Calls that produced a record: connected, voicemail, or a "
                            "disposition. Lines that rang unanswered and got canceled "
                            "don't create a call log — see ☎️ Dials today for those."):
        nav_to("Call List")
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
        ["hot", "warm", "cold", "investor", "dead", "contract", "uncontacted"],
        default=preset_filter or [],
        key="lead_status_multiselect",
    )

    def _status_match(l: dict) -> bool:
        # "Uncontacted" = raw call leads (never dialed). Keep them OUT of the
        # worked-status buckets so the cold/warm/hot pools stay clean.
        if not status_filter:
            return True
        if l.get("conversation_stage") == "not_contacted":
            return "uncontacted" in status_filter
        return l["status"] in status_filter

    matches = [
        l for l in all_leads
        if _status_match(l)
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
                    f"<span class='sl-row-name{' done' if is_done else ''}'>{entry['name']}</span>"
                    f"<span class='sl-row-phone'>{lead['phone'] if lead else ''}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if clean_address:
                    st.markdown(
                        f"<div class='sl-row-sub'>📍 {clean_address}</div>",
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
    _session_ai_cost = st.session_state.get("ai_cost_session_total", 0.0)
    if _session_ai_cost > 0:
        st.caption(f"🤖 AI cost this session: **${_session_ai_cost:.4f}** "
                   f"(Claude Haiku 4.5 — resets when you restart the app)")
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
            coaching_key = f"f_coaching_{lid}"
            cost_key = f"f_cost_{lid}"
            reasoning_key = f"f_reasoning_{lid}"

            def _run_generation(target_lead, target_item, target_msg_key, target_flags_key, target_coaching_key, target_cost_key, target_reasoning_key, force=False, extra_context=""):
                import json as _json
                ixns_g = db.fetch_interactions(target_lead["id"])
                ixn_count = len(ixns_g)

                # Use cached suggestion if interactions haven't changed (unless forced)
                if not force:
                    cached = db.get_followup_suggestion(target_lead["id"])
                    if cached and cached["interaction_count"] == ixn_count:
                        st.session_state[target_msg_key] = cached["message"]
                        st.session_state[target_flags_key] = _json.loads(cached["flags"])
                        st.session_state[target_cost_key] = 0.0  # cached — no API call, no cost
                        st.session_state[target_reasoning_key] = f"Tone: {cached['tone']} (cached suggestion)"
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
                st.session_state[target_cost_key] = draft.cost_usd
                st.session_state[target_reasoning_key] = draft.reasoning
                st.session_state["ai_cost_session_total"] = (
                    st.session_state.get("ai_cost_session_total", 0.0) + draft.cost_usd
                )
                if draft.coaching:
                    st.session_state[target_coaching_key] = draft.coaching
                else:
                    st.session_state.pop(target_coaching_key, None)
                db.save_followup_suggestion(
                    target_lead["id"], draft.message, draft.flags,
                    draft.tone, ixn_count,
                )

            # Handle regenerate (force=True skips cache)
            if st.session_state.pop(regen_key, False):
                with st.spinner("Regenerating..."):
                    _run_generation(lead, item, msg_key, flags_key, coaching_key, cost_key, reasoning_key, force=True,
                                    extra_context=st.session_state.get(ctx_key, ""))

            # Lazy-generate on first view (uses cache if available)
            if msg_key not in st.session_state:
                with st.spinner(f"Analyzing {item['lead_name']}..."):
                    _run_generation(lead, item, msg_key, flags_key, coaching_key, cost_key, reasoning_key)

            flags = st.session_state.get(flags_key, [])

            st.markdown("<div class='card'>", unsafe_allow_html=True)
            if lid > 0 and st.button(f"{item['lead_name']}", key=f"f_name_{lid}", type="tertiary"):
                nav_to("Leads", lid)
            if lid <= 0:
                st.markdown(f"**{item['lead_name']}**")
            st.caption(f"Phone: {lead.get('phone', 'N/A')}  ·  {address}")

            # They CALLED you and you haven't called back — a text reply is
            # the wrong move. Uses the persistent missed-call state, so this
            # clears the moment you call them back (or hit Mark read).
            _mc_open = next((m for m in _missed_calls_open if m["lead_id"] == lid), None)
            if _mc_open:
                _ci = classify_callback_intent(lid, _mc_open["timestamp"], _ixns_by_lead)
                st.markdown(
                    f"<div class='sl-flag' style='border-color:#e74c3c;"
                    f"color:#c0392b;background:#fdecea;font-weight:700'>"
                    f"📞 This lead CALLED YOU and you haven't called back — "
                    f"call them instead of texting.<br>"
                    f"<span style='font-weight:400'>{_ci['label']}: {_ci['hint']}.</span></div>",
                    unsafe_allow_html=True,
                )

            # Reached us on Google Voice -> replies go from a different (Twilio)
            # number, so flag it persistently.
            _is_gv_lead = lead.get("phone", "") in _gv_contacts
            if _is_gv_lead:
                st.markdown(
                    "<div class='sl-flag' style='border-color:#8e44ad;color:#6c3483;"
                    "background:#f4ecfa'>📱 Reached you on <b>Google Voice</b> — "
                    "your reply sends from your Twilio number (a different number on their "
                    "end). The first reply should say it's your new office number they can "
                    "reach you at more reliably.</div>",
                    unsafe_allow_html=True,
                )
            # First text from this number -> the draft must identify the new number.
            _never_texted = not any(
                h.get("type") == "text" and h.get("direction") == "outbound"
                for h in history
            )
            if _never_texted:
                _ft_extra = ("they came in on Google Voice, so say it's your new office "
                             "number they can reach you at more reliably"
                             if _is_gv_lead else
                             "say who you are + that this is your new office number")
                st.markdown(
                    f"<div class='sl-flag'>📱 First text from this number — the "
                    f"draft should {_ft_extra}. Double-check before sending.</div>",
                    unsafe_allow_html=True,
                )

            if flags:
                for flag in flags:
                    st.markdown(f"<div class='sl-flag'>{flag}</div>",
                                unsafe_allow_html=True)

            _coaching = st.session_state.get(coaching_key, "")
            if _coaching:
                st.markdown(
                    f"<div class='sl-coach'>"
                    f"<span class='sl-coach-tag'>Coach</span><br>{_coaching}</div>",
                    unsafe_allow_html=True,
                )

            with st.container(height=180):
                if history:
                    render_imessage(history, max_items=len(history))
                else:
                    st.caption("No conversation yet")

            st.text_area("Suggested text", key=msg_key, height=75)

            _cost = st.session_state.get(cost_key)
            if _cost is not None:
                if _cost > 0:
                    st.caption(f"💰 This generation cost **${_cost:.4f}**")
                else:
                    st.caption("💰 $0.0000 — reused cached suggestion, no API call")

            c1, c2, c_ctx, c3 = st.columns([2, 1, 3, 1])
            _SNOOZE_DAYS = {"warm": 5, "cold": 30}

            if c1.button("Approve & Send", key=f"f_app_{lid}"):
                if lid > 0:
                    ts = datetime.utcnow()
                    approved_text = st.session_state[msg_key]
                    _send_res = send_sms(lid, approved_text)  # logs the text interaction internally
                    if not _send_res.get("sent"):
                        # Send FAILED: no note, no snooze, keep the draft so
                        # the agent can retry. Never claim "sent" unverified.
                        st.error(f"❌ NOT sent: {_send_res.get('error', 'unknown error')} "
                                 f"— nothing was logged or snoozed; fix and retry.")
                        st.stop()
                    user_value = st.session_state.get("user", "RJ")
                    user_dot = "🔴" if user_value == "RJ" else "🟢"

                    # Auto-note: what the AI decided and why, so future you (or
                    # anyone else on the lead) can see the reasoning at a glance.
                    _reasoning = st.session_state.get(reasoning_key, "")
                    _sent_flags = st.session_state.get(flags_key, [])
                    _detail_parts = [p for p in [_reasoning] if p]
                    if _sent_flags:
                        _detail_parts.append("Flags noted: " + "; ".join(_sent_flags[:3]))
                    _extra_ctx = st.session_state.get(ctx_key, "").strip()
                    if _extra_ctx:
                        _detail_parts.append(f"Agent context given: {_extra_ctx}")
                    _detail = "  ·  ".join(_detail_parts) if _detail_parts else "no reasoning captured"
                    db.add_interaction(
                        lid, "note",
                        f"{user_dot} {user_value} approved & sent AI follow-up — {_detail}",
                        "outbound", ts=ts,
                    )
                    # Snooze: hide lead until next follow-up window
                    lead_status = lead.get("status", "warm")
                    snooze = _SNOOZE_DAYS.get(lead_status, 5)
                    next_date = (date.today() + timedelta(days=snooze)).isoformat()
                    db.update_lead(lid, {"next_action_date": next_date})
                    st.session_state.pop(msg_key, None)
                    st.session_state.pop(flags_key, None)
                    st.session_state.pop(reasoning_key, None)
                    autoplay_audio()
                    flag_sent(item["lead_name"], approved_text)
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
    # NOTE: opening this page does NOT mark anything read. Unread state
    # clears only when you reply (text/call back) or hit Mark read.

    # ── header ────────────────────────────────────────────────────────────────
    hdr_left, hdr_gv, hdr_right = st.columns([5, 1.4, 1])
    hdr_left.subheader("💬 Messages & Calls")
    if hdr_gv.button("🔄 Google Voice", key="gv_sync_btn",
                     help="Pull in texts people sent to your Google Voice number "
                          "(forwarded to Gmail) and log them here."):
        with st.spinner("Checking Google Voice (via Gmail)…"):
            try:
                from crm_engine.gv_gmail_import import sync_google_voice
                _gv = sync_google_voice(since_days=60)
                st.toast(f"Google Voice: {_gv['matched']} new to leads, "
                         f"{_gv['unmatched']} to unmatched inbox.")
            except Exception as _gv_exc:
                st.error(f"Google Voice sync failed: {type(_gv_exc).__name__}. "
                         f"Check Gmail is connected in Email Review.")
        st.rerun()
    if hdr_right.button("🔄 Refresh", key="msg_refresh_btn"):
        st.rerun()

    # ── Quick Dial (any number, not tied to a lead) ───────────────────────────
    with st.expander("📞 Quick Dial — call any number", expanded=False):
        _qd_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        _qd_515 = _FROM_OPTS["515"]
        _qd_773 = _FROM_OPTS["773"]
        _qd_agent = "rom" if st.session_state.get("user", "RJ") == "RJ" else "gabby"
        # Self-contained widget: the Browser button opens/focuses the softphone
        # popup itself (no need to visit the Dialer page first), then pushes the
        # number for it to dial. The My-phone button bridges your cell instead.
        components.html(f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
          <input id="qdNum" placeholder="(515) 555-0123" inputmode="tel"
                 style="width:100%;padding:9px 12px;font-size:15px;border:1px solid #ccc;
                        border-radius:8px;margin-bottom:8px;box-sizing:border-box">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <label style="font-size:13px;color:#555">From:</label>
            <select id="qdFrom" style="padding:7px;border-radius:6px;border:1px solid #ccc">
              <option value="{_qd_515}">515 (Iowa)</option>
              <option value="{_qd_773}">773 (Chicago)</option>
            </select>
            <button onclick="qd('browser')" style="flex:1;min-width:120px;padding:9px;
                    background:#28a745;color:#fff;border:none;border-radius:8px;
                    font-weight:600;font-size:14px;cursor:pointer">📞 Browser</button>
            <button onclick="qd('phone')" style="flex:1;min-width:120px;padding:9px;
                    background:#0d6efd;color:#fff;border:none;border-radius:8px;
                    font-weight:600;font-size:14px;cursor:pointer">📞 My phone</button>
          </div>
          <div id="qdMsg" style="margin-top:8px;font-size:13px;color:#555;min-height:18px"></div>
        </div>
        <script>
          var BASE = "{_qd_base}";
          var AGENT = "{_qd_agent}";
          var SOFTPHONE = BASE + "/softphone?agent=" + AGENT;
          function qdDigits(s) {{ return (s.match(/\\d/g) || []).join(''); }}
          function qdE164(s) {{ var d = qdDigits(s); return d.length === 10 ? '+1'+d : '+'+d; }}
          function qdMsg(t) {{ document.getElementById('qdMsg').textContent = t; }}
          function qd(mode) {{
            var raw = document.getElementById('qdNum').value;
            var d = qdDigits(raw);
            if (d.length !== 10 && d.length !== 11) {{ qdMsg('Enter a valid 10-digit number.'); return; }}
            var from = document.getElementById('qdFrom').value;
            var to = qdE164(raw);
            if (mode === 'browser') {{
              // Open (or focus) the softphone popup — same window name reuses it.
              var win = window.open(SOFTPHONE, 'crm_softphone_' + AGENT, 'width=380,height=440,resizable=yes');
              if (!win) {{ qdMsg('Popup blocked — allow popups for this site, then try again.'); return; }}
              fetch(BASE + '/dialer/current', {{ method:'POST',
                body: JSON.stringify({{ agent: AGENT, to_number: to, caller_id: from,
                  lead_name: 'Manual dial', lead_phone: raw, ts: Date.now().toString() }}) }});
              qdMsg('Dialing ' + raw + ' in the softphone popup...');
            }} else {{
              qdMsg('Calling your phone first...');
              fetch(BASE + '/quick-dial-phone', {{ method:'POST',
                body: JSON.stringify({{ to: to, from: from }}) }})
                .then(function(r){{ return r.json(); }})
                .then(function(j){{ qdMsg(j.called ? ('Calling ' + raw + ' — your phone rings first.')
                                                   : ('Failed: ' + (j.error || 'unknown'))); }})
                .catch(function(e){{ qdMsg('Failed: ' + e); }});
            }}
          }}
        </script>
        """, height=130)

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
        _last_in = max((x["timestamp"] for x in _txts if x["direction"] == "inbound"),
                       default=None)
        _threads.append({
            "lead_id": _lid,
            "lead_name": _lead["name"],
            "phone": _lead["phone"],
            "status": _lead["status"],
            "last_text": _latest["content"],
            "last_ts": _latest["timestamp"],
            "last_in_ts": _last_in,      # when THEY last texted us
            "direction": _latest["direction"],
        })
    # Unread threads pin to the top (newest first within each group) and stay
    # there until replied to or marked read.
    _unread_ids = db.unread_message_lead_ids()
    _threads.sort(key=lambda t: t["last_ts"], reverse=True)
    _threads.sort(key=lambda t: t["lead_id"] in _unread_ids, reverse=True)

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
                _is_unread = _t["lead_id"] in _unread_ids
                _preview = (_t["last_text"][:48] + "…") if len(_t["last_text"]) > 48 else _t["last_text"]
                _d, _tm = fmt_date_time(_t["last_ts"])
                _dir_icon = "← " if _t["direction"] == "inbound" else "→ "
                _btn_type = "secondary" if _is_sel else "tertiary"
                # iMessage-style unread: small blue dot in the gutter + bold name
                _dot_col, _item_col = st.columns([0.09, 1])
                with _dot_col:
                    if _is_unread:
                        st.markdown(
                            "<div style='width:9px;height:9px;border-radius:50%;"
                            "background:#0A84FF;margin-top:15px'></div>",
                            unsafe_allow_html=True,
                        )
                with _item_col:
                    _name = f"**{_t['lead_name']}**" if _is_unread else _t["lead_name"]
                    _sicon = _STATUS_ICONS.get(_t["status"], "")
                    _label = ("● " if _is_sel else "") + _name + (f" {_sicon}" if _sicon else "")
                    if st.button(_label, key=f"msg_thread_{_t['lead_id']}",
                                 use_container_width=True, type=_btn_type):
                        st.session_state["msg_selected_lead"] = _t["lead_id"]
                        st.rerun()
                    if _is_unread and _t.get("last_in_ts"):
                        st.caption(f"← {_preview}  ·  they texted {fmt_local_ts(_t['last_in_ts'])}")
                    else:
                        st.caption(f"{_dir_icon}{_preview}  ·  {fmt_local_ts(_t['last_ts'])}")

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
                _ch_left, _ch_mark, _ch_right = st.columns([3.2, 1.3, 1])
                _sel_icon = _STATUS_ICONS.get(_sel_lead["status"], "")
                _gv_tag = (" · <span style='color:#8e44ad;font-weight:700'>"
                           "📱 Google Voice</span>"
                           if _sel_lead["phone"] in _gv_contacts else "")
                _ch_left.markdown(
                    f"**{_sel_lead['name']}** {_sel_icon} "
                    f"{_sel_lead['status'].upper()} · `{_sel_lead['phone']}`{_gv_tag}",
                    unsafe_allow_html=True,
                )
                _sel_unread = _sel_lid in _unread_ids
                _mark_label = "✓ Mark read" if _sel_unread else "Mark unread"
                if _ch_mark.button(_mark_label, key=f"msg_mark_{_sel_lid}", type="secondary"):
                    db.set_thread_mark(_sel_lid, "message",
                                       "read" if _sel_unread else "unread")
                    st.rerun()
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
                if not any(i.get("type") == "text" and i.get("direction") == "outbound"
                           for i in _convo_items):
                    if _sel_lead["phone"] in _gv_contacts:
                        st.caption("📱 They reached you on Google Voice — this reply "
                                   "sends from your Twilio number. Say it's your new office "
                                   "number they can reach you at more reliably.")
                    else:
                        st.caption("📱 First text to this lead from this number — say who "
                                   "you are and that this is your new office number they can save.")
                _compose_key = f"msg_compose_{_sel_lid}"
                # An AI draft staged on the previous run lands in the compose
                # box here (widget keys can't be set after the widget renders).
                _staged_draft = st.session_state.pop(f"msg_ai_stage_{_sel_lid}", None)
                if _staged_draft is not None:
                    st.session_state[_compose_key] = _staged_draft
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
                            flag_sent(_sel_lead["name"], _msg_text.strip())
                            st.session_state.pop(_compose_key, None)
                            st.rerun()
                        else:
                            # No rerun on failure — error and draft stay visible.
                            st.error(f"❌ NOT sent: {_res.get('error')}")
                    else:
                        st.warning("Type a message first.")
                _msg_auto_cid = _auto_caller_id(_sel_lead.get("phone", ""))
                _msg_cid = _sc2.radio(
                    "From", ["515", "773"],
                    index=0 if _msg_auto_cid == "515" else 1,
                    horizontal=True,
                    key=f"msg_cid_{_sel_lid}",
                    label_visibility="collapsed",
                )
                with _sc2:
                    # Softphone call — instant connect, recorded + transcribed.
                    softphone_call_button(_sel_lead, _FROM_OPTS[_msg_cid],
                                          label="📞 Call", height=40)

                # AI-drafted reply, same engine + context box as Follow-ups —
                # lands in the compose box for editing before you hit Send.
                render_ai_draft_row(_sel_lead, f"msg_ai_stage_{_sel_lid}", f"msg_ai_{_sel_lid}")

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
                _cl_icon = _STATUS_ICONS.get(_cl["status"], "") if _cl else ""
                st.markdown(
                    f"<div class='call-name'>{_cl_name} {_cl_icon}</div>"
                    f"<div class='call-preview'>{_call_preview}</div>",
                    unsafe_allow_html=True,
                )
                if _c["direction"] == "inbound" and _cl:
                    _ci = classify_callback_intent(_cl["id"], _c["timestamp"], _ixns_by_lead)
                    st.markdown(
                        f"<span style='display:inline-block;background:{_ci['bg']};"
                        f"color:{_ci['fg']};border-radius:6px;padding:3px 8px;"
                        f"font-size:11px;font-weight:700;margin-top:3px'>"
                        f"{_ci['label']} \u2014 {_ci['hint']}</span>",
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
                    # Resurface a handled missed call as needing follow-up.
                    if "missed" in _lcontent:
                        if st.button("↩ Mark unread", key=f"msg_call_unread_{_c['id']}", type="tertiary"):
                            db.set_thread_mark(_cl["id"], "call", "unread")
                            st.toast(f"📞 {_cl['name']} back on the missed-call list.")
                            st.rerun()

            st.divider()

elif page == "dialer":
    st.subheader("📞 Dialer")

    # ── Session state ─────────────────────────────────────────────────────────
    st.session_state.setdefault("dialer_queue", [])       # list of lead_ids this session
    st.session_state.setdefault("dialer_idx", 0)          # position in queue
    st.session_state.setdefault("dialer_on_call", False)  # True after Call, until disposed
    st.session_state.setdefault("dialer_preview", None)   # parsed contacts awaiting confirm
    st.session_state.setdefault("dialer_warnings", [])

    # Prune any leads that were deleted out from under us
    queue = [lid for lid in st.session_state["dialer_queue"] if lid in lead_lookup]
    st.session_state["dialer_queue"] = queue

    # label -> (interaction note, days until next call or None for no auto re-call)
    DISPOSITIONS = {
        "No Answer":      ("Call — no answer", 1),
        "Voicemail":      ("Call — left voicemail", 3),
        "Wrong Number":   ("Call — wrong number", None),
        "Not Interested": ("Call — not interested", None),
        "Callback":       ("Call — wants callback", 1),
        "Connected":      ("Call — connected, talked", 3),
        "Dead Air":       ("Call — answered but dead air (no audio)", 1),
        "No Longer Owns": ("Call — no longer owns the property", None),
        "Hung Up":        ("Call — answered, hung up quickly", 2),
    }

    def _dispose(lead_id: int, label: str) -> None:
        note, days = DISPOSITIONS[label]
        who = st.session_state.get("user", "RJ")
        db.add_interaction(lead_id, "call", f"{note} ({who})", "outbound")
        fields = {"last_contact_date": date.today().isoformat()}
        # A call lead "graduates" into a normal lead once it's actually dialed.
        lead = lead_lookup.get(lead_id)
        if lead and lead.get("conversation_stage") == "not_contacted":
            fields["conversation_stage"] = "initial_contact"
        if days is not None:
            fields["next_action_date"] = (date.today() + timedelta(days=days)).isoformat()
        db.update_lead(lead_id, fields)
        # KPI row (dashboard-side dispositions; popup logs its own)
        db.log_disposition(lead_id, label, source="dashboard", agent=who,
                           batch_id=st.session_state.get("dialer_batch_id"),
                           to_number=lead.get("phone") if lead else None)
        st.session_state["dialer_on_call"] = False
        if st.session_state["dialer_idx"] < len(queue) - 1:
            st.session_state["dialer_idx"] += 1   # auto-advance after disposing
        st.rerun()

    # ── Import a list ─────────────────────────────────────────────────────────
    with st.expander("➕ Import a list to dial", expanded=not queue):
        _imp_mode = st.radio(
            "Import mode",
            ["Auto (table first, AI fallback)", "Table parse only (free)", "AI cleanup (uses Claude)"],
            key="dialer_import_mode", horizontal=True,
        )
        _mode_map = {"Auto (table first, AI fallback)": "auto",
                     "Table parse only (free)": "table",
                     "AI cleanup (uses Claude)": "ai"}
        _selected_mode = _mode_map[_imp_mode]

        _imp_tab1, _imp_tab2 = st.tabs(["📁 Upload file", "📋 Paste text"])

        with _imp_tab1:
            uploaded = st.file_uploader(
                "Drag & drop or browse — CSV, XLSX, or TXT",
                type=["csv", "tsv", "txt", "xlsx"], key="dialer_file_upload",
            )
            if uploaded and st.button("Parse file", key="dialer_parse_file"):
                with st.spinner("Parsing file..."):
                    if uploaded.name.endswith(".xlsx"):
                        import pandas as pd
                        df = pd.read_excel(uploaded)
                        file_text = df.to_csv(index=False)
                    else:
                        file_text = uploaded.read().decode("utf-8", errors="replace")
                    contacts, warnings = parse_contacts(file_text, mode=_selected_mode)
                st.session_state["dialer_preview"] = contacts
                st.session_state["dialer_warnings"] = warnings
                st.rerun()

        with _imp_tab2:
            raw = st.text_area(
                "Paste anything — names, numbers, addresses. Messy is fine.",
                key="dialer_raw_input", height=160,
                placeholder="John Smith 773-555-0100 123 Main St\n(515) 555-0199 Jane Doe ...",
            )
            if st.button("Parse text", key="dialer_parse"):
                with st.spinner("Parsing contacts..."):
                    contacts, warnings = parse_contacts(raw, mode=_selected_mode)
                st.session_state["dialer_preview"] = contacts
                st.session_state["dialer_warnings"] = warnings
                st.rerun()

        preview = st.session_state.get("dialer_preview")
        if preview is not None:
            for w in st.session_state.get("dialer_warnings", []):
                st.caption("⚠️ " + w)
            if preview:
                st.markdown(f"**{len(preview)} contacts found — double-check, then add:**")
                st.dataframe(
                    [{"Name": c.name, "Phones": ", ".join(c.phones), "Address": c.address}
                     for c in preview],
                    use_container_width=True, hide_index=True,
                )
                _default_name = f"Import {date.today().isoformat()}"
                _list_name = st.text_input(
                    "Name this list", value=_default_name, key="dialer_list_name",
                    help="Saved so you can start dialing it anytime from the Saved lists below.",
                )
                if st.button(f"✅ Save & add {len(preview)} to dialer", key="dialer_confirm", type="primary"):
                    # Index every number already in the CRM so we never create a
                    # duplicate call lead for someone already on file (double-dial risk).
                    existing_nums: dict[str, int] = {}
                    for l in all_leads:
                        for p in lead_phones(l):
                            existing_nums.setdefault(p, l["id"])

                    FUTURE = "2999-12-31"  # park call leads out of the follow-up queue until dialed
                    new_ids: list[int] = []
                    seen_nums: set[str] = set()
                    skipped_existing = skipped_dupe = 0
                    for c in preview:
                        # Dedupe across ALL of this contact's numbers — against the
                        # CRM and against earlier rows in this same import.
                        if any(p in existing_nums for p in c.phones):
                            skipped_existing += 1
                            continue
                        if any(p in seen_nums for p in c.phones):
                            skipped_dupe += 1
                            continue
                        # New "call lead": coldest tier, uncontacted, no auto follow-ups
                        # until it is actually dialed (then _dispose graduates it).
                        lid = db.create_lead(
                            c.name, c.phone,
                            conversation_stage="not_contacted",
                            next_action_date=FUTURE,
                        )
                        # Store address + the full ordered phone list so the dialer
                        # can call every number on file. The Situation/Notes scaffold
                        # keeps parse_timeline_parts able to read the address cleanly
                        # (the phone list lands in the Notes channel).
                        if len(c.phones) > 1:
                            tl = (f"Address: {c.address}. Situation: . "
                                  f"Notes: Phones: {', '.join(c.phones)}")
                            db.update_lead(lid, {"timeline": tl})
                        elif c.address:
                            db.update_lead(lid, {"timeline": f"Address: {c.address}"})
                        for p in c.phones:
                            seen_nums.add(p)
                        new_ids.append(lid)
                    # Save the batch as a named, persistent dial list.
                    if new_ids:
                        _new_list_id = db.create_dial_list(_list_name)
                        db.add_list_members(_new_list_id, new_ids)
                    st.session_state["dialer_queue"] = queue + new_ids
                    st.session_state["dialer_idx"] = len(queue)   # jump to first new lead
                    st.session_state["dialer_on_call"] = False
                    st.session_state["dialer_preview"] = None
                    st.session_state["dialer_warnings"] = []
                    msg = f"Saved list '{_list_name}' — added {len(new_ids)} call leads to the dialer."
                    extras = []
                    if skipped_existing:
                        extras.append(f"{skipped_existing} already in CRM")
                    if skipped_dupe:
                        extras.append(f"{skipped_dupe} duplicate in paste")
                    if extras:
                        msg += "  Skipped " + ", ".join(extras) + "."
                    st.success(msg)
                    st.rerun()
            else:
                st.info("No dialable contacts found in that paste.")

    # ── Saved lists ───────────────────────────────────────────────────────────
    _saved_lists = db.fetch_dial_lists()
    with st.expander(f"📋 Saved lists ({len(_saved_lists)})", expanded=not queue):
        # One-time helper: gather previously-imported contacts not yet in a list.
        _listed = db.listed_lead_ids()
        # True dialer imports are parked far-future; match those precisely so we
        # don't offer to group unrelated old uncontacted leads.
        _orphans = [l["id"] for l in all_leads
                    if (l.get("next_action_date") or "") >= "2999-01-01" and l["id"] not in _listed]
        if _orphans:
            oc1, oc2 = st.columns([3, 2])
            oc1.caption(f"{len(_orphans)} previously-imported contacts aren't in a list yet.")
            if oc2.button(f"Save {len(_orphans)} as a list", key="gather_orphans"):
                _gid = db.create_dial_list(f"Imported contacts {date.today().isoformat()}")
                db.add_list_members(_gid, _orphans)
                st.success(f"Saved {len(_orphans)} contacts as a list.")
                st.rerun()

        if not _saved_lists:
            st.caption("No saved lists yet. Import a list above (name it) and it'll appear here.")
        for _dl in _saved_lists:
            _lc1, _lc2, _lc3 = st.columns([4, 3, 2])
            with _lc1:
                st.markdown(f"**{_dl['name']}**")
                _created = _dl["created_at"][:10] if _dl.get("created_at") else ""
                st.caption(f"📇 {_dl['contacts']} contacts · ✅ {_dl['dialed']} dialed · "
                           f"⏳ {_dl['remaining']} left · {_created}")
            with _lc2:
                _rn_key = f"rename_val_{_dl['id']}"
                st.text_input("Rename", value=_dl["name"], key=_rn_key,
                              label_visibility="collapsed")
                if st.button("Rename", key=f"rename_btn_{_dl['id']}"):
                    db.rename_dial_list(_dl["id"], st.session_state[_rn_key])
                    st.rerun()
            with _lc3:
                if st.button("▶ Start dialing", key=f"start_list_{_dl['id']}", type="primary"):
                    _ids = db.fetch_list_lead_ids(_dl["id"])
                    st.session_state["dialer_queue"] = _ids
                    st.session_state["dialer_idx"] = 0
                    st.session_state["dialer_on_call"] = False
                    st.session_state["dialer_batch_id"] = None
                    st.toast(f"Loaded '{_dl['name']}' ({len(_ids)} leads) into the dialer.")
                    st.rerun()
                if st.button("🗑 Delete list", key=f"del_list_{_dl['id']}"):
                    if st.session_state.get(f"confirm_del_{_dl['id']}"):
                        db.delete_dial_list(_dl["id"])
                        st.session_state.pop(f"confirm_del_{_dl['id']}", None)
                        st.toast(f"Deleted list '{_dl['name']}' (leads kept).")
                        st.rerun()
                    else:
                        st.session_state[f"confirm_del_{_dl['id']}"] = True
                        st.warning("Click Delete again to confirm (leads are kept).")
            st.divider()

    # ── The dial card ─────────────────────────────────────────────────────────
    if not queue:
        st.info("Nobody in the dialer yet. Import a list above or start a saved list.")
    else:
        idx = min(st.session_state["dialer_idx"], len(queue) - 1)
        st.session_state["dialer_idx"] = idx
        lead_id = queue[idx]
        lead = lead_lookup[lead_id]
        _, address_text, _, _ = parse_timeline_parts(lead.get("timeline", ""))

        st.progress((idx + 1) / len(queue))
        st.caption(f"Lead {idx + 1} of {len(queue)}")

        phones = lead_phones(lead)
        phone_lines = "".join(
            f"<span class='sl-lead-phone'>📞 {p}</span>"
            for p in phones
        )
        st.markdown(
            "<div class='sl-lead-card'>"
            f"<div class='sl-lead-name'>{lead['name']}</div>"
            f"<div>{phone_lines}</div>"
            + (f"<div class='sl-lead-addr'>📍 {address_text}</div>" if address_text else "")
            + "</div>",
            unsafe_allow_html=True,
        )

        # ── Call mode + line count ────────────────────────────────────────
        _base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        _browser_ready = bool(os.getenv("TWILIO_TWIML_APP_SID"))
        st.session_state.setdefault("dialer_call_mode", "browser" if _browser_ready else "phone")
        st.session_state.setdefault("dialer_lines", 3)
        st.session_state.setdefault("dialer_batch_id", None)

        if not st.session_state["dialer_on_call"]:
            # Which agent this browser session is (RJ toggle = Rom, GL = Gabby).
            # Drives lead claiming, the dial channel, and the softphone identity.
            _agent = "rom" if st.session_state.get("user", "RJ") == "RJ" else "gabby"
            st.caption(f"Dialing as **{_agent.capitalize()}** — flip the RJ/GL toggle "
                       f"(top right) to switch. Leads are claimed per agent, so two "
                       f"people can work the same list without double-dialing.")

            _call_mode = st.radio(
                "Call mode",
                ["Browser (headset/mic)", "Phone (bridges to your cell)"],
                key="dialer_call_mode_radio", horizontal=True,
                index=0 if st.session_state["dialer_call_mode"] == "browser" else 1,
            )
            st.session_state["dialer_call_mode"] = (
                "browser" if "Browser" in _call_mode else "phone"
            )
            _browser_tip = ("Fastest — 1 Twilio leg, ~500ms after first call. "
                            "Softphone popup stays open between leads so the "
                            "Twilio connection never drops. Best with a headset. "
                            "No cell phone needed.")
            _phone_tip = ("Slower — 2 Twilio legs, ~4-6s total. Twilio calls your cell "
                          "first (~2-3s), you answer, then it dials the lead (~2-3s). "
                          "Better audio quality on cell, but adds delay between leads.")
            if st.session_state["dialer_call_mode"] == "browser":
                if not _browser_ready:
                    st.warning("Browser calling not set up. Run `python setup_twilio_app.py` first, then restart the webhook server.")
                else:
                    _softphone_url = f"{_base_url}/softphone?agent={_agent}"
                    # Start Dialing opens the softphone itself (same click =
                    # no popup blocker). This link is just a manual backup.
                    _sp_col1, _sp_col2 = st.columns([3, 1])
                    _sp_col1.markdown(
                        info_icon("⚡ ~500ms connect (softphone opens when you start)", _browser_tip),
                        unsafe_allow_html=True,
                    )
                    _sp_col2.markdown(
                        f"<a href='{_softphone_url}' target='crm_softphone_{_agent}' "
                        f"onclick=\"window.open('{_softphone_url}','crm_softphone_{_agent}',"
                        f"'width=390,height=640,resizable=yes');return false;\" "
                        f"style='display:inline-block;background:transparent;color:#6b7a70;"
                        f"padding:6px 10px;border:1px solid #cdd6cf;border-radius:6px;font-size:12px;"
                        f"text-decoration:none;text-align:center;"
                        f"width:100%'>Open Softphone</a>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    info_icon("🐢 Slower — ~4-6s (two-leg bridge)", _phone_tip),
                    unsafe_allow_html=True,
                )

            _is_browser = st.session_state["dialer_call_mode"] == "browser"
            _lines = st.session_state["dialer_lines"]

            if _is_browser and _browser_ready:
                _lines = st.slider("Lines (simultaneous calls)", 1, 10,
                                   value=st.session_state["dialer_lines"],
                                   key="dialer_lines_slider")
                st.session_state["dialer_lines"] = _lines

            if _is_browser and _browser_ready:
                # AMD auto-kill retired 2026-07-17: even beep-only detection
                # hung up on a live person mid-sentence. Instant connect stays;
                # voicemails are skipped manually (📴 tap → auto-advances).
                _amd_on = False
                st.caption("⚡ Instant connect — you hear every pickup live. "
                           "Voicemails: tap 📴 Voicemail on the softphone (it "
                           "auto-advances). Auto-skip was retired after it hung "
                           "up on live people.")
                _remaining = queue[idx:]
                _batch_size = min(_lines, len(_remaining))

                # Is a softphone queue already running for this agent?
                import requests as _req
                try:
                    _q = _req.get(f"{_base_url}/dialer/queue/status",
                                  params={"agent": _agent}, timeout=4).json()
                except Exception:
                    _q = {}
                if _q.get("active"):
                    st.info(
                        f"📞 Softphone queue running — **{_q.get('remaining', '?')} of "
                        f"{_q.get('total', '?')}** leads left. Work from the softphone "
                        f"window: dispose there and the next batch dials itself."
                    )
                    _qc1, _qc2 = st.columns(2)
                    if _qc1.button("🔄 Refresh", key="q_refresh", use_container_width=True):
                        st.rerun()
                    if _qc2.button("⏹ End softphone queue", key="q_stop", use_container_width=True):
                        try:
                            _req.post(f"{_base_url}/dialer/queue/stop",
                                      json={"agent": _agent}, timeout=5)
                        except Exception:
                            pass
                        st.rerun()
                else:
                    # One real click: opens the softphone (same click = popup
                    # blockers allow it) AND loads the whole remaining list into
                    # the server-side queue. From then on the softphone drives
                    # itself: dispose → next batch dials automatically. Leads
                    # already dialed today / claimed by the other agent are
                    # skipped server-side at claim time.
                    #
                    # NOTE: this must be components.html (iframe), NOT st.markdown —
                    # Streamlit's HTML sanitizer strips onclick handlers, so an
                    # anchor with JS silently does nothing. Component iframes run
                    # scripts, and window.open from a real click passes blockers.
                    _lead_js = "[" + ",".join(str(int(l)) for l in _remaining) + "]"
                    _amd_js = "true" if _amd_on else "false"
                    _lines_word = "line" if _batch_size == 1 else "lines"
                    components.html(f"""
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
  #startBtn {{
    display:block; width:100%; padding:13px; border:none; cursor:pointer;
    background:linear-gradient(180deg,#5E8A67,#49764F); color:#fff;
    border-radius:8px; font-size:15px; font-weight:700; text-align:center;
  }}
  #startBtn:hover {{ filter:brightness(1.05); }}
  #startBtn:disabled {{ opacity:.75; cursor:default; }}
</style>
<button id="startBtn">▶ Start Dialing ({_batch_size} {_lines_word} at a time)</button>
<script>
  var btn = document.getElementById('startBtn');
  btn.addEventListener('click', function() {{
    window.open('{_softphone_url}', 'crm_softphone_{_agent}',
                'width=390,height=640,resizable=yes');
    btn.disabled = true;
    btn.textContent = '📞 Dialing — work from the softphone window';
    fetch('{_base_url}/dialer/queue/start', {{
      method: 'POST',
      body: JSON.stringify({{agent: '{_agent}', lead_ids: {_lead_js},
                             lines: {_batch_size}, amd: {_amd_js}}})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(j) {{
      if (j.error === 'pool_exhausted') {{
        btn.textContent = '🛑 ' + (j.message || 'Daily dial limit reached — resumes tomorrow');
      }} else if (j.error) {{
        btn.disabled = false;
        btn.textContent = '⚠️ ' + (j.message || j.error) + ' — click to retry';
      }} else if (j.done) {{
        btn.textContent = '✅ Nothing left to dial (all already dialed today)';
      }}
    }})
    .catch(function() {{
      btn.disabled = false;
      btn.textContent = '⚠️ Backend unreachable — run Launch CRM, then click to retry';
    }});
  }});
</script>
""", height=54)
                    st.caption(
                        "One click: the softphone opens, you hear it ringing, and after "
                        "each call you dispose right in the softphone — the next batch "
                        "dials automatically until the list is done."
                    )
            else:
                for i, p in enumerate(phones, 1):
                    pcid = _auto_caller_id(p)
                    _call_label = (f"📞 Browser Call Phone {i}: {p}  (from {pcid})"
                                   if _is_browser
                                   else f"📞 Call Phone {i}: {p}  (from {pcid})")
                    if st.button(_call_label,
                                 key=f"dial_call_{lead_id}_{i}",
                                 type="primary" if i == 1 else "secondary",
                                 use_container_width=True):
                        import requests as _req
                        # Claim this lead first — if the other agent has it (or
                        # anyone dialed it today), skip instead of double-dialing.
                        try:
                            _cl = _req.post(
                                f"{_base_url}/dialer/claim",
                                json={"agent": _agent, "candidates": [lead_id], "count": 1},
                                timeout=10,
                            ).json()
                        except Exception:
                            _cl = {"to_dial": [lead_id]}   # claim server unreachable: fail open
                        if not _cl.get("to_dial"):
                            st.session_state["dialer_queue"] = [
                                l for l in st.session_state["dialer_queue"] if l != lead_id
                            ]
                            st.toast("⏭ Lead taken by the other agent or already dialed today — skipped.")
                            st.rerun()
                        if not _is_browser:
                            res = place_call(lead_id, from_number=_FROM_OPTS[pcid], to_number=p)
                            if res.get("called"):
                                st.session_state["dialer_on_call"] = True
                                st.session_state["dialer_call_phone"] = p
                                st.toast(f"📞 Calling {p} from {pcid} — your phone rings first.")
                                st.rerun()
                            else:
                                st.error(f"Call failed: {res.get('error', 'unknown error')}")
                        else:
                            if not _browser_ready:
                                st.error("Browser calling not set up. Run `python setup_twilio_app.py`.")
                            else:
                                _ts = datetime.utcnow().isoformat()
                                _req.post(
                                    f"{_base_url}/dialer/current",
                                    json={
                                        "agent": _agent,
                                        "lead_id": lead_id,
                                        "caller_id": _FROM_OPTS[pcid],
                                        "lead_name": lead["name"],
                                        "lead_phone": p,
                                        "ts": _ts,
                                    },
                                    timeout=3,
                                )
                                st.session_state["dialer_on_call"] = True
                                st.session_state["dialer_call_phone"] = p
                                st.rerun()

            nc1, nc2, nc3 = st.columns(3)
            if nc1.button("⏮ Prev", key="dial_prev", disabled=idx == 0,
                          use_container_width=True):
                st.session_state["dialer_idx"] = max(0, idx - 1)
                st.rerun()
            if nc2.button("Skip ⏭", key="dial_skip", disabled=idx >= len(queue) - 1,
                          use_container_width=True):
                st.session_state["dialer_idx"] = min(len(queue) - 1, idx + 1)
                st.rerun()
            if nc3.button("Open lead", key="dial_open", use_container_width=True):
                nav_to("Leads", lead_id)
        else:
            # ── On-call view ──────────────────────────────────────────────
            _batch_id = st.session_state.get("dialer_batch_id")
            _winner_id = None
            _winner_name = ""
            if _batch_id:
                try:
                    import requests as _req
                    _br = _req.get(f"{_base_url}/dialer/batch/{_batch_id}", timeout=3).json()
                    _winner_id = _br.get("winner")
                    _winner_name = _br.get("winner_name", "")
                    _batch_status = _br.get("status", "")
                    if _batch_status == "waiting_agent":
                        st.info("Waiting for the softphone to join the call... "
                                "**Keep the softphone popup open and visible.**")
                    elif _batch_status == "no_agent":
                        st.error("The softphone never joined, so no calls were placed. "
                                 "Open the softphone popup (Dialer page) and keep it visible, then dial again.")
                    elif _batch_status == "ringing":
                        st.info(f"📞 Ringing {_br.get('total', '?')} lines — listen in the softphone.")
                    elif _batch_status == "connected" and _winner_name:
                        st.success(f"Connected to: **{_winner_name}** ({_br.get('winner_phone', '')})")
                    elif _batch_status == "no_answer":
                        st.warning("No live answer on this batch.")
                    _mc = _br.get("machines_count", 0)
                    if _mc:
                        st.caption(f"🤖 {_mc} voicemail(s) auto-skipped and logged as 'Voicemail (auto)'")
                except Exception:
                    pass

            # Advance WITHOUT re-logging — for when you already disposed in the
            # softphone popup. Cycles any un-reached batch leads back into the queue.
            st.caption("Disposed in the softphone popup? Just hit **Next lead**. "
                       "Or pick an outcome here to save *and* advance.")
            if st.button("▶ Next lead", key="dial_next", type="primary",
                         use_container_width=True):
                if _batch_id:
                    _batch_leads = st.session_state.get("dialer_batch_leads", [])
                    _dropped = ([lid for lid in _batch_leads if lid != _winner_id]
                                if _winner_id else _batch_leads)
                    st.session_state["dialer_queue"].extend(_dropped)
                    st.session_state["dialer_batch_id"] = None
                    st.session_state["dialer_batch_leads"] = []
                st.session_state["dialer_on_call"] = False
                if st.session_state["dialer_idx"] < len(queue) - 1:
                    st.session_state["dialer_idx"] += 1
                st.rerun()

            st.markdown("**Or save the outcome here (also advances):**")
            d_cols = st.columns(3)
            for i, label in enumerate(DISPOSITIONS):
                if d_cols[i % 3].button(label, key=f"dial_disp_{label}",
                                        use_container_width=True):
                    if _batch_id and _winner_id:
                        _dispose(_winner_id, label)
                        _batch_leads = st.session_state.get("dialer_batch_leads", [])
                        _dropped = [lid for lid in _batch_leads if lid != _winner_id]
                        if _dropped:
                            _q = st.session_state["dialer_queue"]
                            _q.extend(_dropped)
                        st.session_state["dialer_batch_id"] = None
                        st.session_state["dialer_batch_leads"] = []
                    else:
                        _dispose(lead_id, label)

            _cancel_col1, _cancel_col2 = st.columns(2)
            if _cancel_col1.button("✖ Cancel (didn't actually call)", key="dial_cancel"):
                if _batch_id:
                    _batch_leads = st.session_state.get("dialer_batch_leads", [])
                    _q = st.session_state["dialer_queue"]
                    _q.extend(_batch_leads)
                    st.session_state["dialer_batch_id"] = None
                    st.session_state["dialer_batch_leads"] = []
                st.session_state["dialer_on_call"] = False
                st.rerun()
            if _batch_id and _batch_status in ("no_answer", "no_agent"):
                if _cancel_col2.button("⏭ Skip batch — next", key="dial_skip_batch"):
                    _batch_leads = st.session_state.get("dialer_batch_leads", [])
                    _q = st.session_state["dialer_queue"]
                    _q.extend(_batch_leads)
                    st.session_state["dialer_batch_id"] = None
                    st.session_state["dialer_batch_leads"] = []
                    st.session_state["dialer_on_call"] = False
                    st.rerun()

elif page == "caller_health":
    st.subheader("📡 Caller ID Health")
    st.caption("Number pool for the multi-line dialer — rotation, daily caps, "
               "warm-up, and spam-flag detection.")

    pool = db.fetch_caller_pool()

    if not pool:
        st.info(
            "No numbers in the pool yet.\n\n"
            "**To add your 10-number pool:**\n"
            "1. Preview available numbers: `python buy_numbers.py --area 515 --count 10`\n"
            "2. Purchase them: `python buy_numbers.py --area 515 --count 10 --buy`\n\n"
            "New numbers start in **warming** mode (low volume for 7 days) automatically."
        )
    else:
        # Auto-detect likely-flagged numbers from answer-rate crater
        suspects = caller_id_pool.detect_flagged()
        suspect_nums = {s["phone_number"]: s for s in suspects}
        if suspects:
            st.warning(
                "⚠️ **Possible spam flags detected** (answer rate cratered vs. pool median):\n\n"
                + "\n".join(
                    f"- **{s['phone_number']}** — answering {int(s['answer_rate']*100)}% "
                    f"vs pool median {int(s['pool_median']*100)}%"
                    for s in suspects
                )
            )

        # Summary metrics
        active = sum(1 for n in pool if n["status"] == "active")
        warming = sum(1 for n in pool if n["status"] == "warming")
        flagged = sum(1 for n in pool if n["status"] == "flagged")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total numbers", len(pool))
        m2.metric("Active", active)
        m3.metric("Warming", warming)
        m4.metric("Flagged", flagged)

        st.divider()

        for num in pool:
            phone = num["phone_number"]
            used = db.caller_calls_today(phone)
            cap = num["daily_cap"]
            rate = caller_id_pool.answer_rate(phone)
            is_suspect = phone in suspect_nums

            status_color = {
                "active": "#28a745", "warming": "#ffc107",
                "flagged": "#dc3545", "retired": "#888",
            }.get(num["status"], "#888")

            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            with c1:
                st.markdown(
                    f"<span class='sl-num'>{phone}</span> "
                    f"<span class='sl-chip' style='background:{status_color};padding:1px 8px;"
                    f"border-radius:10px;font-size:0.75rem'>{num['status']}</span>"
                    + (" <span style='color:#dc3545;font-size:0.8rem'>⚠️ flag?</span>"
                       if is_suspect else ""),
                    unsafe_allow_html=True,
                )
                _warm_note = ""
                if num["status"] == "warming":
                    # Pool-level, from the DB: a warm-up day = a past day the
                    # pool placed 10+ real calls. Every number ramps together.
                    _days_done = caller_id_pool.pool_warmup_days_completed()
                    _warm_note = (f" · warm-up day {_days_done + 1}/{caller_id_pool.WARMUP_DAYS}"
                                  f" · dial {caller_id_pool.MIN_POOL_CALLS_PER_DAY}+ today to advance")
                st.caption(f"Area {num['area_code']}{_warm_note}")
            with c2:
                _eff_cap = caller_id_pool._effective_cap(num)
                st.markdown(f"**{used} / {_eff_cap}**")
                st.caption("calls today (hard cap)")
            with c3:
                st.markdown(f"**{int(rate*100)}%**" if rate is not None else "—")
                st.caption("answer rate")
            with c4:
                if num["status"] == "flagged":
                    if st.button("Reactivate", key=f"react_{num['id']}"):
                        db.update_caller_number(num["id"], {"status": "active", "reputation": "unknown"})
                        st.rerun()
                else:
                    if st.button("Mark flagged", key=f"flag_{num['id']}"):
                        db.update_caller_number(num["id"], {"status": "flagged", "reputation": "flagged"})
                        st.rerun()
            st.divider()

    with st.expander("🛡️ How spam-flag protection works"):
        st.markdown(
            "- **Rotation:** each call picks a number matching the lead's area code, "
            "spreading load and respecting daily caps.\n"
            "- **Daily caps:** 50 calls/number fully warmed — a hard limit; the "
            "dialer stops for the day rather than exceed it.\n"
            "- **Warm-up (pool-level, from real dialing):** the whole pool "
            "advances one warm-up day for each past day it placed 10+ calls "
            "(read from the call record, so it survives restarts). Per-number "
            "caps ramp together: 10/day → 15 → 25 → 40 → 50 over 10 dialing "
            "days. A day with only a few test calls doesn't count.\n"
            "- **Detection:** answer rate is tracked per number; a sudden crater vs. "
            "the pool median flags a likely carrier block, and the number is pulled.\n"
            "- **Proactive registration:** run `python register_voice_integrity.py` to "
            "check Trust Hub / Voice Integrity status (registers numbers with the "
            "T-Mobile / Verizon / AT&T analytics engines)."
        )
