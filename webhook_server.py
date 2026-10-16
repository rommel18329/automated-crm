"""
Twilio inbound SMS + Voice webhook server.
Runs alongside Streamlit on port 5001.
When a seller replies to your Twilio number, Twilio POSTs here,
and the message is saved to crm.db as an inbound text interaction.
When someone calls your Twilio number, Twilio POSTs to /voice/incoming
and the call is forwarded live to MY_CELL_NUMBER, with the call logged
to the matching lead's interaction history.

Run: python webhook_server.py
Then expose with Tailscale Funnel (or ngrok): tailscale funnel --bg 5001
Set Twilio SMS webhook URL to:   https://<your-url>/twilio/inbound
Set Twilio Voice webhook URL to: https://<your-url>/voice/incoming
"""
from __future__ import annotations

import json
import os
import re
import sys
import logging
import uuid
import time
import threading
from datetime import datetime, timedelta, date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests as _http
from flask import Flask, request, Response, jsonify
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=True)

from crm_engine import db
from crm_engine import caller_id_pool
from crm_engine.messaging_gateway import place_call_number

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = PROJECT_ROOT / "crm.db"
MY_CELL_NUMBER = os.getenv("MY_CELL_NUMBER", "")
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWIML_APP_SID = os.getenv("TWILIO_TWIML_APP_SID", "")
API_KEY = os.getenv("TWILIO_API_KEY", "")
API_SECRET = os.getenv("TWILIO_API_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
FROM_IL_NUMBER = os.getenv("TWILIO_FROM_IL_NUMBER", "")
VI_SERVICE_SID = os.getenv("VOICE_INTELLIGENCE_SERVICE_SID", "")  # call transcription
MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")  # A2P campaign

# transcript_sid -> lead_id, so the transcript webhook can find its lead
_transcript_to_lead: dict[str, int] = {}

# ── Speed-to-lead alerts: ping the user's cell the moment a seller reaches
# out (inbound text / missed call / voicemail). Replying in minutes instead
# of hours is the highest-proven conversion lever in outbound real estate.
_last_alert: dict[str, float] = {}     # key -> monotonic ts (rate limit)
_ALERT_COOLDOWN = 300                  # max one alert per lead per 5 min


def _alert_cell(key: str, message: str) -> None:
    """SMS the user's cell (MY_CELL_NUMBER) from the established number.
    Background-threaded by callers; rate-limited per source key."""
    if not MY_CELL_NUMBER or not FROM_NUMBER:
        return
    now = time.monotonic()
    if now - _last_alert.get(key, -_ALERT_COOLDOWN) < _ALERT_COOLDOWN:
        return
    _last_alert[key] = now
    try:
        from twilio.rest import Client as TwilioClient
        _kw = {"body": message[:320], "from_": FROM_NUMBER,
               "to": to_e164(MY_CELL_NUMBER)}
        if MESSAGING_SERVICE_SID:
            _kw["messaging_service_sid"] = MESSAGING_SERVICE_SID
        TwilioClient(ACCOUNT_SID, AUTH_TOKEN).messages.create(**_kw)
        log.info(f"Speed-to-lead alert sent: {message[:60]}")
    except Exception as exc:
        log.warning(f"Cell alert failed: {exc}")


# ── Daily "sellers are waiting on you" reminder ─────────────────────────────
# Every morning (DAILY_REMINDER_HOUR local, default 9), text the user's cell a
# digest of every thread where a seller had the last word and every missed
# call not yet returned. Reuses the persistent read-state, so an item stops
# nagging the moment it's actually replied to / called back / marked read —
# and keeps nagging daily until then.
DAILY_REMINDER_HOUR = int(os.getenv("DAILY_REMINDER_HOUR", "9"))
_DIGEST_STATE = PROJECT_ROOT / "daily_digest_state.json"


def _waiting_digest() -> str:
    """Compose the digest, or '' when nobody is waiting."""
    try:
        unread_ids = db.unread_message_lead_ids(DB_PATH)
        missed = db.fetch_unacknowledged_missed_calls(DB_PATH)
        unmatched = db.fetch_unmatched_inbox(DB_PATH)
    except Exception as exc:
        log.warning(f"digest query failed: {exc}")
        return ""
    if not unread_ids and not missed and not unmatched:
        return ""
    parts = []
    if unread_ids:
        with db.get_conn(DB_PATH) as conn:
            rows = conn.execute(
                """SELECT i.lead_id, l.name, MAX(i.timestamp) t
                   FROM interactions i JOIN leads l ON l.id = i.lead_id
                   WHERE i.type='text' AND i.direction='inbound'
                     AND i.lead_id IN (%s)
                   GROUP BY i.lead_id ORDER BY t DESC"""
                % ",".join("?" * len(unread_ids)),
                tuple(unread_ids),
            ).fetchall()
        names = []
        for r in rows[:5]:
            try:
                days = (date.today() - datetime.fromisoformat(r["t"]).date()).days
                names.append(f"{r['name']} ({days}d)" if days > 0 else r["name"])
            except Exception:
                names.append(r["name"])
        more = f" +{len(unread_ids) - 5} more" if len(unread_ids) > 5 else ""
        parts.append(f"{len(unread_ids)} awaiting a text reply: "
                     + ", ".join(names) + more)
    if missed:
        mnames = ", ".join(m["name"] for m in missed[:4])
        more = f" +{len(missed) - 4} more" if len(missed) > 4 else ""
        parts.append(f"{len(missed)} missed call(s) not returned: {mnames}{more}")
    if unmatched:
        gv_un = sum(1 for u in unmatched if (u.get("content") or "").startswith("[Google Voice]"))
        gv_note = f" ({gv_un} on Google Voice)" if gv_un else ""
        parts.append(f"{len(unmatched)} msg(s) from unknown numbers to triage{gv_note}")
    return "⏰ CRM daily: " + " | ".join(parts) + " — open the dashboard."


def _daily_digest_loop() -> None:
    while True:
        try:
            now = datetime.now()
            sent_day = ""
            try:
                sent_day = json.loads(_DIGEST_STATE.read_text()).get("sent", "")
            except Exception:
                pass
            if now.hour >= DAILY_REMINDER_HOUR and sent_day != now.date().isoformat():
                msg = _waiting_digest()
                if msg:
                    _alert_cell("daily-digest", msg)
                # mark the day handled either way; fresh events during the day
                # get instant speed-to-lead alerts anyway
                _DIGEST_STATE.write_text(json.dumps({"sent": now.date().isoformat()}))
        except Exception as exc:
            log.warning(f"daily digest loop error: {exc}")
        time.sleep(600)


threading.Thread(target=_daily_digest_loop, daemon=True).start()

IL_AREA_CODES = {"217","224","309","312","331","618","630","708","773","779","815","847"}

def pick_from_number(to_phone: str) -> str:
    digits = re.sub(r"\D", "", to_phone or "")
    area = digits[:3] if len(digits) == 10 else digits[1:4] if len(digits) == 11 else ""
    return FROM_IL_NUMBER if (area in IL_AREA_CODES and FROM_IL_NUMBER) else FROM_NUMBER

# In-memory map: CallSid → lead_id, used to attach recordings to the right lead
_call_sid_to_lead: dict[str, int] = {}

# Per-agent dial channels for the persistent softphone popups.
# Streamlit POSTs here when an agent clicks CALL; that agent's popup polls
# its own channel — two agents (rom / gabby) never see each other's dials.
_dialer_current: dict[str, dict] = {}   # agent -> {lead_id, caller_id, ..., ts}

# Lead claims: while an agent is dialing a lead, the other agent can't.
# In-memory (single webhook server = single source of truth), TTL-expired.
# The permanent guard is db.leads_dialed_today() — anything dialed today by
# anyone is excluded for the rest of the day.
_lead_claims: dict[int, dict] = {}      # lead_id -> {"agent": str, "ts": float}
_CLAIM_TTL = 600.0                       # seconds a claim blocks the other agent


def _prune_claims() -> None:
    """Call with _batch_lock held."""
    now = time.time()
    for lid in [l for l, c in _lead_claims.items() if now - c["ts"] > _CLAIM_TTL]:
        _lead_claims.pop(lid, None)

# Multi-line batch state.  batch_id -> dict with:
#   conference, lead_ids, call_sids{lead_id: sid}, winner(lead_id),
#   winner_name, winner_phone, dropped[], resolved_count, total, status
_multi_batches: dict[str, dict] = {}
_batch_lock = threading.Lock()

# Serializes caller-ID pick + daily-counter reservation so two agents firing
# batches at the same moment can never oversubscribe a number's hard cap.
_pool_pick_lock = threading.Lock()


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def to_e164(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


# ── Ringback tone for the multi-line dialer ─────────────────────────────────
# While the softphone waits in the conference for a lead to answer, Twilio
# plays this instead of dead silence (US ringback: 440+480 Hz, 2s on / 4s off).
_RINGBACK_WAV = PROJECT_ROOT / "static" / "ringback.wav"


def _ensure_ringback() -> None:
    """Generate static/ringback.wav once (stdlib only — no numpy needed)."""
    if _RINGBACK_WAV.exists():
        return
    import math
    import struct
    import wave
    _RINGBACK_WAV.parent.mkdir(exist_ok=True)
    rate = 8000
    frames = bytearray()
    for i in range(rate * 2):            # 2s ring tone
        t = i / rate
        v = 0.25 * (math.sin(2 * math.pi * 440 * t) + math.sin(2 * math.pi * 480 * t))
        frames += struct.pack("<h", int(v * 32767))
    frames += b"\x00\x00" * (rate * 4)   # 4s silence
    with wave.open(str(_RINGBACK_WAV), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    log.info(f"Generated ringback tone: {_RINGBACK_WAV}")


_ensure_ringback()


@app.route("/voice/wait-music", methods=["GET", "POST"])
def voice_wait_music():
    """Conference waitUrl: loop the ringback tone until a lead answers."""
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Play loop="0">{PUBLIC_BASE_URL}/static/ringback.wav</Play>'
        '</Response>'
    )
    return Response(twiml, mimetype="text/xml")


@app.route("/twilio/inbound", methods=["POST"])
def inbound_sms():
    from_raw = request.form.get("From", "")
    body = request.form.get("Body", "").strip()

    log.info(f"Inbound SMS from {from_raw}: {body[:80]}")

    # Filter Twilio system messages regardless of who sent them
    if "Configure your number" in body or "Reply STOP to unsubscribe" in body:
        log.info("Filtered Twilio system message")
        return _twiml_response()

    phone = normalize_phone(from_raw)
    if not phone:
        log.warning(f"Could not normalize phone: {from_raw}")
        return _twiml_response()

    # Match to lead
    leads = db.fetch_leads(DB_PATH)
    lead = next((l for l in leads if l["phone"] == phone), None)

    if not lead:
        # Unknown number — don't drop it, log it to the unmatched inbox
        db.add_unmatched_message(phone, "text", body, DB_PATH)
        log.info(f"No lead found for {phone} — logged to unmatched inbox")
        if phone != normalize_phone(MY_CELL_NUMBER):
            threading.Thread(target=_alert_cell, args=(
                f"sms:{phone}",
                f"💬 CRM: text from unknown {phone}: \"{body[:120]}\""),
                daemon=True).start()
        return _twiml_response()

    # Save inbound text
    ts = datetime.utcnow()
    db.add_interaction(lead["id"], "text", body, "inbound", DB_PATH, ts=ts)

    # Re-surface lead in follow-up queue immediately
    today = datetime.utcnow().strftime("%Y-%m-%d")
    db.update_lead(lead["id"], {"next_action_date": today}, DB_PATH)

    # Clear cached suggestion so it regenerates with new context
    with db.get_conn(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM followup_suggestions WHERE lead_id=?", (lead["id"],)
        )

    # Speed-to-lead: ping the user's cell so the reply happens in minutes.
    if phone != normalize_phone(MY_CELL_NUMBER):
        threading.Thread(target=_alert_cell, args=(
            f"lead:{lead['id']}",
            f"💬 CRM: {lead['name']} ({lead['status']}) texted: \"{body[:130]}\" "
            f"— reply from Messages or text them at {lead['phone']}"),
            daemon=True).start()

    log.info(f"Saved inbound text from {lead['name']} ({phone}): {body[:60]}")
    return _twiml_response()


@app.route("/voice/incoming", methods=["POST"])
def voice_incoming():
    """Someone called the Twilio number. Forward it live to MY_CELL_NUMBER,
    record both legs of the call, and log it on the matching lead (if found)."""
    from_raw = request.form.get("From", "")
    call_sid = request.form.get("CallSid", "")
    log.info(f"Inbound call from {from_raw} (CallSid: {call_sid})")

    phone = normalize_phone(from_raw)
    lead = None
    if phone:
        leads = db.fetch_leads(DB_PATH)
        lead = next((l for l in leads if l["phone"] == phone), None)

    if not MY_CELL_NUMBER:
        log.warning("MY_CELL_NUMBER not set in .env — cannot forward call")
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say>This number is not yet set up to take calls.</Say></Response>',
            mimetype="text/xml",
        )

    if lead:
        ts = datetime.utcnow()
        db.add_interaction(lead["id"], "call", "Inbound call (forwarded to cell)", "inbound", DB_PATH, ts=ts)
        if call_sid:
            _call_sid_to_lead[call_sid] = lead["id"]
        log.info(f"Logged inbound call from {lead['name']} ({phone})")
    else:
        log.info(f"Inbound call from unknown number: {from_raw}")

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Dial timeout="20" callerId="{from_raw}" action="/voice/status"'
        ' record="record-from-ringing-dual"'
        f' recordingStatusCallback="{PUBLIC_BASE_URL}/voice/recording"'
        ' recordingStatusCallbackMethod="POST">'
        f'{MY_CELL_NUMBER}'
        '</Dial>'
        '</Response>'
    )
    return Response(twiml, mimetype="text/xml")


@app.route("/voice/connect/<int:lead_id>", methods=["POST", "GET"])
def voice_connect(lead_id: int):
    """Click-to-call leg 2: fired once you answer the call to your own cell
    (triggered from the CRM). Dials the lead and bridges the two legs."""
    lead = db.fetch_lead(lead_id, DB_PATH)
    if not lead:
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say>Lead not found.</Say></Response>',
            mimetype="text/xml",
        )

    from_number = os.getenv("TWILIO_FROM_NUMBER", "")
    # The dialer can request a specific alternate number via ?to=; otherwise
    # fall back to the lead's primary stored phone.
    to_override = request.args.get("to", "")
    lead_e164 = to_e164(to_override) if to_override else to_e164(lead["phone"])
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Say>Connecting you to {lead["name"]}.</Say>'
        f'<Dial callerId="{from_number}">{lead_e164}</Dial>'
        '</Response>'
    )
    return Response(twiml, mimetype="text/xml")


@app.route("/voice/connect-raw", methods=["POST", "GET"])
def voice_connect_raw():
    """Click-to-call leg 2 for an ad-hoc number (Messages quick-dial). Bridges
    your answered cell to the raw number passed as ?to=."""
    to_raw = request.args.get("to", "")
    if not to_raw:
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say>No number specified.</Say></Response>',
            mimetype="text/xml",
        )
    from_number = pick_from_number(to_raw)
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Dial callerId="{from_number}">{to_e164(to_raw)}</Dial>'
        '</Response>'
    )
    return Response(twiml, mimetype="text/xml")


@app.route("/voice/status", methods=["POST"])
def voice_status():
    """Called after the forwarded call ends. If it wasn't answered, log a
    missed-call note and let the caller leave a real voicemail."""
    dial_status = request.form.get("DialCallStatus", "")
    from_raw = request.form.get("From", "")
    log.info(f"Call from {from_raw} ended with status: {dial_status}")

    if dial_status not in ("completed", "answered"):
        phone = normalize_phone(from_raw)
        if phone:
            leads = db.fetch_leads(DB_PATH)
            lead = next((l for l in leads if l["phone"] == phone), None)
            if lead:
                db.add_interaction(
                    lead["id"], "note", "Missed inbound call (no answer)", "inbound", DB_PATH
                )
                threading.Thread(target=_alert_cell, args=(
                    f"call:{lead['id']}",
                    f"📞 CRM: MISSED CALL from {lead['name']} ({lead['status']}) "
                    f"{lead['phone']} — call back ASAP, they're live right now"),
                    daemon=True).start()
            else:
                threading.Thread(target=_alert_cell, args=(
                    f"call:{phone}",
                    f"📞 CRM: missed call from unknown {phone} — likely a "
                    f"callback from the dialer"), daemon=True).start()
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            '<Say>Sorry we missed you. Please leave a message after the tone.</Say>'
            '<Record maxLength="120" transcribe="true" '
            'transcribeCallback="/voice/voicemail" playBeep="true"/>'
            '<Say>We did not get a message. Goodbye.</Say>'
            '</Response>',
            mimetype="text/xml",
        )

    return _twiml_response()


@app.route("/voice/voicemail", methods=["POST"])
def voice_voicemail():
    """Twilio's transcription callback for a recorded voicemail."""
    from_raw = request.form.get("Caller", "") or request.form.get("From", "")
    recording_url = request.form.get("RecordingUrl", "")
    transcription = (request.form.get("TranscriptionText", "") or "").strip()
    transcription_status = request.form.get("TranscriptionStatus", "")

    if not transcription:
        transcription = "(transcription unavailable, see recording)"
    log.info(f"Voicemail from {from_raw} ({transcription_status}): {transcription[:80]}")

    phone = normalize_phone(from_raw)
    content = f"Voicemail: {transcription}"

    # Speed-to-lead: the VM text tells the user WHAT they want, right on their cell.
    if phone and phone != normalize_phone(MY_CELL_NUMBER):
        threading.Thread(target=_alert_cell, args=(
            f"vm:{phone}",
            f"🎙 CRM: voicemail from {phone}: \"{transcription[:180]}\""),
            daemon=True).start()

    if phone:
        leads = db.fetch_leads(DB_PATH)
        lead = next((l for l in leads if l["phone"] == phone), None)
        if lead:
            db.add_interaction(lead["id"], "call", content, "inbound", DB_PATH,
                               recording_url=recording_url or None)
            with db.get_conn(DB_PATH) as conn:
                conn.execute(
                    "DELETE FROM followup_suggestions WHERE lead_id=?", (lead["id"],)
                )
            log.info(f"Logged voicemail from {lead['name']} ({phone})")
            return ("", 204)

    # No matching lead — log to the unmatched inbox instead of losing it
    db.add_unmatched_message(phone or from_raw, "voicemail", content, DB_PATH, recording_url=recording_url)
    log.info(f"Voicemail from unmatched number {phone or from_raw} — logged to unmatched inbox")
    return ("", 204)


@app.route("/voice/recording", methods=["POST"])
def voice_recording():
    """Twilio fires this when a live forwarded call recording is ready.
    We match via CallSid → lead_id and attach the recording URL to that lead's
    most recent call interaction."""
    call_sid = request.form.get("CallSid", "")
    recording_url = request.form.get("RecordingUrl", "")
    recording_sid = request.form.get("RecordingSid", "")
    duration = request.form.get("RecordingDuration", "?")
    log.info(f"Recording ready: CallSid={call_sid} SID={recording_sid} duration={duration}s")

    lead_id = _call_sid_to_lead.get(call_sid)
    if lead_id and recording_url:
        db.update_call_recording(lead_id, recording_url, DB_PATH)
        log.info(f"Attached recording {recording_sid} to lead {lead_id}")
        _call_sid_to_lead.pop(call_sid, None)
        if VI_SERVICE_SID and recording_sid:
            threading.Thread(target=_start_transcription,
                             args=(recording_sid, lead_id), daemon=True).start()
    else:
        log.info(f"No lead mapping for CallSid {call_sid} — recording not attached")
    return ("", 204)


def _start_transcription(recording_sid: str, lead_id: int) -> None:
    """Kick off a Voice Intelligence transcript for a finished recording.
    Background thread; failures are logged, never affect the call."""
    try:
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(ACCOUNT_SID, AUTH_TOKEN)
        tr = twilio.intelligence.v2.transcripts.create(
            service_sid=VI_SERVICE_SID,
            channel={"media_properties": {"source_sid": recording_sid}},
            customer_key=str(lead_id),   # survives a webhook restart
        )
        _transcript_to_lead[tr.sid] = lead_id
        log.info(f"Transcription started {tr.sid} for lead {lead_id} (rec {recording_sid})")
    except Exception as exc:
        log.warning(f"Transcription start failed for rec {recording_sid}: {exc}")


@app.route("/voice/transcript", methods=["POST"])
def voice_transcript():
    """Voice Intelligence webhook: fires when a transcript's status changes.
    On completion we assemble the sentences and save them to the lead."""
    transcript_sid = request.form.get("transcript_sid") or request.form.get("TranscriptSid", "")
    status = request.form.get("status") or request.form.get("Status", "")
    log.info(f"Transcript webhook: {transcript_sid} status={status}")
    if transcript_sid and status == "completed":
        threading.Thread(target=_save_transcript, args=(transcript_sid,),
                         daemon=True).start()
    return ("", 204)


def _auto_call_notes(lead_id: int, text: str) -> None:
    """AI call note + call facts for one transcript, only while the Dialer's
    "AI call notes" switch is on (turning it on is the price approval). Every
    call still goes through the ledger and the daily limit. Best-effort: never
    blocks or fails the transcript save."""
    from crm_engine import ai_budget
    from crm_engine.ai_message_generator import (extract_call_facts, generate_call_note,
                                                 prepare_call_facts, prepare_call_note)
    try:
        if not ai_budget.autonotes_status()["active"]:
            return
        lead = db.fetch_lead(lead_id, DB_PATH) or {}
        request = prepare_call_note(lead, text)
        note = generate_call_note(lead, text, approval=ai_budget.auto_approve(request), prepared=request)
        if note:
            db.add_interaction(lead_id, "note", "🤖 " + note, "outbound",
                               DB_PATH, ts=datetime.utcnow())
            log.info(f"Saved AI call note for lead {lead_id}")
    except Exception as exc:
        log.warning(f"AI call note failed for lead {lead_id}: {exc}")
    # Structured extraction: update motivation from what THEY actually said,
    # and log the concrete facts.
    try:
        if not ai_budget.autonotes_status()["active"]:
            return
        lead = db.fetch_lead(lead_id, DB_PATH)
        request = prepare_call_facts(lead or {}, text)
        facts = extract_call_facts(lead or {}, text, approval=ai_budget.auto_approve(request),
                                   prepared=request)
        ms = facts.get("motivation_score")
        fact_line = (facts.get("facts") or "").strip()
        parts = []
        if isinstance(ms, int) and 1 <= ms <= 10 and lead and ms != lead.get("motivation_score"):
            db.update_lead(lead_id, {"motivation_score": ms}, DB_PATH)
            parts.append(f"motivation {lead.get('motivation_score')}→{ms}")
        if fact_line:
            parts.append(fact_line)
        if parts:
            db.add_interaction(lead_id, "note", "📊 From the call: " + " · ".join(parts),
                               "outbound", DB_PATH, ts=datetime.utcnow())
            log.info(f"Extracted call facts for lead {lead_id}: {parts}")
    except Exception as exc:
        log.warning(f"Call fact extraction failed for lead {lead_id}: {exc}")


def _save_transcript(transcript_sid: str) -> None:
    """Fetch a completed transcript's sentences, assemble readable text, and
    log the raw transcript on the lead (+ AI notes when the Dialer switch is on)."""
    try:
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(ACCOUNT_SID, AUTH_TOKEN)
        sentences = twilio.intelligence.v2.transcripts(transcript_sid).sentences.list()
        parts = []
        for s in sorted(sentences, key=lambda x: (x.media_channel or 0, x.sentence_index or 0)):
            who = "Them" if (s.media_channel or 0) == 1 else "Rom"
            parts.append(f"{who}: {s.transcript}")
        text = "\n".join(parts).strip()
        lead_id = _transcript_to_lead.pop(transcript_sid, None)
        if lead_id is None:
            # In-memory map lost (webhook restarted) — recover from customer_key
            try:
                tr = twilio.intelligence.v2.transcripts(transcript_sid).fetch()
                lead_id = int(tr.customer_key) if tr.customer_key else None
            except Exception:
                lead_id = None
        if lead_id is None:
            log.warning(f"Transcript {transcript_sid} has no lead mapping — skipping")
            return
        if not text:
            log.info(f"Transcript {transcript_sid}: empty, nothing to save")
            return
        db.add_interaction(lead_id, "note", "📝 Call transcript:\n" + text[:4000],
                           "outbound", DB_PATH, ts=datetime.utcnow())
        log.info(f"Saved transcript {transcript_sid} to lead {lead_id} ({len(text)} chars)")
        _auto_call_notes(lead_id, text)
    except Exception as exc:
        log.warning(f"Saving transcript {transcript_sid} failed: {exc}")


@app.route("/recordings/<recording_sid>")
def serve_recording(recording_sid):
    """Proxy Twilio recording audio so the CRM can embed it without exposing credentials."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Recordings/{recording_sid}.mp3"
    try:
        r = _http.get(url, auth=(ACCOUNT_SID, AUTH_TOKEN), timeout=15)
        return Response(r.content, content_type="audio/mpeg")
    except Exception as exc:
        log.warning(f"Failed to proxy recording {recording_sid}: {exc}")
        return Response("Recording unavailable", status=502)


@app.route("/voice/token")
def voice_token():
    """Generate a short-lived Twilio access token so the browser can make calls."""
    if not API_KEY or not API_SECRET or not TWIML_APP_SID:
        return jsonify({"error": "Browser calling not set up. Run setup_twilio_app.py first."}), 500

    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant

    # Per-agent identity so two softphones (rom + gabby) register separately.
    agent = re.sub(r"[^a-z0-9]", "", (request.args.get("agent") or "dialer").lower()) or "dialer"
    token = AccessToken(ACCOUNT_SID, API_KEY, API_SECRET, identity=f"crm-{agent}")
    token.add_grant(VoiceGrant(outgoing_application_sid=TWIML_APP_SID))
    jwt = token.to_jwt()
    resp = jsonify({"token": jwt})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/voice/browser-connect", methods=["POST"])
def voice_browser_connect():
    """TwiML endpoint called by Twilio when the browser softphone dials out.

    Two modes:
    - Single-line: lead_id + caller_id → direct dial.
    - Multi-line:  conference name → join the conference and wait for a lead to answer.
    """
    conference = request.form.get("conference", "")
    if conference:
        log.info(f"Browser joining conference: {conference}")
        # The agent STARTS the room; the winner joins the already-live room.
        # Ringing feedback is played by the softphone itself (browser-side
        # audio loop) — NOT via waitUrl: a start=false wait state allowed the
        # winner to start a SECOND lifecycle of the same conference name when
        # the agent's leg raced/dropped, leaving the agent hearing ringback
        # while the lead spoke into an empty room ("connected but kept
        # ringing", seen twice on 2026-07-09).
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Dial>'
            f'<Conference startConferenceOnEnter="true" endConferenceOnExit="true"'
            f' waitUrl="" beep="false">{conference}</Conference>'
            '</Dial></Response>'
        )
        return Response(twiml, mimetype="text/xml")

    # Ad-hoc browser quick-dial: a raw number with no lead attached.
    raw_to = request.form.get("to_number", "")
    if raw_to:
        caller_id = request.form.get("caller_id", "") or pick_from_number(raw_to)
        log.info(f"Browser quick-dial → {raw_to} from {caller_id}")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response><Dial callerId="{caller_id}">{to_e164(raw_to)}</Dial></Response>'
        )
        return Response(twiml, mimetype="text/xml")

    lead_id_raw = request.form.get("lead_id", "")
    caller_id = request.form.get("caller_id", "") or os.getenv("TWILIO_FROM_NUMBER", "")
    call_sid = request.form.get("CallSid", "")

    if not lead_id_raw:
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say>No lead specified.</Say></Response>',
            mimetype="text/xml",
        )

    lead = db.fetch_lead(int(lead_id_raw), DB_PATH)
    if not lead:
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say>Lead not found.</Say></Response>',
            mimetype="text/xml",
        )

    lead_e164 = to_e164(lead["phone"])

    db.add_interaction(
        lead["id"], "call",
        f"Outbound call (browser dialer) to {lead['name']}",
        "outbound", DB_PATH, ts=datetime.utcnow(),
    )
    if call_sid:
        _call_sid_to_lead[call_sid] = lead["id"]

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Dial callerId="{caller_id}"'
        ' record="record-from-ringing-dual"'
        f' recordingStatusCallback="{PUBLIC_BASE_URL}/voice/recording"'
        ' recordingStatusCallbackMethod="POST">'
        f'{lead_e164}'
        '</Dial>'
        '</Response>'
    )
    log.info(f"Browser call → {lead['name']} ({lead_e164}) from {caller_id}")
    return Response(twiml, mimetype="text/xml")


def _mark_resolved(batch: dict, lead_id: int) -> None:
    """Record that one line of a multi-line batch has finished (caller holds
    _batch_lock). Idempotent per lead, so a late Twilio status callback and the
    reconciler can't double-count."""
    done = batch.setdefault("resolved", set())
    if lead_id in done:
        return
    done.add(lead_id)
    batch["resolved_count"] = len(done)
    if batch["resolved_count"] >= batch["total"] and batch["winner"] is None:
        batch["status"] = "no_answer"


_TERMINAL_CALL_STATES = {"completed", "busy", "no-answer", "failed", "canceled"}


def _reconcile_batch(batch_id: str, twilio, first_wait: float = 40, step: float = 10,
                     cap: float = 120, sleep=time.sleep) -> None:
    """Don't depend on every Twilio status callback arriving. Callbacks can be
    lost (the public tunnel returned 502s for 3 of 10 on 2026-10-14), which left
    the batch "ringing" forever and locked the agent out of dialing. After the
    lines should have rung out, ask Twilio directly for any unresolved call;
    at `cap` seconds close the batch as no-answer no matter what."""
    waited = 0.0
    sleep(first_wait); waited += first_wait
    while True:
        with _batch_lock:
            batch = _multi_batches.get(batch_id)
            if not batch or batch["status"] not in ("waiting_agent", "ringing"):
                return
            pending = [(lid, batch["call_sids"].get(lid)) for lid in batch["lead_ids"]
                       if lid not in batch.get("resolved", set()) and lid != batch["winner"]]
        for lid, sid in pending:
            if not sid:
                continue
            try:
                state = twilio.calls(sid).fetch().status
            except Exception as exc:
                log.warning(f"Multi [{batch_id}] reconcile: could not fetch {sid}: {exc}")
                continue
            if state in _TERMINAL_CALL_STATES:
                with _batch_lock:
                    if lid not in batch.get("resolved", set()):
                        log.info(f"Multi [{batch_id}] reconcile: lead {lid} was {state} "
                                 f"(status callback never arrived)")
                    _mark_resolved(batch, lid)
        with _batch_lock:
            if batch["status"] not in ("waiting_agent", "ringing"):
                return
            if waited >= cap:
                if batch["winner"] is None:
                    batch["status"] = "no_answer"
                    log.warning(f"Multi [{batch_id}] still unresolved after {int(cap)}s "
                                f"({batch['resolved_count']}/{batch['total']}) — closing as no answer")
                return
        sleep(step); waited += step


def _start_multi_batch(agent: str, lead_ids: list[int], amd: bool = True) -> str:
    """Fire N simultaneous outbound calls. First answer wins and joins
    the conference where the browser softphone is waiting. Returns batch_id.
    Used by /dialer/multi-start and the server-side queue."""
    batch_id = uuid.uuid4().hex[:12]
    conference = f"multi_{batch_id}"
    batch = {
        "batch_id": batch_id,
        "conference": conference,
        "agent": agent,
        "lead_ids": lead_ids,
        "call_sids": {},
        "from_numbers": {},   # lead_id -> caller ID used (for answer-rate stats)
        "winner": None,
        "winner_name": "",
        "winner_phone": "",
        "dropped": [],
        "machines": [],       # lead_ids classified as voicemail by AMD
        "amd": bool(amd),
        "resolved_count": 0,
        "total": len(lead_ids),
        "status": "waiting_agent",   # waiting for the softphone to join the room
        "agent_ready": False,
    }

    with _batch_lock:
        _multi_batches[batch_id] = batch

    def _fire_calls():
        # Wait until the softphone has actually joined the conference, so the
        # winning call never drops into an empty room (the cause of the silence).
        # 35s: Twilio Device.connect() is occasionally slow (~2 min seen once);
        # the softphone's own watchdog handles the rare join that outlasts this.
        waited = 0.0
        while waited < 35:
            with _batch_lock:
                if batch.get("agent_ready"):
                    break
            time.sleep(0.25); waited += 0.25
        with _batch_lock:
            if not batch.get("agent_ready"):
                batch["status"] = "no_agent"
                # Clear the dead command from the channel so a popup opened
                # later can't replay it into a dead conference.
                cur = _dialer_current.get(batch["agent"])
                if cur and cur.get("batch_id") == batch_id:
                    _dialer_current.pop(batch["agent"], None)
                log.warning(f"Multi [{batch_id}] softphone never joined — calls NOT fired")
            else:
                batch["status"] = "ringing"
        if batch["status"] == "no_agent":
            _requeue_leads(agent, lead_ids)
            return

        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(ACCOUNT_SID, AUTH_TOKEN)

        # Pre-assign a DISTINCT caller ID per line (sequential, so no race gives
        # two lines the same number — that's the concurrency/spam risk). Also
        # exclude numbers currently ringing in the OTHER agent's active batches.
        assignments = []
        with _batch_lock:
            used_numbers: set[str] = {
                n for b in _multi_batches.values()
                if b["batch_id"] != batch_id and b["status"] in ("waiting_agent", "ringing")
                for n in b["from_numbers"].values()
            }
        for lid in lead_ids:
            lead = db.fetch_lead(lid, DB_PATH)
            if not lead:
                with _batch_lock:
                    _mark_resolved(batch, lid)
                continue
            # Atomic pick + reserve: the daily counter is incremented the
            # moment the number is chosen, inside the lock, so a second
            # agent's batch running concurrently sees the slot as taken.
            with _pool_pick_lock:
                frm = caller_id_pool.pick_number(lead["phone"], DB_PATH, exclude=used_numbers)
                if frm:
                    db.increment_caller_calls(frm, DB_PATH)
            if not frm:
                # HARD LIMIT: the pool is at its daily caps. NEVER overflow
                # onto the established texting numbers for cold multi-dial —
                # that's exactly how they'd get spam-flagged. This lead waits;
                # release its claim so it's dialable when capacity returns.
                log.warning(f"Multi [{batch_id}] pool at daily cap — lead {lid} NOT dialed")
                with _batch_lock:
                    _mark_resolved(batch, lid)
                    _lead_claims.pop(lid, None)
                continue
            used_numbers.add(frm)
            assignments.append((lid, lead, frm))

        def _one_call(a):
            lid, lead, frm = a
            phone = to_e164(lead["phone"])
            try:
                call = twilio.calls.create(
                    to=phone,
                    from_=frm,
                    url=f"{PUBLIC_BASE_URL}/voice/multi-answer/{batch_id}/{lid}",
                    status_callback=f"{PUBLIC_BASE_URL}/voice/multi-status/{batch_id}/{lid}",
                    # "completed" is the only valid FINAL event here; busy/
                    # no-answer/failed arrive as the CallStatus on that event.
                    # (The old list logged a 21626 warning on every call.)
                    status_callback_event=["completed"],
                    timeout=25,
                )
                with _batch_lock:
                    batch["call_sids"][lid] = call.sid
                    batch["from_numbers"][lid] = frm
                # (daily counter already reserved at pick time, under the lock)
                log.info(f"Multi-dial [{batch_id}] → {lead['name']} ({phone}) from {frm} SID={call.sid}")
                try:
                    caller_id_pool.record_placed(frm, call.sid, agent, DB_PATH)
                except Exception:
                    # A statistics failure must never mark an actual live call
                    # as failed/resolved. The log retains its SID for recovery.
                    log.exception(f"Call history write failed for {call.sid}")
            except Exception as exc:
                log.warning(f"Multi-dial [{batch_id}] call to {lid} failed: {exc}")
                with _batch_lock:
                    _mark_resolved(batch, lid)

        with ThreadPoolExecutor(max_workers=10) as pool:
            pool.map(_one_call, assignments)

        _reconcile_batch(batch_id, twilio)

    threading.Thread(target=_fire_calls, daemon=True).start()

    # Tell THIS agent's softphone to join the conference (it POSTs agent-ready
    # once it's in the room, which releases the gated calls above).
    _dialer_current[agent] = {
        "mode": "multi",
        "batch_id": batch_id,
        "conference": conference,
        "lines": len(lead_ids),
        "ts": datetime.utcnow().isoformat(),
    }
    return batch_id


@app.route("/dialer/multi-start", methods=["POST"])
def dialer_multi_start():
    """Fire N simultaneous outbound calls (one-shot batch, no queue)."""
    data = request.get_json(force=True) or {}
    lead_ids = data.get("lead_ids", [])
    if not lead_ids:
        return jsonify({"error": "No leads"}), 400
    agent = (data.get("agent") or "rom").lower()
    batch_id = _start_multi_batch(agent, lead_ids, bool(data.get("amd", True)))
    resp = jsonify({"batch_id": batch_id, "lines": len(lead_ids)})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/dialer/agent-ready/<batch_id>", methods=["POST", "OPTIONS"])
def dialer_agent_ready(batch_id: str):
    """The softphone calls this once it has joined the conference. Releases the
    gated multi-dial so the outbound calls fire into a room the agent is in."""
    if request.method == "OPTIONS":
        resp = Response("")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    # Exactly ONE softphone may own a batch: the first to report ready wins.
    # A second popup (e.g. two browsers logged in as the same agent) gets
    # owner=false and backs out instead of double-joining the conference.
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token") or ""
    owner = False
    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        if batch:
            if not batch.get("agent_ready"):
                batch["agent_ready"] = True
                batch["agent_token"] = token
                log.info(f"Multi [{batch_id}] softphone joined — releasing calls")
            owner = (batch.get("agent_token") == token)
    resp = jsonify({"ok": True, "owner": owner})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


_HANGUP_TWIML = '<?xml version="1.0"?><Response><Hangup/></Response>'


def _conference_twiml(conference: str, caller_id: str, stop_stream: bool = False) -> str:
    """Winner TwiML: join the conference the softphone is waiting in.
    stop_stream ends the AMD media stream first (AMD path only)."""
    stop = '<Stop><Stream name="amd"/></Stop>' if stop_stream else ''
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>' + stop +
        f'<Dial callerId="{caller_id}"'
        ' record="record-from-ringing-dual"'
        f' recordingStatusCallback="{PUBLIC_BASE_URL}/voice/recording"'
        ' recordingStatusCallbackMethod="POST">'
        f'<Conference startConferenceOnEnter="true" endConferenceOnExit="true"'
        f' waitUrl="" beep="false">{conference}</Conference>'
        '</Dial></Response>'
    )


def _finalize_winner(batch_id: str, batch: dict, lead_id: int, call_sid: str | None) -> None:
    """Side effects shared by every winner path: interaction log, recording
    map, answer-rate credit, and killing the other ringing lines."""
    lead = db.fetch_lead(lead_id, DB_PATH)
    if lead:
        db.add_interaction(
            lead_id, "call",
            f"Outbound call (multi-line dialer) to {lead['name']}",
            "outbound", DB_PATH, ts=datetime.utcnow(),
        )
    if call_sid:
        _call_sid_to_lead[call_sid] = lead_id
    winner_from = batch["from_numbers"].get(lead_id)
    if winner_from:
        caller_id_pool.record_connected(winner_from)

    def _kill_others():
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(ACCOUNT_SID, AUTH_TOKEN)
        with _batch_lock:
            sids = {lid: sid for lid, sid in batch["call_sids"].items() if lid != lead_id}
        for lid, sid in sids.items():
            try:
                twilio.calls(sid).update(status="completed")
                log.info(f"Multi [{batch_id}] killed call to lead {lid} (SID={sid})")
            except Exception:
                pass
            with _batch_lock:
                batch["dropped"].append(lid)
        # The killed lines never got their shot (they were still ringing when
        # the winner connected — which may have been a voicemail). Put them
        # back at the FRONT of the agent's queue so they're redialed within
        # minutes instead of falling through until tomorrow.
        with _batch_lock:
            requeue = [l for l in batch["dropped"] if l not in batch["machines"]]
        _requeue_leads(batch.get("agent", ""), requeue)

    threading.Thread(target=_kill_others, daemon=True).start()
    log.info(f"Multi [{batch_id}] WINNER: lead {lead_id} ({batch['winner_name']})")


@app.route("/voice/multi-answer/<batch_id>/<int:lead_id>", methods=["POST"])
def voice_multi_answer(batch_id: str, lead_id: int):
    """A lead's line was answered — first answer wins the conference
    IMMEDIATELY (instant bridge: the agent hears the real first hello, no
    screening delay).

    AMD on: the call's audio ALSO streams to the AMD server in parallel; if
    a voicemail greeting emerges in the first seconds, /voice/amd-result
    kills the live call, auto-disposes it, and the queue moves on. A human
    verdict is a no-op — the agent is already talking. Fails open by
    construction: AMD can never delay or drop a human."""
    call_sid = request.form.get("CallSid", "")
    lead = db.fetch_lead(lead_id, DB_PATH)
    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        if not batch:
            return Response(_HANGUP_TWIML, mimetype="text/xml")
        if call_sid:
            batch["call_sids"].setdefault(lead_id, call_sid)
        if batch["winner"] is not None:
            log.info(f"Multi [{batch_id}] lead {lead_id} answered but {batch['winner']} already won")
            return Response(_HANGUP_TWIML, mimetype="text/xml")
        amd_on = batch.get("amd", False)
        batch["winner"] = lead_id
        batch["status"] = "connected"
        batch["winner_name"] = lead["name"] if lead else ""
        batch["winner_phone"] = lead["phone"] if lead else ""
        conference = batch["conference"]

    _finalize_winner(batch_id, batch, lead_id, call_sid)
    caller_id = pick_from_number(batch["winner_phone"])
    twiml = _conference_twiml(conference, caller_id)
    # AMD auto-kill is RETIRED (2026-07-17). Even beep-only detection false-
    # fired on a live human voice ("well you got the wrong—" → killed mid-
    # sentence; 4 real people hung up on across two days). Voicemail skipping
    # is manual now: the agent hears the greeting and taps 📴 Voicemail, which
    # auto-advances. No audio fork, no classifier in the call path.
    return Response(twiml, mimetype="text/xml")


def _handle_machine(batch_id: str, batch: dict, lead_id: int, sid: str | None) -> None:
    """AMD said voicemail: hang the line up, auto-dispose the lead (+1 day),
    and log a KPI row with source='amd'."""
    with _batch_lock:
        if lead_id not in batch["machines"]:
            batch["machines"].append(lead_id)
    if sid:
        try:
            from twilio.rest import Client as TwilioClient
            TwilioClient(ACCOUNT_SID, AUTH_TOKEN).calls(sid).update(status="completed")
        except Exception:
            pass
    lead = db.fetch_lead(lead_id, DB_PATH)
    if lead:
        db.add_interaction(lead_id, "call", "Call - voicemail detected (auto)",
                           "outbound", DB_PATH, ts=datetime.utcnow())
        fields = {
            "last_contact_date": date.today().isoformat(),
            "next_action_date": (date.today() + timedelta(days=1)).isoformat(),
        }
        if lead.get("conversation_stage") == "not_contacted":
            fields["conversation_stage"] = "initial_contact"
        db.update_lead(lead_id, fields, DB_PATH)
        db.log_disposition(lead_id, "Voicemail (auto)", source="amd",
                           batch_id=batch_id,
                           from_number=batch["from_numbers"].get(lead_id),
                           to_number=lead.get("phone"))
    log.info(f"AMD [{batch_id}] machine on lead {lead_id} — hung up + auto-disposed")


def _declare_winner_via_rest(batch_id: str, batch: dict, lead_id: int, sid: str | None) -> None:
    """AMD said human (or unknown): first one wins; redirect that live call
    into the conference. Late humans are hung up."""
    lead = db.fetch_lead(lead_id, DB_PATH)
    with _batch_lock:
        if batch["winner"] is not None:
            late = batch["winner"] != lead_id
        else:
            late = False
            batch["winner"] = lead_id
            batch["status"] = "connected"
            batch["winner_name"] = lead["name"] if lead else ""
            batch["winner_phone"] = lead["phone"] if lead else ""
        conference = batch["conference"]
    from twilio.rest import Client as TwilioClient
    twilio = TwilioClient(ACCOUNT_SID, AUTH_TOKEN)
    if late:
        if sid:
            try:
                twilio.calls(sid).update(status="completed")
            except Exception:
                pass
        return
    caller_id = pick_from_number(batch["winner_phone"])
    if sid:
        try:
            twilio.calls(sid).update(
                twiml=_conference_twiml(conference, caller_id, stop_stream=True))
        except Exception as exc:
            log.warning(f"AMD [{batch_id}] failed to redirect winner {lead_id}: {exc}")
    _finalize_winner(batch_id, batch, lead_id, sid)


def _kill_machine_winner(batch_id: str, batch: dict, lead_id: int, sid: str | None) -> None:
    """Instant-bridge mode: the connected 'winner' turned out to be a
    voicemail. Hang it up mid-greeting (ending the conference — the agent's
    leg drops with it), auto-dispose it, and flip the batch to machine_ended
    so the softphone auto-advances instead of asking for a disposition.
    Status flips BEFORE the hangup so the popup's disconnect handler always
    sees machine_ended when it checks the batch."""
    with _batch_lock:
        if batch.get("winner") == lead_id and batch["status"] == "connected":
            batch["status"] = "machine_ended"
    _handle_machine(batch_id, batch, lead_id, sid)   # hangup + "Voicemail (auto)" dispo
    log.info(f"AMD [{batch_id}] live winner {lead_id} was voicemail — killed, batch machine_ended")


@app.route("/voice/amd-result/<batch_id>/<int:lead_id>", methods=["POST"])
def voice_amd_result(batch_id: str, lead_id: int):
    """Verdict from the AMD server (instant-bridge mode): a machine verdict
    kills the call — mid-conversation for the connected winner, silently for
    a non-winner line. Human/unknown verdicts are no-ops: the agent is
    already live with them, so there is nothing to connect or delay."""
    data = request.get_json(force=True, silent=True) or {}
    result = data.get("result", "unknown")
    detail = data.get("detail", "")
    log.info(f"AMD [{batch_id}] lead {lead_id}: {result} ({detail})")
    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        if not batch:
            return jsonify({"ok": False, "error": "batch not found"}), 404
        sid = batch["call_sids"].get(lead_id)
        already_winner = batch["winner"] == lead_id
    if result == "machine":
        # AUTO-KILL RETIRED (2026-07-17): speech-pattern verdicts hung up on
        # real people (2026-07-09), then even a beep verdict false-fired on a
        # live voice mid-sentence. Machine verdicts are now LOG-ONLY — nothing
        # is ever hung up automatically. VM skipping is the agent's 📴 tap.
        log.warning(f"AMD [{batch_id}] machine verdict ({detail}) for lead "
                    f"{lead_id} — LOG ONLY, auto-kill retired")
    return jsonify({"ok": True})


@app.route("/voice/amd-timeout/<batch_id>/<int:lead_id>", methods=["POST", "GET"])
def voice_amd_timeout(batch_id: str, lead_id: int):
    """TwiML fallback after the AMD hold <Pause>: no verdict arrived (AMD
    server down/slow), so FAIL OPEN — connect this call like AMD never ran."""
    call_sid = request.form.get("CallSid", "")
    lead = db.fetch_lead(lead_id, DB_PATH)
    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        if not batch:
            return Response(_HANGUP_TWIML, mimetype="text/xml")
        if batch["winner"] == lead_id:
            # amd-result already declared us winner; its REST redirect lost the
            # race with this Redirect — return the same conference TwiML.
            conference = batch["conference"]
            phone = batch["winner_phone"]
            return Response(_conference_twiml(conference, pick_from_number(phone),
                                              stop_stream=True), mimetype="text/xml")
        if batch["winner"] is not None:
            return Response(_HANGUP_TWIML, mimetype="text/xml")
        batch["winner"] = lead_id
        batch["status"] = "connected"
        batch["winner_name"] = lead["name"] if lead else ""
        batch["winner_phone"] = lead["phone"] if lead else ""
        conference = batch["conference"]
    log.info(f"AMD [{batch_id}] no verdict for lead {lead_id} — failing open to connect")
    _finalize_winner(batch_id, batch, lead_id, call_sid)
    return Response(_conference_twiml(conference, pick_from_number(batch["winner_phone"]),
                                      stop_stream=True), mimetype="text/xml")


@app.route("/voice/multi-status/<batch_id>/<int:lead_id>", methods=["POST"])
def voice_multi_status(batch_id: str, lead_id: int):
    """Status callback for each outbound call in a multi-line batch."""
    call_status = request.form.get("CallStatus", "")
    log.info(f"Multi [{batch_id}] lead {lead_id} status: {call_status}")

    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        if batch:
            _mark_resolved(batch, lead_id)
            if batch["status"] == "no_answer":
                log.info(f"Multi [{batch_id}] all lines resolved — no one answered")
    return ("", 204)


@app.route("/dialer/batch/<batch_id>")
def dialer_batch(batch_id: str):
    """Get the current state of a multi-line batch (polled by the softphone)."""
    with _batch_lock:
        batch = _multi_batches.get(batch_id)
    if not batch:
        return jsonify({"error": "Batch not found"}), 404
    resp = jsonify({
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "winner": batch["winner"],
        "winner_name": batch["winner_name"],
        "winner_phone": batch["winner_phone"],
        "total": batch["total"],
        "resolved_count": batch["resolved_count"],
        "dropped": batch["dropped"],
        "machines_count": len(batch.get("machines", [])),
        "amd": batch.get("amd", False),
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/quick-dial-phone", methods=["POST", "OPTIONS"])
def quick_dial_phone():
    """Phone-bridge quick dial: ring the user's cell, then connect to a raw
    number. Called by the Messages Quick Dial widget's 'My phone' button."""
    if request.method == "OPTIONS":
        resp = Response("")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    data = request.get_json(force=True, silent=True) or {}
    to = data.get("to", "")
    from_number = data.get("from", "")
    result = place_call_number(to, from_number=from_number)
    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# label -> (interaction note, days until next call or None). Mirrors the
# dashboard dialer so disposing in the popup behaves identically.
_DISPOSITIONS = {
    "No Answer":      ("Call - no answer", 1),
    "Voicemail":      ("Call - left voicemail", 3),
    "Wrong Number":   ("Call - wrong number", None),
    "Not Interested": ("Call - not interested", None),
    "Callback":       ("Call - wants callback", 1),
    "Connected":      ("Call - connected, talked", 3),
    "Investor":       ("Call - investor buyer", None),
    # Answered but no audio either way (silent screening app, dead line,
    # or an audio-path glitch). Retry the next day.
    "Dead Air":       ("Call - answered but dead air (no audio)", 1),
    # Reached the right person but they sold the property. Terminal — never
    # auto re-queued. (Status stays as-is; mark the lead dead on its page.)
    "No Longer Owns": ("Call - no longer owns the property", None),
    # Live person answered, heard the agent, hung up (screen-out). The number
    # is good and they answer — retry in 2 days, ideally same caller ID.
    "Hung Up":        ("Call - answered, hung up quickly", 2),
}


@app.route("/dialer/dispose", methods=["POST", "OPTIONS"])
def dialer_dispose():
    """Log a call disposition from the softphone popup and update the lead in
    crm.db immediately (interaction + next-call date)."""
    if request.method == "OPTIONS":
        resp = Response("")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    data = request.get_json(force=True, silent=True) or {}
    lead_id = data.get("lead_id")
    label = data.get("disposition", "")
    if not lead_id or label not in _DISPOSITIONS:
        resp = jsonify({"ok": False, "error": "bad lead_id or disposition"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 400

    note, days = _DISPOSITIONS[label]
    lead = db.fetch_lead(int(lead_id), DB_PATH)
    if not lead:
        resp = jsonify({"ok": False, "error": "lead not found"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 404

    db.add_interaction(int(lead_id), "call", note, "outbound", DB_PATH, ts=datetime.utcnow())
    fields = {"last_contact_date": date.today().isoformat()}
    if label == "Investor":
        fields["status"] = "investor"
    if lead.get("conversation_stage") == "not_contacted":
        fields["conversation_stage"] = "initial_contact"
    if days is not None:
        fields["next_action_date"] = (date.today() + timedelta(days=days)).isoformat()
    db.update_lead(int(lead_id), fields, DB_PATH)

    # KPI row. Enrich caller-ID/number from the batch when this lead won one.
    frm = data.get("from_number")
    to = data.get("to_number") or lead.get("phone")
    b_id = data.get("batch_id")
    if b_id:
        with _batch_lock:
            b = _multi_batches.get(b_id)
            if b and b.get("winner") == int(lead_id):
                frm = b["from_numbers"].get(int(lead_id)) or frm
                to = b.get("winner_phone") or to
    db.log_disposition(int(lead_id), label, source="popup",
                       status_set="investor" if label == "Investor" else None,
                       agent=data.get("agent"), batch_id=b_id,
                       from_number=frm, to_number=to,
                       duration_sec=data.get("duration_sec"), db_path=DB_PATH)
    # Disposed = done for today; the claim is no longer needed.
    with _batch_lock:
        _lead_claims.pop(int(lead_id), None)
    log.info(f"Disposed lead {lead_id} ({lead['name']}): {label}")

    resp = jsonify({"ok": True, "lead_id": lead_id, "disposition": label})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/dialer/lead/<int:lead_id>")
def dialer_lead(lead_id: int):
    """Lead details for the softphone script panel (name + property address)."""
    lead = db.fetch_lead(lead_id, DB_PATH)
    if not lead:
        resp = jsonify({}); resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 404
    tl = lead.get("timeline") or ""
    address = ""
    if "Address:" in tl:
        after = tl.split("Address:", 1)[1]
        address = after.split(". Situation")[0].split(". Notes")[0].strip()
    name = (lead.get("name") or "").strip()
    resp = jsonify({
        "name": name,
        "first_name": name.split()[0] if name else "",
        "phone": lead.get("phone", ""),
        "address": address,
        # Prior dialing attempts (disposed calls) — the softphone shows
        # "Attempt #N" plus VM-cadence advice from this.
        "attempts": db.count_dispositions(lead_id, DB_PATH),
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/dialer/status", methods=["POST", "OPTIONS"])
def dialer_set_status():
    """Manually set a lead's status (cold/warm/hot) from the softphone popup.
    This is a direct status-field update (the dashboard does the same) — it does
    NOT touch the engine's escalation logic. Logs a note for the audit trail."""
    if request.method == "OPTIONS":
        resp = Response("")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    data = request.get_json(force=True, silent=True) or {}
    lead_id = data.get("lead_id")
    status = (data.get("status") or "").lower()
    if not lead_id or status not in ("cold", "warm", "hot"):
        resp = jsonify({"ok": False, "error": "bad lead_id or status"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 400

    lead = db.fetch_lead(int(lead_id), DB_PATH)
    if not lead:
        resp = jsonify({"ok": False, "error": "lead not found"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 404

    db.set_manual_status(int(lead_id), status, DB_PATH, contacted_on=date.today())
    db.add_interaction(int(lead_id), "note", f"Marked {status.upper()} from dialer",
                       "outbound", DB_PATH, ts=datetime.utcnow())
    # KPI: attach the temperature to this call's disposition row.
    db.attach_status_to_disposition(int(lead_id), status, DB_PATH)
    log.info(f"Set lead {lead_id} ({lead['name']}) status -> {status}")

    resp = jsonify({"ok": True, "lead_id": lead_id, "status": status})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/dialer/note", methods=["POST", "OPTIONS"])
def dialer_note():
    """Save a free-text note typed on the softphone during a call."""
    if request.method == "OPTIONS":
        return _cors(Response(""))
    data = request.get_json(force=True, silent=True) or {}
    lead_id = data.get("lead_id")
    note = (data.get("note") or "").strip()
    if not lead_id or not note:
        return _cors(jsonify({"ok": False, "error": "missing lead_id or note"})), 400
    if not db.fetch_lead(int(lead_id), DB_PATH):
        return _cors(jsonify({"ok": False, "error": "lead not found"})), 404
    who = (data.get("agent") or "").strip()
    prefix = f"📞 {who}: " if who else "📞 "
    db.add_interaction(int(lead_id), "note", prefix + note, "outbound",
                       DB_PATH, ts=datetime.utcnow())
    log.info(f"Saved call note on lead {lead_id} ({len(note)} chars)")
    return _cors(jsonify({"ok": True}))


@app.route("/dialer/vm-drop-audio", methods=["POST", "OPTIONS"])
def dialer_vm_drop_audio():
    """Save the agent's recorded VM-drop message (WAV from the softphone
    recorder). Stored per agent; its existence is what unlocks the 📼 button."""
    if request.method == "OPTIONS":
        return _cors(Response(""))
    agent = re.sub(r"[^a-z0-9]", "", (request.args.get("agent") or "rom").lower()) or "rom"
    data = request.get_data()
    if not data or len(data) < 1000:
        return _cors(jsonify({"ok": False, "error": "no audio received"})), 400
    if len(data) > 8_000_000:
        return _cors(jsonify({"ok": False, "error": "recording too large"})), 400
    path = PROJECT_ROOT / "static" / f"vm_drop_{agent}.wav"
    path.write_bytes(data)
    log.info(f"Saved VM drop message for {agent} ({len(data)} bytes)")
    return _cors(jsonify({"ok": True}))


@app.route("/dialer/vm-drop", methods=["POST", "OPTIONS"])
def dialer_vm_drop():
    """AGENT-triggered voicemail drop (no AMD involved — the agent's ears are
    the detector). Redirects the connected lead leg out of the conference to
    play the recorded message into the voicemail and hang up, logs the
    disposition, and frees the agent to dial on immediately."""
    if request.method == "OPTIONS":
        return _cors(Response(""))
    data = request.get_json(force=True, silent=True) or {}
    agent = re.sub(r"[^a-z0-9]", "", (data.get("agent") or "rom").lower()) or "rom"
    batch_id = data.get("batch_id") or ""
    try:
        lead_id = int(data.get("lead_id"))
    except (TypeError, ValueError):
        return _cors(jsonify({"ok": False, "error": "bad lead_id"})), 400

    audio = PROJECT_ROOT / "static" / f"vm_drop_{agent}.wav"
    if not audio.exists():
        return _cors(jsonify({"ok": False, "error": "no VM drop message recorded"})), 400
    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        sid = batch["call_sids"].get(lead_id) if batch else None
    if not sid:
        return _cors(jsonify({"ok": False, "error": "call not found for this batch"})), 404

    # Redirect the LEAD leg out of the conference: short pause (beep buffer),
    # play the message into the voicemail, hang up. The leg is independent of
    # the conference after this, so the agent leaving can't cut it off.
    try:
        from twilio.rest import Client as TwilioClient
        TwilioClient(ACCOUNT_SID, AUTH_TOKEN).calls(sid).update(
            twiml=(f'<Response><Pause length="1"/>'
                   f'<Play>{PUBLIC_BASE_URL}/static/vm_drop_{agent}.wav</Play>'
                   f'<Hangup/></Response>')
        )
    except Exception as exc:
        log.warning(f"VM drop redirect failed for lead {lead_id}: {exc}")
        return _cors(jsonify({"ok": False, "error": f"redirect failed: {exc}"})), 502

    lead = db.fetch_lead(lead_id, DB_PATH)
    if lead:
        db.add_interaction(lead_id, "call", "Call - voicemail, drop message played",
                           "outbound", DB_PATH, ts=datetime.utcnow())
        fields = {"last_contact_date": date.today().isoformat(),
                  "next_action_date": (date.today() + timedelta(days=3)).isoformat()}
        if lead.get("conversation_stage") == "not_contacted":
            fields["conversation_stage"] = "initial_contact"
        db.update_lead(lead_id, fields, DB_PATH)
        db.log_disposition(lead_id, "Voicemail (drop)", source="popup", agent=agent,
                           batch_id=batch_id, to_number=lead.get("phone"))
    with _batch_lock:
        _lead_claims.pop(lead_id, None)
    log.info(f"VM drop playing for lead {lead_id} (agent {agent})")
    return _cors(jsonify({"ok": True}))


@app.route("/dialer/current", methods=["GET", "POST"])
def dialer_current():
    """Per-agent dial channel between Streamlit and each softphone popup.
    POST: Streamlit writes {agent, lead_id, caller_id, lead_name, lead_phone, ts}.
    GET:  A popup polls ?agent=<name> to detect its own new leads to dial."""
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        agent = (data.get("agent") or "rom").lower()
        _dialer_current[agent] = data
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    agent = (request.args.get("agent") or "rom").lower()
    resp = jsonify(_dialer_current.get(agent, {}))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/dialer/claim", methods=["POST", "OPTIONS"])
def dialer_claim():
    """Check out the next dialable leads for an agent (the pro-dialer split).

    Body: {agent, candidates: [lead_id in queue order], count}
    Returns {to_dial: [...], skipped: [...]} where skipped leads are either
    claimed by the other agent right now or already dialed today by anyone —
    the dashboard drops those from its local queue, so two agents working the
    same list divide it automatically without ever double-dialing."""
    if request.method == "OPTIONS":
        resp = Response("")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    data = request.get_json(force=True, silent=True) or {}
    agent = (data.get("agent") or "rom").lower()
    try:
        candidates = [int(x) for x in data.get("candidates", [])]
        count = max(1, int(data.get("count", 1)))
    except (TypeError, ValueError):
        return jsonify({"error": "bad payload"}), 400

    to_dial, skipped = _claim_leads(agent, candidates, count)
    if skipped:
        log.info(f"Claim [{agent}]: dialing {to_dial}, skipped {len(skipped)} "
                 f"(other agent / already dialed today)")
    resp = jsonify({"to_dial": to_dial, "skipped": skipped})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def _claim_leads(agent: str, candidates: list[int], count: int) -> tuple[list[int], list[int]]:
    """Core claim logic shared by /dialer/claim and the server-side queue.
    Returns (to_dial, skipped): skipped leads are claimed by the other agent
    or already dialed today by anyone."""
    dialed_today = db.leads_dialed_today(DB_PATH)
    to_dial: list[int] = []
    skipped: list[int] = []
    now = time.time()
    with _batch_lock:
        _prune_claims()
        for lid in candidates:
            if len(to_dial) >= count:
                break
            claim = _lead_claims.get(lid)
            if lid in dialed_today or (claim and claim["agent"] != agent):
                skipped.append(lid)
                continue
            to_dial.append(lid)
        for lid in to_dial:
            _lead_claims[lid] = {"agent": agent, "ts": now}
    return to_dial, skipped


# ── Server-side dial queue (the softphone-cockpit flow) ─────────────────────
# The dashboard loads a whole list here ONCE; from then on the softphone popup
# drives itself: dispose → /dialer/queue/next → next batch fires → repeat.
# The agent never has to switch back to the CRM between calls.
# Persisted to disk on every change so a webhook restart mid-session can't
# eat the remaining list (batches/claims are transient and rebuild fine).
_dial_queues: dict[str, dict] = {}   # agent -> {pending, total, lines, amd}
_QUEUE_STATE = PROJECT_ROOT / "dial_queue_state.json"


def _save_queues() -> None:
    try:
        _QUEUE_STATE.write_text(json.dumps(_dial_queues))
    except Exception:
        pass


def _load_queues() -> None:
    try:
        if _QUEUE_STATE.exists():
            data = json.loads(_QUEUE_STATE.read_text())
            for agent, q in data.items():
                if q.get("pending"):
                    _dial_queues[agent] = q
            if _dial_queues:
                log.info("Restored dial queues after restart: "
                         + ", ".join(f"{a}={len(q['pending'])} left"
                                     for a, q in _dial_queues.items()))
    except Exception as exc:
        log.warning(f"Could not restore dial queues: {exc}")


_load_queues()


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def _agent_batch_busy(agent: str) -> bool:
    """True while this agent has a batch still waiting/ringing (pre-answer)."""
    with _batch_lock:
        return any(
            b.get("agent") == agent and b["status"] in ("waiting_agent", "ringing")
            for b in _multi_batches.values()
        )


def _requeue_leads(agent: str, lead_ids: list[int]) -> None:
    """Return un-dialed batch casualties (lines killed while still ringing)
    to the FRONT of the agent's queue and release their claims, so a batch
    'won' by a voicemail doesn't cost the other nine leads their shot today.
    Capped at 2 requeues per lead per queue so nobody's phone rings all
    morning; leads past the cap simply wait for tomorrow's list."""
    with _batch_lock:
        for lid in lead_ids:
            _lead_claims.pop(lid, None)
    q = _dial_queues.get(agent)
    if not q or not lead_ids:
        return
    counts = q.setdefault("requeued", {})
    back = []
    for lid in lead_ids:
        # str keys: survives the JSON round-trip of queue persistence
        if counts.get(str(lid), 0) < 2 and lid not in q["pending"]:
            counts[str(lid)] = counts.get(str(lid), 0) + 1
            back.append(lid)
    if back:
        q["pending"] = back + q["pending"]
        log.info(f"Queue [{agent}]: requeued {len(back)} ringing casualties {back}")
    _save_queues()


def _queue_next(agent: str) -> dict:
    """Claim the next batch off the agent's queue and fire it."""
    q = _dial_queues.get(agent)
    if not q or not q["pending"]:
        _dial_queues.pop(agent, None)
        return {"done": True, "remaining": 0}
    if _agent_batch_busy(agent):
        return {"error": "busy", "remaining": len(q["pending"])}
    # HARD daily limit: batches only dial what the caller ID pool has left
    # today (limits shared by Rom and Gabby; per-number limits ramp with usage). At zero the queue pauses
    # until tomorrow — cold calls never overflow onto the texting numbers.
    capacity = caller_id_pool.pool_status(DB_PATH)["remaining_today"]
    if capacity <= 0:
        log.info(f"Queue [{agent}]: pool at daily cap — queue paused "
                 f"({len(q['pending'])} leads remain)")
        return {"error": "pool_exhausted",
                "message": "Daily dial limit reached — the number pool is at "
                           "its spam-safe cap. The queue resumes tomorrow.",
                "remaining": len(q["pending"])}
    while q["pending"]:
        chunk = q["pending"][:100]
        to_dial, skipped = _claim_leads(agent, chunk, min(q["lines"], capacity))
        taken = set(to_dial) | set(skipped)
        q["pending"] = [l for l in q["pending"] if l not in taken]
        if to_dial:
            batch_id = _start_multi_batch(agent, to_dial, q["amd"])
            log.info(f"Queue [{agent}]: batch {batch_id} × {len(to_dial)} lines, "
                     f"{len(q['pending'])} left in queue")
            return {"batch_id": batch_id, "lines": len(to_dial),
                    "remaining": len(q["pending"]), "total": q["total"]}
    _dial_queues.pop(agent, None)
    return {"done": True, "remaining": 0}


@app.route("/dialer/queue/start", methods=["POST", "OPTIONS"])
def dialer_queue_start():
    """Load a dial list into the server queue and fire the first batch.
    Body: {agent, lead_ids: [...], lines, amd}."""
    if request.method == "OPTIONS":
        return _cors(Response(""))
    data = request.get_json(force=True, silent=True) or {}
    agent = (data.get("agent") or "rom").lower()
    try:
        lead_ids = [int(x) for x in data.get("lead_ids", [])]
        lines = min(10, max(1, int(data.get("lines", 3))))
    except (TypeError, ValueError):
        return _cors(jsonify({"error": "bad payload"})), 400
    if not lead_ids:
        return _cors(jsonify({"error": "no leads"})), 400
    if _agent_batch_busy(agent):
        return _cors(jsonify({"error": "a batch is already ringing — finish or end it first"})), 409
    _dial_queues[agent] = {"pending": lead_ids, "total": len(lead_ids),
                           "lines": lines, "amd": bool(data.get("amd", True))}
    result = _queue_next(agent)
    _save_queues()
    log.info(f"Queue [{agent}]: loaded {len(lead_ids)} leads × {lines} lines")
    return _cors(jsonify(result))


@app.route("/dialer/queue/next", methods=["POST", "OPTIONS"])
def dialer_queue_next():
    """Softphone calls this after each disposition to dial the next batch."""
    if request.method == "OPTIONS":
        return _cors(Response(""))
    data = request.get_json(force=True, silent=True) or {}
    agent = (data.get("agent") or "rom").lower()
    result = _queue_next(agent)
    _save_queues()
    return _cors(jsonify(result))


@app.route("/dialer/queue/status")
def dialer_queue_status():
    agent = (request.args.get("agent") or "rom").lower()
    q = _dial_queues.get(agent)
    if not q:
        return _cors(jsonify({"active": False}))
    return _cors(jsonify({"active": True, "remaining": len(q["pending"]),
                          "total": q["total"], "lines": q["lines"],
                          "amd": q["amd"]}))


@app.route("/dialer/ai-notes-status")
def dialer_ai_notes_status():
    """Running count for the softphone's AI call notes bar."""
    from crm_engine import ai_budget
    return _cors(jsonify(ai_budget.autonotes_status()))


@app.route("/dialer/pool-status")
def dialer_pool_status():
    """Caller ID pool health for the softphone bar: capacity used/left today,
    shared warm-up progress. remaining_today == 0 means dialing is done for the day."""
    return _cors(jsonify(caller_id_pool.pool_status(DB_PATH)))


@app.route("/dialer/queue/stop", methods=["POST", "OPTIONS"])
def dialer_queue_stop():
    if request.method == "OPTIONS":
        return _cors(Response(""))
    data = request.get_json(force=True, silent=True) or {}
    agent = (data.get("agent") or "rom").lower()
    _dial_queues.pop(agent, None)
    _save_queues()
    log.info(f"Queue [{agent}]: stopped")
    return _cors(jsonify({"ok": True}))


@app.route("/softphone")
def softphone_page():
    """Persistent browser softphone with multi-line support."""
    html = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CRM Softphone</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#fafbfa; padding:16px; min-width:320px; }
  h3 { font-size:15px; color:#888; margin-bottom:12px; text-align:center; }
  .status { text-align:center; padding:10px; font-size:14px; color:#555;
            border-radius:8px; margin-bottom:10px; background:#f0f0f0; }
  .status.connecting { background:#fff3cd; color:#856404; }
  .status.ringing    { background:#fff3cd; color:#856404; }
  .status.connected  { background:#d4edda; color:#155724; }
  .status.ended      { background:#f0f0f0; color:#555; }
  .status.error      { background:#f8d7da; color:#721c24; }
  .status.ready      { background:#e3f2fd; color:#0d47a1; }
  .status.no-answer  { background:#fff3cd; color:#856404; }
  .status.ringing    { animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:.7;} }
  .lead-info { text-align:center; margin-bottom:8px; }
  .lead-name { font-size:18px; font-weight:700; color:#1a1a1a; }
  .lead-phone { font-size:14px; color:#666; }
  .attempt-line { font-size:12px; color:#6b6b6b; margin-top:3px; }
  .attempt-line b { color:#b8770a; }
  .timer { text-align:center; font-size:28px; font-weight:700; color:#1a1a1a;
           margin:8px 0; font-variant-numeric:tabular-nums; }
  .buttons { display:flex; gap:8px; justify-content:center; flex-wrap:wrap; margin-top:10px; }
  .btn { padding:10px 22px; border:none; border-radius:8px; font-size:14px;
         font-weight:600; cursor:pointer; transition:all 0.15s; }
  .btn:hover { transform:scale(1.03); }
  .btn:disabled { opacity:0.4; cursor:not-allowed; transform:none; }
  .btn-call   { background:#28a745; color:#fff; }
  .btn-hangup { background:#dc3545; color:#fff; }
  .btn-mute   { background:#6c757d; color:#fff; }
  .btn-mute.active { background:#ffc107; color:#000; }
  .disp { margin-top:12px; display:none; }
  .disp.show { display:block; }
  .disp-title { font-size:12px; color:#777; text-align:center; margin-bottom:6px;
                text-transform:uppercase; letter-spacing:.5px; }
  .disp-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; }
  .disp-grid.locked { opacity:.4; pointer-events:none; }
  .disp-btn { padding:9px 4px; border:1px solid #ccc; border-radius:7px;
              background:#fff; font-size:12px; font-weight:600; cursor:pointer;
              color:#333; transition:all .12s; }
  .disp-btn:hover { background:#f0f7f1; border-color:#6B8F71; }
  .st-hot  { border-color:#e74c3c; color:#c0392b; }
  .st-hot:hover  { background:#fdecea; }
  .st-warm { border-color:#e69500; color:#b8770a; }
  .st-warm:hover { background:#fff6e6; }
  .st-cold { border-color:#3498db; color:#2471a3; }
  .st-cold:hover { background:#eaf4fb; }
  .disp-done { text-align:center; font-size:13px; color:#155724; font-weight:600;
               background:#d4edda; padding:8px; border-radius:7px; margin-top:8px; display:none; }
  .disp-done.show { display:block; }
  .script { margin-top:12px; border:1px solid #e3e3e3; border-radius:8px;
            background:#fff; padding:8px 10px; }
  .script summary { cursor:pointer; font-weight:700; font-size:13px; color:#444;
                    list-style:none; }
  .script summary::-webkit-details-marker { display:none; }
  .rep-row { font-size:12px; color:#666; margin:8px 0 6px; }
  .rep-row input { border:1px solid #ccc; border-radius:5px; padding:3px 6px;
                   font-size:12px; width:90px; }
  .sc-block { margin-top:8px; }
  .sc-label { font-size:11px; text-transform:uppercase; letter-spacing:.5px;
              color:#8a8a8a; margin-bottom:3px; font-weight:700; }
  .sc-line { font-size:13px; color:#1a1a1a; line-height:1.4; padding:4px 6px;
             border-left:3px solid #6B8F71; background:#f6f9f6; border-radius:4px;
             margin-bottom:5px; }
  .sc-obj { font-size:12px; color:#333; line-height:1.35; padding:3px 0;
            border-bottom:1px dashed #eee; }
  .sc-obj b { color:#b8770a; }
  .log { margin-top:14px; font-size:12px; color:#999; max-height:80px;
         overflow-y:auto; border-top:1px solid #eee; padding-top:6px; }
  .log div { padding:1px 0; }
  .queuebar { display:none; align-items:center; justify-content:space-between;
              gap:6px; margin-bottom:10px; padding:8px 10px; border-radius:8px;
              background:#eef4ef; border:1px solid #cfe0d2; }
  .queuebar.show { display:flex; }
  .q-info { font-size:13px; font-weight:700; color:#2f4a35; white-space:nowrap; }
  .q-btns { display:flex; gap:5px; }
  .q-btn { padding:6px 9px; border:1px solid #b9ccbc; border-radius:6px;
           background:#fff; font-size:12px; font-weight:600; cursor:pointer;
           color:#2f4a35; }
  .q-btn:hover { background:#e4efe6; }
  .q-btn.q-end { border-color:#d9b3b3; color:#8c3b3b; }
  .q-btn.q-end:hover { background:#f7e9e9; }
  .poolbar { display:none; margin-bottom:10px; padding:6px 10px; border-radius:7px;
             font-size:12px; font-weight:600; color:#4a5a4e; background:#f2f5f2;
             border:1px solid #dde5de; text-align:center; }
  .poolbar.low  { background:#fff6e6; border-color:#ecd9ae; color:#8a6510; }
  .poolbar.warn { background:#fdecea; border-color:#e5b8b3; color:#a03225; }
</style>
</head><body>
<h3>📞 CRM Softphone</h3>
<div class="lead-info">
  <div class="lead-name" id="leadName">Waiting for lead...</div>
  <div class="lead-phone" id="leadPhone"></div>
  <div class="attempt-line" id="attemptLine"></div>
</div>
<div class="timer" id="timer">00:00</div>
<div class="status" id="status">Starting up...</div>
<div class="queuebar" id="queueBar">
  <span class="q-info" id="queueInfo"></span>
  <span class="q-btns">
    <button class="q-btn" id="btnQNext" title="Dial the next batch now">⏭ Next</button>
    <button class="q-btn" id="btnQAuto" title="Toggle auto-dial after each disposition">⏸ Auto: ON</button>
    <button class="q-btn q-end" id="btnQEnd" title="End the queue">⏹ End</button>
  </span>
</div>
<div class="poolbar" id="poolBar" title="Caller ID pool: today's call volume vs the spam-safe caps (limits shared by Rom and Gabby; per-number limits ramp with usage). Dialing hard-stops at the cap."></div>
<div class="poolbar" id="aiBar" title="AI call notes: turned on in the CRM Dialer before dialing. Each answered, transcribed call gets an AI note; this is the running count and cost since you turned it on."></div>
<div class="buttons">
  <button class="btn btn-call" id="btnCall" disabled>📞 Call</button>
  <button class="btn btn-mute" id="btnMute" disabled>🔇 Mute</button>
  <button class="btn btn-hangup" id="btnHangup" disabled>📕 Hang Up</button>
  <button class="btn btn-mute" id="btnSound" title="Speaker test — plays a ring tone through your browser. If you hear it, your audio output works; dead air on a call means the other side was silent.">🔊 Test</button>
  <button class="btn btn-call" id="btnDropVm" style="display:none" disabled
          title="Hear a voicemail greeting? Tap at the beep: your recorded message plays into the voicemail while you move on to the next batch.">📼 Drop VM</button>
</div>
<div class="disp" id="disp">
  <div class="disp-title">Call outcome — saves to CRM</div>
  <div class="disp-grid" id="dispOutcome">
    <button class="disp-btn" onclick="dispose('No Answer')">No Answer</button>
    <button class="disp-btn" onclick="dispose('Voicemail')">Voicemail</button>
    <button class="disp-btn" onclick="dispose('Connected')">Connected</button>
    <button class="disp-btn" onclick="dispose('Investor')">Investor</button>
    <button class="disp-btn" onclick="dispose('Callback')">Callback</button>
    <button class="disp-btn" onclick="dispose('Not Interested')">Not Interested</button>
    <button class="disp-btn" onclick="dispose('Wrong Number')">Wrong Number</button>
    <button class="disp-btn" onclick="dispose('Dead Air')">🔇 Dead Air</button>
    <button class="disp-btn" onclick="dispose('No Longer Owns')">🚫 No Longer Owns</button>
    <button class="disp-btn" onclick="dispose('Hung Up')">📴 Hung Up</button>
  </div>
  <div id="statusSection">
    <div class="disp-title" style="margin-top:10px">Set lead status</div>
    <div class="disp-grid disp-grid-3" id="dispStatus">
      <button class="disp-btn st-hot" onclick="setLeadStatus('hot')">🔥 Hot</button>
      <button class="disp-btn st-warm" onclick="setLeadStatus('warm')">🌤 Warm</button>
      <button class="disp-btn st-cold" onclick="setLeadStatus('cold')">❄️ Cold</button>
    </div>
  </div>
  <button class="btn btn-call" id="btnDispNext"
          style="width:100%;margin-top:8px">✓ Save & Next lead</button>
</div>
<div id="liveNotes" style="display:none;margin-top:12px">
  <div class="disp-title">Call notes — available during your call</div>
  <textarea id="dispNote" placeholder="Type a quick note about this call…"
            style="width:100%;box-sizing:border-box;min-height:52px;padding:7px 9px;
                   border:1px solid #ccc;border-radius:7px;font-size:13px;
                   font-family:inherit;resize:vertical"></textarea>
  <button class="btn btn-call" id="btnSaveNote" style="margin-top:8px">Save Note</button>
  <button class="btn btn-mute" id="btnInvestor" onclick="dispose('Investor')">Investor</button>
  <div id="noteSaveStatus" role="status" style="font-size:12px;margin-top:6px"></div>
</div>
<div class="disp-done" id="dispDone"></div>
<details class="script" id="scriptBox" open>
  <summary>📋 Script</summary>
  <div class="rep-row">Your name: <input id="repName" value="Rom" maxlength="20"></div>
  <div class="sc-block">
    <div class="sc-label">Open — pick one</div>
    <div class="sc-line" id="scOpen1"></div>
    <div class="sc-line" id="scOpen2"></div>
    <div class="sc-line" id="scOpen3"></div>
  </div>
  <div class="sc-block">
    <div class="sc-label">Once they confirm</div>
    <div class="sc-line" id="scConnect"></div>
  </div>
  <div class="sc-block">
    <div class="sc-label">Get them talking</div>
    <div class="sc-line">What's the condition like — anything need work?</div>
    <div class="sc-line">If the price and terms were right, is it something you'd sell?</div>
    <div class="sc-line">Do you have a number in mind, or want me to come back with one?</div>
  </div>
  <div class="sc-block">
    <div class="sc-label">If they push back</div>
    <div class="sc-obj"><b>Not interested:</b> Totally fair — mind if I ask, is it just not the right time, or not something you'd ever sell?</div>
    <div class="sc-obj"><b>What's your offer:</b> Depends on the condition — can I ask you a couple quick things about the place first?</div>
    <div class="sc-obj"><b>How'd you get my #:</b> Property records are public with the county. I focus on this area and yours came up — no pressure at all.</div>
    <div class="sc-obj"><b>I'll think about it:</b> Of course — what's the main thing you'd want to figure out before deciding?</div>
  </div>
</details>
<details class="script" id="vmDropBox">
  <summary>📼 VM drop message (record once — unlocks the Drop VM button)</summary>
  <div id="vmRecStatus" style="font-size:12px;color:#666;margin:6px 0">
    Record a ~15s message once. On voicemail rounds you tap 📼 at the beep and
    it plays into the voicemail while you keep dialing.
  </div>
  <div style="display:flex;gap:6px">
    <button class="q-btn" id="btnVmRec">● Record</button>
    <button class="q-btn" id="btnVmStop" disabled>■ Stop</button>
    <button class="q-btn" id="btnVmSave" disabled>💾 Save</button>
  </div>
  <audio id="vmPreview" controls style="width:100%;margin-top:6px;display:none"></audio>
</details>
<div class="log" id="log"></div>

<script src="/static/twilio-voice.min.js"></script>
<script>
(function() {
  const statusEl  = document.getElementById('status');
  const timerEl   = document.getElementById('timer');
  const nameEl    = document.getElementById('leadName');
  const phoneEl   = document.getElementById('leadPhone');
  const btnCall   = document.getElementById('btnCall');
  const btnMute   = document.getElementById('btnMute');
  const btnHangup = document.getElementById('btnHangup');
  const logEl     = document.getElementById('log');
  const dispEl    = document.getElementById('disp');
  const dispDoneEl = document.getElementById('dispDone');
  const dispOutcomeEl = document.getElementById('dispOutcome');
  const dispStatusEl  = document.getElementById('dispStatus');
  const dispNote  = document.getElementById('dispNote');
  const liveNotes = document.getElementById('liveNotes');
  const btnSaveNote = document.getElementById('btnSaveNote');
  const btnInvestor = document.getElementById('btnInvestor');
  const noteSaveStatus = document.getElementById('noteSaveStatus');
  let noteLeadId = null, noteSavePending = false;
  const noteDrafts = {};
  const btnDispNext = document.getElementById('btnDispNext');
  const repNameEl = document.getElementById('repName');
  const scOpen1 = document.getElementById('scOpen1');
  const scOpen2 = document.getElementById('scOpen2');
  const scOpen3 = document.getElementById('scOpen3');
  const scConnect = document.getElementById('scConnect');

  let currentLeadId = null;     // lead being handled (null for manual quick-dials)
  let savedParts = [];          // confirmation chips shown after saving
  let lastScriptInfo = null;    // {first_name, address} for the active lead
  let lastCallDuration = 0;     // seconds, for the KPI disposition row
  let dispBatchId = null;       // batch the disposition belongs to (multi-dial)

  // ── Server-side queue (dispose → auto-dial next batch) ────────────────
  const queueBar  = document.getElementById('queueBar');
  const queueInfo = document.getElementById('queueInfo');
  const btnQNext  = document.getElementById('btnQNext');
  const btnQAuto  = document.getElementById('btnQAuto');
  const btnQEnd   = document.getElementById('btnQEnd');
  let queueActive = false, queueRemaining = 0, queueTotal = 0;
  let nextTimer = null;                 // pending auto-advance countdown
  let outcomeSaved = false;             // this call's outcome was logged
  let statusSaved = false;              // this call's hot/warm/cold was set
  let lastOutcomeNoStatus = false;      // outcome that needs no temperature
  // Outcomes where there's no temperature to set — advance right after saving.
  var NO_STATUS_OUTCOMES = ['Wrong Number', 'No Answer', 'Voicemail', 'Dead Air', 'No Longer Owns', 'Hung Up', 'Not Interested'];

  // Which agent this popup belongs to (rom / gabby). Each agent's popup polls
  // its own dial channel and registers its own Twilio identity.
  var AGENT = (new URLSearchParams(location.search).get('agent') || 'rom').toLowerCase();
  document.title = 'CRM Softphone - ' + AGENT.charAt(0).toUpperCase() + AGENT.slice(1);
  var h3 = document.querySelector('h3');
  if (h3) h3.textContent = '📞 CRM Softphone — ' + AGENT.charAt(0).toUpperCase() + AGENT.slice(1);

  // Rep name persists per agent; defaults to the agent's name.
  repNameEl.value = AGENT.charAt(0).toUpperCase() + AGENT.slice(1);
  try { var _rn = localStorage.getItem('crm_rep_name_' + AGENT); if (_rn) repNameEl.value = _rn; } catch (e) {}
  repNameEl.addEventListener('input', function() {
    try { localStorage.setItem('crm_rep_name_' + AGENT, repNameEl.value); } catch (e) {}
    renderScript(lastScriptInfo);
  });

  function renderScript(info) {
    lastScriptInfo = info;
    var rep = (repNameEl.value || 'Rom').trim() || 'Rom';
    var first = (info && info.first_name) ? info.first_name : '';
    var addr = (info && info.address) ? info.address : '';
    var addrPhrase = addr || 'this property';
    var namePhrase = first || 'there';
    scOpen1.textContent = 'Hey, is this the owner of ' + addrPhrase + '?';
    scOpen2.textContent = 'Hey, this is ' + rep + ' - is this ' + namePhrase + '?';
    scOpen3.textContent = 'Hey, this is ' + rep + ', I was hoping to reach the owner of ' + addrPhrase + '?';
    scConnect.textContent = "Great - my partner and I buy a few houses around here, and I wanted to "
      + "see if you'd ever consider selling this one, now or down the road.";
  }

  var attemptLine = document.getElementById('attemptLine');

  // Attempt counter + pro voicemail cadence: no VM on attempts 1-2 (protect
  // pace, VMs rarely get returned early), leave a short VM on attempt 3 and
  // every 3rd attempt after (3, 6, 9...) — familiarity builds callbacks.
  function renderAttempt(info) {
    if (!info || typeof info.attempts === 'undefined') { attemptLine.textContent = ''; return; }
    var att = (info.attempts || 0) + 1;   // the attempt happening right now
    var vmNow = att >= 3 && ((att - 3) % 3 === 0);
    if (vmNow) {
      attemptLine.innerHTML = '📞 Attempt #' + att +
        (hasDropAudio
          ? ' · 📼 <b>VM round — hit 📼 Drop VM at the beep</b>'
          : ' · 💡 <b>VM round — leave a short voicemail if a machine picks up</b>');
    } else if (att < 3) {
      attemptLine.innerHTML = '📞 Attempt #' + att +
        ' · 🚫 no voicemail yet — pros start VMs on attempt 3';
    } else {
      var nextVm = att + ((3 - ((att - 3) % 3)) % 3);
      attemptLine.innerHTML = '📞 Attempt #' + att +
        ' · 🚫 skip the VM this round (next VM: attempt ' + nextVm + ')';
    }
  }

  function loadScriptForLead(leadId) {
    if (!leadId) { renderScript(null); attemptLine.textContent = ''; return; }
    fetch('/dialer/lead/' + leadId)
      .then(function(r) { return r.json(); })
      .then(function(info) { renderScript(info); renderAttempt(info); })
      .catch(function() { renderScript(null); attemptLine.textContent = ''; });
  }

  renderScript(null);   // show generic script until a lead is dialed

  let device = null;
  let call = null;
  let connectPending = false;
  let callGeneration = 0;
  let muted = false;
  let timerInterval = null;
  let callStart = null;
  let deviceReady = false;
  let lastDialTs = null;
  let currentBatchId = null;
  let batchPollInterval = null;
  let joinToken = '';               // per-join ownership token (agent-ready)

  // Browser-side ringback: plays in the popup while lines ring, stops the
  // instant a lead connects (or the batch ends). Twilio-side wait music was
  // removed — it required a passive conference join that could double-create
  // the room and strand the agent.
  var ringAudio = null;
  function startRinging() {
    try {
      if (!ringAudio) { ringAudio = new Audio('/static/ringback.wav'); ringAudio.loop = true; }
      ringAudio.currentTime = 0;
      ringAudio.play().catch(function() {
        logMsg('Ring sound blocked by browser — click 🔊 Test once to enable audio.');
      });
    } catch (e) {}
  }
  function stopRinging() {
    try { if (ringAudio) ringAudio.pause(); } catch (e) {}
  }

  // Auto-retry-once state (absorbs transient funnel 502s without double-dialing)
  let acceptedFired = false;     // did this call ever connect to a human?
  let dialStartTime = 0;         // when the current single dial began
  let retriedThisDial = false;   // already used our one retry?
  let lastSingleParams = null;   // the lead/number to re-dial on retry
  let callEndHandled = false;    // guard against duplicate end events

  function isTransientError(err) {
    if (!err) return false;
    var code = err.code || (err.error && err.error.code);
    if (code === 31005 || code === 31000 || code === 31009 || code === 53000) return true;
    var m = '' + (err.message || '');
    return /gateway|bad gateway|502|connection/i.test(m);
  }

  // 2.x: call events live on the Call object. Wire them when a call starts.
  function wireCall(c, batchId, generation) {
    call = c;
    callEndHandled = false;
    acceptedFired = false;
    c.on('accept', function() {
      if (call !== c || generation !== callGeneration) return;
      btnHangup.disabled = false;
      btnMute.disabled = false;
      acceptedFired = true;
      if (batchId) {
        // Agent is now in the conference — tell the server to fire the calls.
        // The token makes ownership exclusive: if another softphone (same
        // agent in two browsers) got there first, we back out immediately
        // instead of double-joining the room.
        fetch('/dialer/agent-ready/' + batchId, { method: 'POST',
          body: JSON.stringify({ token: joinToken }) })
          .then(function(r) { return r.json(); })
          .then(function(j) {
            if (call !== c || generation !== callGeneration) return;
            if (j && j.owner === false) {
              logMsg('Another softphone (same agent) owns this batch — backing out.');
              setStatus('Batch taken by your other softphone window', 'ended');
              stopRinging();
              if (device) device.disconnectAll();
            }
          })
          .catch(function(){});
        logMsg('In the room — firing lines...');
      } else {
        setStatus('Connected', 'connected');
        startTimer();
        logMsg('Call connected');
      }
    });
    c.on('disconnect', function() { if (call === c && generation === callGeneration) handleCallEnd(null); });
    c.on('cancel', function() { if (call === c && generation === callGeneration) handleCallEnd(null); });
    c.on('reject', function() { if (call === c && generation === callGeneration) handleCallEnd(null); });
    c.on('error', function(err) {
      if (call !== c || generation !== callGeneration) return;
      logMsg('Call error: ' + (err.code ? ('[' + err.code + '] ') : '') + (err.message || err));
      handleCallEnd(err);
    });
  }

  function handleCallEnd(err) {
    if (callEndHandled) return;   // ignore duplicate end events for one call
    callEndHandled = true;
    call = null;
    if (callStart) {
      lastCallDuration = Math.floor((Date.now() - callStart) / 1000);
      callStart = null;
    }
    stopTimer();
    // Retry ONCE only if: transient gateway/connection error, the call NEVER
    // connected (no human answered), it's not a multi-line batch, we haven't
    // retried yet, and it failed fast (<5s — a 502 fails instantly with no PSTN
    // call placed, so re-dialing cannot reach someone twice).
    var canRetry = isTransientError(err) && !acceptedFired && !currentBatchId
                   && !retriedThisDial && lastSingleParams
                   && (Date.now() - dialStartTime < 5000);
    if (canRetry) {
      retriedThisDial = true;
      setStatus('Connection hiccup — retrying once...', 'connecting');
      logMsg('Transient error before connect; retrying once in 1.2s...');
      var p = lastSingleParams;
      setTimeout(function() { dialSingle(p, true); }, 1200);
      return;
    }
    onCallEnded();
  }

  function onCallEnded() {
    callGeneration += 1;
    const endedGeneration = callGeneration;
    connectPending = false;
    call = null;
    currentBatchId = null;
    muted = false;
    stopTimer();
    stopRinging();
    if (batchPollInterval) { clearInterval(batchPollInterval); batchPollInterval = null; }
    btnCall.disabled = false;
    btnHangup.disabled = true;
    btnMute.disabled = true;
    if (typeof btnDropVm !== 'undefined') btnDropVm.disabled = true;
    btnMute.textContent = '🔇 Mute';
    btnMute.classList.remove('active');
    logMsg('Call ended');
    if (currentLeadId) {
      // The disconnect may be AMD killing a voicemail winner (the room ends
      // with it) — check the batch before asking for a disposition that the
      // server already logged.
      if (dispBatchId) {
        fetch('/dialer/batch/' + dispBatchId)
          .then(function(r) { return r.json(); })
          .then(function(b) {
            if (endedGeneration !== callGeneration) return;
            if (b && b.status === 'machine_ended') {
              currentLeadId = null;
              hideDisposition();
              nameEl.textContent = 'Voicemail — auto-skipped';
              phoneEl.textContent = '';
              setStatus('🤖 Voicemail detected — hung up & logged automatically', 'no-answer');
              logMsg('AMD: connected line was a voicemail — auto-disposed.');
              if (queueActive && autoAdvance) scheduleNext(1500, 'VM skipped — next batch');
            } else {
              setStatus('Call ended — pick a disposition below', 'ended');
              showDisposition();
            }
          })
          .catch(function() {
            if (endedGeneration !== callGeneration) return;
            setStatus('Call ended — pick a disposition below', 'ended');
            showDisposition();
          });
      } else {
        setStatus('Call ended — pick a disposition below', 'ended');
        showDisposition();
      }
    } else {
      setStatus('Call ended', 'ended');
    }
  }

  function logMsg(msg) {
    const d = document.createElement('div');
    const now = new Date().toLocaleTimeString();
    d.textContent = now + ' — ' + msg;
    logEl.prepend(d);
    while (logEl.children.length > 30) logEl.removeChild(logEl.lastChild);
  }

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = 'status ' + (cls || '');
  }

  // Hard lock: while a real (human) call is waiting to be written up, NOTHING
  // may auto-dial the next batch. Cleared only by disposing or an explicit
  // Next click. This is what stops a manual hang-up from skipping straight to
  // the next person without a disposition.
  var awaitingDisposition = false;

  function showDisposition() {
    if (currentLeadId) {
      awaitingDisposition = true;
      dispEl.classList.add('show');
    }
  }
  function hideDisposition() {
    awaitingDisposition = false;
    dispEl.classList.remove('show');
    dispDoneEl.classList.remove('show');
    dispDoneEl.textContent = '';
    dispOutcomeEl.classList.remove('locked');
    dispStatusEl.classList.remove('locked');
    var _ss = document.getElementById('statusSection');
    if (_ss) _ss.style.display = '';   // restore status prompt for the next call
    savedParts = [];
    outcomeSaved = false;
    statusSaved = false;
    lastOutcomeNoStatus = false;
  }

  // Outcomes that don't need a hot/warm/cold temperature — just log and move on.
  var TERMINAL_OUTCOMES = ['Wrong Number', 'Dead Air', 'No Longer Owns', 'Hung Up', 'Not Interested'];
  function showSaved(part) {
    savedParts.push(part);
    dispDoneEl.textContent = '✓ Saved: ' + savedParts.join('  ·  ');
    dispDoneEl.classList.add('show');
  }

  // Call outcome -> logs the call + sets the next-call date.
  window.dispose = function(label) {
    if (!currentLeadId || outcomeSaved) return;
    dispOutcomeEl.classList.add('locked');   // one outcome per call
    var terminal = TERMINAL_OUTCOMES.indexOf(label) !== -1 || label === 'Investor';
    if (terminal) {
      // No temperature needed — hide the status prompt entirely.
      var _ss = document.getElementById('statusSection');
      if (_ss) _ss.style.display = 'none';
    }
    // KPI extras: who called, how long, which batch/numbers.
    var singleMatch = lastSingleParams
      && String(lastSingleParams.lead_id) === String(currentLeadId);
    fetch('/dialer/dispose', { method: 'POST',
      body: JSON.stringify({
        lead_id: currentLeadId, disposition: label,
        agent: (repNameEl.value || '').trim() || null,
        duration_sec: lastCallDuration || 0,
        batch_id: dispBatchId,
        from_number: singleMatch ? (lastSingleParams.caller_id || null) : null,
        to_number: singleMatch ? (lastSingleParams.lead_phone || null) : null
      }) })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j.ok) {
          showSaved(label);
          logMsg('Outcome saved: ' + label);
          outcomeSaved = true;
          if (label === 'Investor') { statusSaved = true; btnInvestor.disabled = true; }
          lastOutcomeNoStatus = NO_STATUS_OUTCOMES.indexOf(label) !== -1;
          if (terminal) { dispDoneEl.textContent = '✓ ' + label; }
          maybeAdvance();
        } else {
          dispOutcomeEl.classList.remove('locked');
          logMsg('Dispose failed: ' + (j.error || ''));
        }
      })
      .catch(function(e) { dispOutcomeEl.classList.remove('locked'); logMsg('Dispose failed: ' + e); });
  };

  // Lead status -> sets cold/warm/hot (direct status field update).
  window.setLeadStatus = function(status) {
    if (!currentLeadId) return;
    dispStatusEl.classList.add('locked');    // one status per call
    fetch('/dialer/status', { method: 'POST',
      body: JSON.stringify({ lead_id: currentLeadId, status: status,
        agent: (repNameEl.value || '').trim() || null }) })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j.ok) {
          showSaved(status.toUpperCase()); logMsg('Status set: ' + status);
          statusSaved = true;
          maybeAdvance();
        }
        else { dispStatusEl.classList.remove('locked'); logMsg('Status failed: ' + (j.error || '')); }
      })
      .catch(function(e) { dispStatusEl.classList.remove('locked'); logMsg('Status failed: ' + e); });
  };

  // ── Queue engine: dispose → auto-dial the next batch ──────────────────
  var autoAdvance = true;
  try { autoAdvance = localStorage.getItem('crm_auto_adv_' + AGENT) !== '0'; } catch (e) {}

  function updateQueueBar() {
    if (!queueActive) { queueBar.classList.remove('show'); return; }
    queueBar.classList.add('show');
    queueInfo.textContent = '📋 ' + queueRemaining + ' of ' + queueTotal + ' left';
    btnQAuto.textContent = autoAdvance ? '⏸ Auto: ON' : '▶ Auto: OFF';
  }

  // Reopening the popup mid-queue resumes dialing automatically — no need
  // to hit Next. Fires once per popup load; a batch already ringing just
  // returns 'busy' harmlessly (and the /dialer/current poll rejoins it).
  var autoResumed = false;
  function maybeAutoResume() {
    if (autoResumed || !queueActive || call || connectPending || !deviceReady || !autoAdvance) return;
    if (awaitingDisposition) return;   // a call is waiting to be written up
    autoResumed = true;
    logMsg('Queue is active — resuming automatically...');
    scheduleNext(1500, 'Resuming queue');
  }

  function refreshQueue() {
    fetch('/dialer/queue/status?agent=' + AGENT)
      .then(function(r) { return r.json(); })
      .then(function(j) {
        queueActive = !!j.active;
        if (j.active) { queueRemaining = j.remaining; queueTotal = j.total; }
        updateQueueBar();
        maybeAutoResume();
      })
      .catch(function() {});
  }

  // Advance once the call is fully written up: outcome saved, plus a
  // temperature unless the outcome doesn't need one (no answer / VM / wrong #).
  function maybeAdvance() {
    if (call || connectPending || noteSavePending) return;
    if (!outcomeSaved) return;
    if (!statusSaved && !lastOutcomeNoStatus) return;
    // Temperature outcomes (warm/hot/cold): don't auto-jump — let the agent
    // write a note, then advance with the ✓ Save & Next button.
    if (!lastOutcomeNoStatus) {
      setStatus('Add a note (optional), then hit ✓ Save & Next', 'ended');
      return;
    }
    if (!queueActive || !autoAdvance) return;
    // No-status outcomes auto-advance — unless the agent started a note.
    if (dispNote && dispNote.value.trim()) return;
    awaitingDisposition = false;
    scheduleNext(2200, 'Saved — dialing next batch');
  }

  // Save the typed note (if any) then advance. This is the button the agent
  // hits after warm/hot/cold + note; also works for any outcome.
  async function saveNoteThenNext() {
    if (nextTimer) { clearTimeout(nextTimer); nextTimer = null; }
    if (noteSavePending) return;
    const leadId = currentLeadId;
    if (!await saveCallNote()) return;
    if (call || connectPending || currentLeadId !== leadId || hasUnsavedNote()) return;
    awaitingDisposition = false;
    if (queueActive && autoAdvance) scheduleNext(600, 'Next lead');
    else { hideDisposition(); setStatus('Saved — hit ⏭ Next when ready', 'ended'); }
  }

  function bindCallNotes(leadId) {
    if (noteLeadId) noteDrafts[noteLeadId] = dispNote.value;
    noteLeadId = leadId;
    dispNote.value = leadId ? (noteDrafts[leadId] || '') : '';
    liveNotes.style.display = leadId ? '' : 'none';
    noteSaveStatus.textContent = '';
    btnInvestor.disabled = false;
  }

  function hasUnsavedNote() {
    return noteSavePending || !!(dispNote && dispNote.value.trim());
  }

  async function saveCallNote() {
    if (noteSavePending) return false;
    const leadId = noteLeadId;
    const draft = dispNote.value;
    if (!draft.trim()) return true;
    if (!leadId) return false;
    noteSavePending = true;
    btnSaveNote.disabled = true;
    noteSaveStatus.textContent = 'Saving…';
    try {
      const response = await fetch('/dialer/note', { method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lead_id: leadId, note: draft.trim(),
          agent: (repNameEl.value || '').trim() || null}) });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'Save failed');
      // Preserve anything typed while this request was in flight.
      let latest = noteLeadId === leadId ? dispNote.value : (noteDrafts[leadId] || '');
      if (latest.startsWith(draft)) latest = latest.slice(draft.length);
      noteDrafts[leadId] = latest;
      if (noteLeadId === leadId) {
        dispNote.value = latest;
        noteSaveStatus.textContent = latest.trim() ? 'Saved. New edits still unsaved.' : '✓ Note saved to CRM';
      }
      logMsg('Note saved.');
      return true;
    } catch (error) {
      if (noteLeadId === leadId) noteSaveStatus.textContent = 'Save failed — your note is kept. Try again.';
      logMsg('Note save failed — keeping draft and staying on this lead.');
      return false;
    } finally {
      noteSavePending = false;
      btnSaveNote.disabled = false;
    }
  }

  function scheduleNext(ms, why) {
    // Never auto-advance over a pending disposition.
    if (awaitingDisposition) return;
    if (nextTimer) clearTimeout(nextTimer);
    setStatus((why || 'Next batch') + ' in ' + (Math.round(ms / 500) / 2) + 's…', 'ready');
    nextTimer = setTimeout(function() { nextTimer = null; if (queueActive && autoAdvance && !queueStopped) doNext(); }, ms);
  }

  var nextRetries = 0;
  var nextPending = false;
  var queueStopped = false;
  function doNext() {
    if (nextTimer) { clearTimeout(nextTimer); nextTimer = null; }
    if (call || connectPending) { logMsg('Still on a call — hang up first.'); return; }
    if (hasUnsavedNote()) { noteSaveStatus.textContent = 'Save your note before moving to the next lead.'; return; }
    if (nextPending) return;
    nextPending = true;
    queueStopped = false;
    fetch('/dialer/queue/next', { method: 'POST', body: JSON.stringify({ agent: AGENT }) })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        nextPending = false;
        if (queueStopped) return;
        nextRetries = 0;
        if (j.error === 'busy') { logMsg('A batch is already ringing.'); return; }
        if (j.error === 'pool_exhausted') {
          setStatus('🛑 Daily dial limit reached — queue paused until tomorrow', 'error');
          logMsg('Pool at daily cap. ' + j.remaining + ' leads still queued — they keep their place.');
          refreshPool();
          return;
        }
        if (j.done) { queueDone(); return; }
        if (!j.batch_id) return;
        queueRemaining = j.remaining; queueTotal = j.total || queueTotal;
        queueActive = true;
        updateQueueBar();
        hideDisposition();
        setStatus('Dialing next ' + j.lines + ' line(s)…', 'connecting');
        logMsg('Queue: next batch fired (' + j.remaining + ' left after this)');
        // The /dialer/current poll sees the new batch and joins its conference.
      })
      .catch(function(e) {
        nextPending = false;
        if (queueStopped) return;
        // Backend hiccup (restart / tunnel blip / empty response). Never
        // freeze the queue on one bad response — back off and retry, then
        // hand control back to the agent with a clear message.
        nextRetries += 1;
        if (nextRetries <= 3) {
          var wait = 2000 * nextRetries;
          setStatus('Backend hiccup — retrying in ' + (wait / 1000) + 's… (' + nextRetries + '/3)', 'connecting');
          logMsg('Queue next failed (' + e + ') — retry ' + nextRetries + '/3');
          nextTimer = setTimeout(function() { nextTimer = null; doNext(); }, wait);
        } else {
          nextRetries = 0;
          setStatus('⚠️ Queue paused — backend unreachable. Hit ⏭ Next to resume.', 'error');
          logMsg('Queue next failed 3x — paused. Your list is safe; hit Next to retry.');
        }
      });
  }

  function queueDone() {
    queueActive = false;
    updateQueueBar();
    nameEl.textContent = 'Queue finished 🎉';
    phoneEl.textContent = '';
    setStatus('All leads dialed — load another list in the CRM', 'ready');
    logMsg('Queue finished.');
  }

  btnQNext.addEventListener('click', function() {
    awaitingDisposition = false;   // explicit skip — user chose to move on
    doNext();
  });

  // Save & Next lead (after a disposition + optional note).
  if (btnDispNext) btnDispNext.addEventListener('click', saveNoteThenNext);
  btnSaveNote.addEventListener('click', saveCallNote);
  // Typing a note cancels any pending auto-advance so it can't jump away
  // mid-sentence — the agent finishes and hits Save & Next.
  if (dispNote) dispNote.addEventListener('input', function() {
    if (noteLeadId) noteDrafts[noteLeadId] = dispNote.value;
    noteSaveStatus.textContent = dispNote.value.trim() ? 'Unsaved note' : '';
    if (nextTimer) {
      clearTimeout(nextTimer); nextTimer = null;
      setStatus('Writing a note — hit ✓ Save & Next when done', 'ended');
    }
  });
  btnQAuto.addEventListener('click', function() {
    autoAdvance = !autoAdvance;
    try { localStorage.setItem('crm_auto_adv_' + AGENT, autoAdvance ? '1' : '0'); } catch (e) {}
    if (!autoAdvance && nextTimer) {
      clearTimeout(nextTimer); nextTimer = null;
      setStatus('Auto-dial paused — hit ⏭ Next when ready', 'ended');
    }
    updateQueueBar();
    logMsg('Auto-dial ' + (autoAdvance ? 'ON' : 'OFF'));
  });
  btnQEnd.addEventListener('click', function() {
    if (nextTimer) { clearTimeout(nextTimer); nextTimer = null; }
    queueStopped = true;
    queueActive = false; autoAdvance = false; updateQueueBar();
    fetch('/dialer/queue/stop', { method: 'POST', body: JSON.stringify({ agent: AGENT }) })
      .then(function() {
        queueActive = false; updateQueueBar();
        setStatus('Queue ended', 'ended'); logMsg('Queue ended by agent.');
      })
      .catch(function() {});
  });

  refreshQueue();                      // restore the bar if a queue is running
  setInterval(refreshQueue, 4000);     // stay in sync with the CRM

  // ── Caller ID pool bar: warm-up + hard daily limit tracking ───────────
  var poolBar = document.getElementById('poolBar');
  function refreshPool() {
    fetch('/dialer/pool-status')
      .then(function(r) { return r.json(); })
      .then(function(p) {
        if (!p || !p.numbers) { poolBar.style.display = 'none'; return; }
        poolBar.style.display = 'block';
        if (p.remaining_today <= 0) {
          poolBar.className = 'poolbar warn';
          poolBar.textContent = '🛑 Daily dial limit hit (' + p.used_today + '/' +
            p.cap_today + ') — paused to protect your numbers. Resumes tomorrow.';
          return;
        }
        var txt = '☎️ ' + p.used_today + '/' + p.cap_today + ' calls today · ' +
                  p.remaining_today + ' left';
        if (p.active > 0) txt += ' · ' + p.active + ' warmed';
        if (p.warming > 0) {
          txt += ' · ' + p.warming + ' warming';
          if (p.warmup_day) {
            txt += ' — day ' + p.warmup_day + '/' + p.warmup_total_days +
                   ' (' + p.warming_cap +
                   (p.warming_cap_max > p.warming_cap ? '–' + p.warming_cap_max : '') + '/number)';
          } else {
            txt += ' — newer numbers still warming';
          }
        }
        poolBar.className = 'poolbar' + (p.remaining_today < 20 ? ' low' : '');
        poolBar.textContent = txt;
      })
      .catch(function() {});
  }
  refreshPool();
  setInterval(refreshPool, 30000);

  // ── AI call notes bar: running count while the Dialer switch is on ─────
  var aiBar = document.getElementById('aiBar');
  function refreshAiNotes() {
    fetch('/dialer/ai-notes-status')
      .then(function(r) { return r.json(); })
      .then(function(a) {
        if (!a || !a.active) { aiBar.style.display = 'none'; return; }
        aiBar.style.display = 'block';
        var txt = '🤖 AI notes on · ' + a.notes + (a.notes === 1 ? ' call' : ' calls') +
                  ' · $' + Number(a.cost_usd).toFixed(4);
        if (a.limit_reached) {
          aiBar.className = 'poolbar warn';
          aiBar.textContent = txt + ' — daily AI limit reached, notes paused';
        } else {
          aiBar.className = 'poolbar';
          aiBar.textContent = txt;
        }
      })
      .catch(function() {});
  }
  refreshAiNotes();
  setInterval(refreshAiNotes, 15000);

  function updateTimer() {
    if (!callStart) return;
    const secs = Math.floor((Date.now() - callStart) / 1000);
    const m = String(Math.floor(secs / 60)).padStart(2, '0');
    const s = String(secs % 60).padStart(2, '0');
    timerEl.textContent = m + ':' + s;
  }

  function startTimer() {
    callStart = Date.now();
    timerInterval = setInterval(updateTimer, 1000);
    updateTimer();
  }

  function stopTimer() {
    clearInterval(timerInterval);
    timerInterval = null;
  }

  // ── Single-line dial ──────────────────────────────────────────────────
  async function dialSingle(lead, isRetry) {
    if (call || connectPending || !device) return;
    lastSingleParams = lead;
    dialStartTime = Date.now();
    if (!isRetry) { retriedThisDial = false; }
    hideDisposition();
    currentLeadId = lead.lead_id || null;   // null for manual quick-dials
    bindCallNotes(currentLeadId);
    dispBatchId = null;
    lastCallDuration = 0;
    loadScriptForLead(currentLeadId);
    lastDialTs = lead.ts;
    nameEl.textContent = lead.lead_name || '(unknown)';
    phoneEl.textContent = lead.lead_phone || '';
    timerEl.textContent = '00:00';
    setStatus('Connecting...', 'connecting');
    btnCall.disabled = true;
    btnHangup.disabled = false;
    btnMute.disabled = false;
    logMsg('Dialing ' + (lead.lead_name || lead.lead_phone));
    // Ad-hoc quick-dial passes a raw to_number with no lead_id.
    var params = { caller_id: lead.caller_id || '' };
    if (lead.to_number) { params.to_number = lead.to_number; }
    else { params.lead_id = String(lead.lead_id); }
    const generation = ++callGeneration;
    connectPending = true;
    try {
      var c = await device.connect({ params: params });
      if (generation !== callGeneration) { c.disconnect(); return; }
      connectPending = false;
      wireCall(c, null, generation);
    } catch (e) {
      if (generation !== callGeneration) return;
      connectPending = false;
      setStatus('Connect failed: ' + (e.message || e), 'error');
      logMsg('Connect failed: ' + (e.message || e));
      onCallEnded();
    }
  }

  // ── Multi-line dial ───────────────────────────────────────────────────
  async function joinMulti(data) {
    if (call || connectPending || !device) return;
    const generation = ++callGeneration;
    connectPending = true;
    const batchId = data.batch_id;
    lastDialTs = data.ts;   // claim this command immediately (no double-trigger)
    // Stale-replay guard: never join a batch that already ended (a fresh popup
    // can see an old command still sitting in the channel).
    try {
      var bs = await (await fetch('/dialer/batch/' + data.batch_id)).json();
      if (generation !== callGeneration) return;
      if (!bs || (bs.status !== 'waiting_agent' && bs.status !== 'ringing')) {
        connectPending = false;
        logMsg('Ignored stale batch ' + data.batch_id + ' (' + ((bs && bs.status) || 'gone') + ')');
        return;
      }
    } catch (e) { if (generation === callGeneration) connectPending = false; return; }
    if (generation !== callGeneration || call) return;       // a dial started while we were checking
    hideDisposition();
    currentLeadId = null;
    dispBatchId = data.batch_id;
    bindCallNotes(null);
    lastCallDuration = 0;
    currentBatchId = data.batch_id;
    joinToken = Math.random().toString(36).slice(2) + Date.now().toString(36);
    nameEl.textContent = data.lines + ' lines ringing...';
    phoneEl.textContent = '';
    loadScriptForLead(null);
    timerEl.textContent = '00:00';
    setStatus('Ringing ' + data.lines + ' leads — waiting for first answer', 'ringing');
    startRinging();
    btnCall.disabled = true;
    btnHangup.disabled = false;
    btnMute.disabled = false;
    logMsg('Multi-line: joining conference ' + data.conference);
    const joinTimeout = setTimeout(function() {
      if (generation !== callGeneration || !connectPending) return;
      hangUp();
      autoAdvance = false; updateQueueBar();
      setStatus('Connection timed out — press Next to retry', 'error');
    }, 35000);
    try {
      var c = await device.connect({ params: { conference: data.conference } });
      clearTimeout(joinTimeout);
      if (generation !== callGeneration) { c.disconnect(); return; }
      connectPending = false;
      wireCall(c, batchId, generation);
    } catch (e) {
      clearTimeout(joinTimeout);
      if (generation !== callGeneration) return;
      connectPending = false;
      setStatus('Connect failed: ' + (e.message || e), 'error');
      logMsg('Connect failed: ' + (e.message || e));
      onCallEnded();
      return;
    }

    if (batchPollInterval) clearInterval(batchPollInterval);
    var batchStart = Date.now();
    batchPollInterval = setInterval(function() {
      if (generation !== callGeneration) return;
      // Watchdog: a batch that never resolves (Twilio Device join hung, or
      // every line failed silently with no status callback) must not freeze
      // the softphone on "ringing" forever — recover after 45s.
      if (Date.now() - batchStart > 45000 && !call) {
        clearInterval(batchPollInterval); batchPollInterval = null;
        stopRinging();
        setStatus('No connection this batch — moving on', 'no-answer');
        logMsg('Batch stuck 45s with no answer — recovering, next batch.');
        if (device) device.disconnectAll();
        if (queueActive && autoAdvance) scheduleNext(1500, 'Recovering — next batch');
        return;
      }
      // Hard cap even while sitting in the room: every line rings out in ~30s,
      // so a batch that hasn't connected in 150s is dead. Leave and move on
      // instead of freezing the dialer.
      if (Date.now() - batchStart > 150000) {
        clearInterval(batchPollInterval); batchPollInterval = null;
        stopRinging();
        setStatus('No connection this batch — moving on', 'no-answer');
        logMsg('Batch unresolved after 150s — leaving the room, next batch.');
        if (device) device.disconnectAll();
        if (queueActive && autoAdvance) scheduleNext(1500, 'Recovering — next batch');
        return;
      }
      fetch('/dialer/batch/' + batchId)
        .then(function(r) { return r.json(); })
        .then(function(b) {
          if (generation !== callGeneration) return;
          if (b && b.error) {
            // The server no longer knows this batch (e.g. it restarted) — the
            // room is dead. Leave it instead of ringing forever.
            clearInterval(batchPollInterval); batchPollInterval = null;
            stopRinging();
            setStatus('Batch ended on the server — moving on', 'no-answer');
            logMsg('Batch no longer exists on the server — next batch.');
            if (device) device.disconnectAll();
            if (queueActive && autoAdvance) scheduleNext(1500, 'Recovering — next batch');
            return;
          }
          if (b.status === 'no_agent') {
            // The batch aborted before this softphone finished joining (slow
            // connect) — NO calls were fired. Recover to a fresh batch.
            clearInterval(batchPollInterval); batchPollInterval = null;
            stopRinging();
            nameEl.textContent = '';
            setStatus('Line was slow to connect — retrying batch', 'no-answer');
            logMsg('Batch aborted (softphone joined too late) — dialing a fresh batch.');
            if (device) device.disconnectAll();
            if (queueActive && autoAdvance) scheduleNext(1200, 'Retrying batch');
            return;
          }
          if (b.status === 'connected' && b.winner_name) {
            stopRinging();
            nameEl.textContent = b.winner_name;
            phoneEl.textContent = b.winner_phone || '';
            currentLeadId = b.winner || null;   // dispose the winning lead
            bindCallNotes(currentLeadId);
            if (hasDropAudio && currentLeadId) btnDropVm.disabled = false;
            loadScriptForLead(currentLeadId);   // fill script for the person who answered
            setStatus('Connected — ' + b.winner_name, 'connected');
            logMsg('Connected to ' + b.winner_name);
            startTimer();
            clearInterval(batchPollInterval);
            batchPollInterval = null;
          } else if (b.status === 'ringing' && b.machines_count > 0) {
            setStatus('Ringing — ' + b.machines_count + ' voicemail(s) skipped', 'ringing');
          } else if (b.status === 'machine_ended') {
            // Instant-bridge AMD: the connected line turned out to be a
            // voicemail — server already hung up and logged it.
            stopRinging();
            currentLeadId = null;          // dispo already logged server-side
            hideDisposition();
            nameEl.textContent = 'Voicemail — auto-skipped';
            phoneEl.textContent = '';
            setStatus('🤖 Voicemail detected — hung up & logged automatically', 'no-answer');
            logMsg('AMD: connected line was a voicemail — auto-disposed.');
            clearInterval(batchPollInterval);
            batchPollInterval = null;
            if (device) device.disconnectAll();
            setTimeout(function() {
              if (queueActive && autoAdvance && !call) {
                scheduleNext(1500, 'VM skipped — next batch');
              }
            }, 600);
          } else if (b.status === 'no_answer') {
            nameEl.textContent = 'No one answered';
            phoneEl.textContent = '';
            var vmNote = b.machines_count > 0 ? ' (' + b.machines_count + ' voicemail, auto-logged)' : '';
            setStatus('All ' + b.total + ' lines — no live answer' + vmNote, 'no-answer');
            logMsg('No live answer (' + b.total + ' leads' + vmNote + ')');
            clearInterval(batchPollInterval);
            batchPollInterval = null;
            if (device) device.disconnectAll();
            // Nobody to dispose — keep the queue churning automatically.
            setTimeout(function() {
              if (queueActive && autoAdvance && !call) {
                scheduleNext(1600, 'No answer — next batch');
              }
            }, 600);
          }
        })
        .catch(function() {});
    }, 500);
  }

  function hangUp() {
    if (nextTimer) { clearTimeout(nextTimer); nextTimer = null; }
    callGeneration += 1;
    connectPending = false;
    if (device) device.disconnectAll();
    onCallEnded();
    if (batchPollInterval) { clearInterval(batchPollInterval); batchPollInterval = null; }
  }

  function toggleMute() {
    if (!call) return;
    muted = !muted;
    call.mute(muted);
    btnMute.textContent = muted ? '🔊 Unmute' : '🔇 Mute';
    btnMute.classList.toggle('active', muted);
  }

  btnCall.addEventListener('click', function() { pollForLead(); });
  btnHangup.addEventListener('click', hangUp);
  btnMute.addEventListener('click', toggleMute);

  // 🔊 Speaker test: plays the ring tone locally. Instantly separates
  // "my browser audio is broken" from "the lead was silent" on dead-air calls.
  var btnSound = document.getElementById('btnSound');
  var testAudio = null;
  btnSound.addEventListener('click', function() {
    try {
      if (testAudio) { testAudio.pause(); testAudio = null; return; }
      testAudio = new Audio('/static/ringback.wav');
      testAudio.play().then(function() {
        logMsg('Speaker test: playing ring tone — hearing it means your audio output works.');
        setTimeout(function() { if (testAudio) { testAudio.pause(); testAudio = null; } }, 2500);
      }).catch(function(e) {
        logMsg('Speaker test FAILED: ' + e + ' — browser blocked audio or wrong output device.');
        setStatus('Speaker test failed — check browser audio/output device', 'error');
        testAudio = null;
      });
    } catch (e) { logMsg('Speaker test error: ' + e); }
  });

  // ── 📼 VM drop: agent-triggered, no AMD anywhere ──────────────────────
  var btnDropVm = document.getElementById('btnDropVm');
  var hasDropAudio = false;
  function checkDropAudio() {
    fetch('/static/vm_drop_' + AGENT + '.wav', { method: 'HEAD' })
      .then(function(r) {
        hasDropAudio = r.ok;
        btnDropVm.style.display = hasDropAudio ? '' : 'none';
      })
      .catch(function() {});
  }
  checkDropAudio();

  btnDropVm.addEventListener('click', function() {
    if (!call || !dispBatchId || !currentLeadId) return;
    btnDropVm.disabled = true;
    fetch('/dialer/vm-drop', { method: 'POST', body: JSON.stringify({
      agent: AGENT, batch_id: dispBatchId, lead_id: currentLeadId }) })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (!j.ok) {
          logMsg('VM drop failed: ' + (j.error || ''));
          btnDropVm.disabled = false;
          return;
        }
        logMsg('📼 Message dropping into the voicemail — moving on.');
        currentLeadId = null;            // server already logged the dispo
        awaitingDisposition = false;
        hideDisposition();
        setStatus('📼 VM dropped — next lead', 'no-answer');
        if (device) device.disconnectAll();
        if (queueActive && autoAdvance) scheduleNext(1800, 'VM dropped — next batch');
      })
      .catch(function(e) { logMsg('VM drop failed: ' + e); btnDropVm.disabled = false; });
  });

  // Recorder: capture mic → 16-bit WAV (Twilio <Play> needs wav/mp3, so we
  // build the WAV in the browser — no server-side conversion needed).
  var vmRecBtn = document.getElementById('btnVmRec');
  var vmStopBtn = document.getElementById('btnVmStop');
  var vmSaveBtn = document.getElementById('btnVmSave');
  var vmPreview = document.getElementById('vmPreview');
  var vmStatus = document.getElementById('vmRecStatus');
  var vmCtx = null, vmProc = null, vmStream = null, vmChunks = [], vmRate = 44100, vmBlob = null;

  function encodeWav(chunks, rate) {
    var len = 0;
    chunks.forEach(function(c) { len += c.length; });
    var pcm = new Float32Array(len), off = 0;
    chunks.forEach(function(c) { pcm.set(c, off); off += c.length; });
    var buf = new ArrayBuffer(44 + pcm.length * 2);
    var v = new DataView(buf);
    function ws(o, s) { for (var i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); }
    ws(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); ws(8, 'WAVE');
    ws(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, 1, true); v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    ws(36, 'data'); v.setUint32(40, pcm.length * 2, true);
    for (var i = 0; i < pcm.length; i++) {
      var s = Math.max(-1, Math.min(1, pcm[i]));
      v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Blob([buf], { type: 'audio/wav' });
  }

  vmRecBtn.addEventListener('click', function() {
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function(stream) {
      vmStream = stream;
      vmCtx = new (window.AudioContext || window.webkitAudioContext)();
      vmRate = vmCtx.sampleRate;
      var src = vmCtx.createMediaStreamSource(stream);
      vmProc = vmCtx.createScriptProcessor(4096, 1, 1);
      vmChunks = [];
      vmProc.onaudioprocess = function(e) {
        vmChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      src.connect(vmProc); vmProc.connect(vmCtx.destination);
      vmRecBtn.disabled = true; vmStopBtn.disabled = false; vmSaveBtn.disabled = true;
      vmStatus.textContent = '● Recording… speak your VM message, then hit Stop.';
    }).catch(function(e) { vmStatus.textContent = 'Mic error: ' + e; });
  });

  vmStopBtn.addEventListener('click', function() {
    try { if (vmProc) vmProc.disconnect(); } catch (e) {}
    try { if (vmStream) vmStream.getTracks().forEach(function(t) { t.stop(); }); } catch (e) {}
    try { if (vmCtx) vmCtx.close(); } catch (e) {}
    vmBlob = encodeWav(vmChunks, vmRate);
    vmChunks = [];
    vmPreview.src = URL.createObjectURL(vmBlob);
    vmPreview.style.display = '';
    vmRecBtn.disabled = false; vmStopBtn.disabled = true; vmSaveBtn.disabled = false;
    vmStatus.textContent = 'Listen back below. Happy with it? Hit 💾 Save. Or re-record.';
  });

  vmSaveBtn.addEventListener('click', function() {
    if (!vmBlob) return;
    vmSaveBtn.disabled = true;
    fetch('/dialer/vm-drop-audio?agent=' + AGENT, { method: 'POST', body: vmBlob })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j.ok) {
          vmStatus.textContent = '✅ Saved — the 📼 Drop VM button is now live on calls.';
          logMsg('VM drop message saved.');
          checkDropAudio();
        } else {
          vmStatus.textContent = 'Save failed: ' + (j.error || '');
          vmSaveBtn.disabled = false;
        }
      })
      .catch(function(e) { vmStatus.textContent = 'Save failed: ' + e; vmSaveBtn.disabled = false; });
  });

  // ── Poll /dialer/current for new leads / multi-batches ────────────────
  function pollForLead() {
    fetch('/dialer/current?agent=' + AGENT)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data || !data.ts) return;
        if (data.ts === lastDialTs) return;
        if (call || connectPending) return;
        if (!deviceReady) return;
        if (data.mode === 'multi') { if (!queueStopped) joinMulti(data); }
        else if (data.lead_id || data.to_number) { dialSingle(data); }
      })
      .catch(function() {});
  }

  // Baseline first: ignore any SINGLE-dial command already sitting in the
  // channel from a previous session — only commands issued AFTER this popup
  // opened may dial. Multi-batch commands are NOT baselined: the CRM's Start
  // button fires the batch while this popup is still loading, so the command
  // legitimately predates us. joinMulti's own staleness check (batch must be
  // waiting_agent/ringing) rejects genuinely dead batches, and fresh batches
  // wait 20s for us to join before firing — so letting multi through is safe.
  fetch('/dialer/current?agent=' + AGENT)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d && d.ts && d.mode !== 'multi') {
        // A single-dial command pushed within the last 15s is the click that
        // just OPENED this popup (CRM call buttons open + push in one click)
        // — let it dial. Anything older is a stale leftover: baseline it.
        var t = /^\\d+$/.test(d.ts) ? parseInt(d.ts, 10) : Date.parse(d.ts);
        var age = Date.now() - t;
        if (!(age >= 0 && age < 15000)) { lastDialTs = d.ts; }
      }
    })
    .catch(function() {})
    .finally(function() { setInterval(pollForLead, 500); });

  // ── Init Twilio Device (Voice SDK 2.x) ────────────────────────────────
  function initDevice(token) {
    try {
      device = new Twilio.Device(token, {
        logLevel: 'error',
        codecPreferences: ['opus', 'pcmu'],
      });
    } catch (e) {
      setStatus('Device init failed: ' + (e.message || e), 'error');
      logMsg('Device init failed: ' + (e.message || e));
      return;
    }
    device.on('registered', function() {
      deviceReady = true;
      setStatus('Ready — waiting for lead from CRM', 'ready');
      btnCall.disabled = false;
      logMsg('Device ready. Click CALL in the CRM to start dialing.');
    });
    device.on('error', function(err) {
      setStatus('Device error: ' + (err.message || err), 'error');
      logMsg('Device error: ' + (err.message || err));
    });
    device.on('tokenWillExpire', function() {
      fetch('/voice/token?agent=' + AGENT).then(function(r) { return r.json(); }).then(function(d) {
        if (d.token && device) { device.updateToken(d.token); logMsg('Token refreshed'); }
      }).catch(function() {});
    });
    device.register();
    // Outgoing calls work even before 'registered' fires; mark ready as a
    // fallback so a slow/missing registration event never blocks dialing.
    setTimeout(function() {
      if (!deviceReady && device) {
        deviceReady = true;
        setStatus('Ready — waiting for lead from CRM', 'ready');
        btnCall.disabled = false;
        logMsg('Device ready (outgoing).');
      }
    }, 3000);
  }

  if (typeof Twilio === 'undefined' || !Twilio.Device) {
    setStatus('Voice SDK failed to load — check /static/twilio-voice.min.js', 'error');
    logMsg('ERROR: Twilio Voice SDK not loaded.');
    return;
  }

  setStatus('Fetching token...', '');
  logMsg('Fetching access token (' + AGENT + ')...');
  fetch('/voice/token?agent=' + AGENT)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        setStatus(data.error, 'error');
        logMsg('ERROR: ' + data.error);
        return;
      }
      logMsg('Token received, initializing device...');
      initDevice(data.token);
    })
    .catch(function(err) {
      setStatus('Token fetch failed: ' + err, 'error');
      logMsg('ERROR: ' + err);
    });
})();
</script>
</body></html>"""
    return Response(html, mimetype="text/html")


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def _twiml_response() -> Response:
    """Return empty TwiML so Twilio doesn't retry."""
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        mimetype="text/xml",
    )


if __name__ == "__main__":
    db.init_db(DB_PATH)
    print("Twilio webhook server starting on http://localhost:5001")
    # Use waitress (production WSGI, multi-threaded) so concurrent softphone
    # polls + simultaneous Twilio callbacks during multi-line dialing don't
    # block each other or hang the single-threaded Flask dev server.
    try:
        from waitress import serve
        print("Serving with waitress (threads=16)")
        serve(app, host="0.0.0.0", port=5001, threads=16, channel_timeout=120)
    except ImportError:
        print("waitress not installed; falling back to threaded Flask dev server")
        app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
