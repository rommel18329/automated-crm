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

import os
import re
import sys
import logging
import uuid
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

IL_AREA_CODES = {"217","224","309","312","331","618","630","708","773","779","815","847"}

def pick_from_number(to_phone: str) -> str:
    digits = re.sub(r"\D", "", to_phone or "")
    area = digits[:3] if len(digits) == 10 else digits[1:4] if len(digits) == 11 else ""
    return FROM_IL_NUMBER if (area in IL_AREA_CODES and FROM_IL_NUMBER) else FROM_NUMBER

# In-memory map: CallSid → lead_id, used to attach recordings to the right lead
_call_sid_to_lead: dict[str, int] = {}

# Shared state for the persistent softphone popup.
# Streamlit POSTs here when the user clicks CALL; the popup polls for changes.
_dialer_current: dict = {}   # {lead_id, caller_id, lead_name, lead_phone, ts}

# Multi-line batch state.  batch_id -> dict with:
#   conference, lead_ids, call_sids{lead_id: sid}, winner(lead_id),
#   winner_name, winner_phone, dropped[], resolved_count, total, status
_multi_batches: dict[str, dict] = {}
_batch_lock = threading.Lock()


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
        ' recordingStatusCallback="/voice/recording"'
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
    else:
        log.info(f"No lead mapping for CallSid {call_sid} — recording not attached")
    return ("", 204)


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

    token = AccessToken(ACCOUNT_SID, API_KEY, API_SECRET, identity="crm-dialer")
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
        ' recordingStatusCallback="/voice/recording"'
        ' recordingStatusCallbackMethod="POST">'
        f'{lead_e164}'
        '</Dial>'
        '</Response>'
    )
    log.info(f"Browser call → {lead['name']} ({lead_e164}) from {caller_id}")
    return Response(twiml, mimetype="text/xml")


@app.route("/dialer/multi-start", methods=["POST"])
def dialer_multi_start():
    """Fire N simultaneous outbound calls. First answer wins and joins
    the conference where the browser softphone is waiting."""
    data = request.get_json(force=True) or {}
    lead_ids = data.get("lead_ids", [])
    if not lead_ids:
        return jsonify({"error": "No leads"}), 400

    batch_id = uuid.uuid4().hex[:12]
    conference = f"multi_{batch_id}"
    batch = {
        "batch_id": batch_id,
        "conference": conference,
        "lead_ids": lead_ids,
        "call_sids": {},
        "from_numbers": {},   # lead_id -> caller ID used (for answer-rate stats)
        "winner": None,
        "winner_name": "",
        "winner_phone": "",
        "dropped": [],
        "resolved_count": 0,
        "total": len(lead_ids),
        "status": "ringing",
    }

    with _batch_lock:
        _multi_batches[batch_id] = batch

    def _fire_calls():
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(ACCOUNT_SID, AUTH_TOKEN)

        def _one_call(lid):
            lead = db.fetch_lead(lid, DB_PATH)
            if not lead:
                return
            phone = to_e164(lead["phone"])
            # Rotate caller ID from the pool (area-code match, caps, warm-up).
            # Fall back to the simple 515/773 routing if the pool is empty.
            from_ = caller_id_pool.pick_number(lead["phone"], DB_PATH) or pick_from_number(lead["phone"])
            try:
                call = twilio.calls.create(
                    to=phone,
                    from_=from_,
                    url=f"{PUBLIC_BASE_URL}/voice/multi-answer/{batch_id}/{lid}",
                    status_callback=f"{PUBLIC_BASE_URL}/voice/multi-status/{batch_id}/{lid}",
                    status_callback_event=["completed", "busy", "no-answer", "failed", "canceled"],
                    timeout=25,
                )
                with _batch_lock:
                    batch["call_sids"][lid] = call.sid
                    batch["from_numbers"][lid] = from_
                # Count toward this number's daily cap + answer-rate denominator.
                db.increment_caller_calls(from_, DB_PATH)
                caller_id_pool.record_placed(from_)
                log.info(f"Multi-dial [{batch_id}] → {lead['name']} ({phone}) from {from_} SID={call.sid}")
            except Exception as exc:
                log.warning(f"Multi-dial [{batch_id}] call to {lid} failed: {exc}")
                with _batch_lock:
                    batch["resolved_count"] += 1

        with ThreadPoolExecutor(max_workers=10) as pool:
            pool.map(_one_call, lead_ids)

    threading.Thread(target=_fire_calls, daemon=True).start()

    # Tell the softphone to join the conference
    global _dialer_current
    _dialer_current = {
        "mode": "multi",
        "batch_id": batch_id,
        "conference": conference,
        "lines": len(lead_ids),
        "ts": datetime.utcnow().isoformat(),
    }

    resp = jsonify({"batch_id": batch_id, "lines": len(lead_ids)})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/voice/multi-answer/<batch_id>/<int:lead_id>", methods=["POST"])
def voice_multi_answer(batch_id: str, lead_id: int):
    """A lead answered. If first → join the conference. Otherwise → hangup."""
    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        if not batch:
            return Response('<?xml version="1.0"?><Response><Hangup/></Response>',
                            mimetype="text/xml")

        if batch["winner"] is not None:
            # Someone already won — hang up this call
            log.info(f"Multi [{batch_id}] lead {lead_id} answered but {batch['winner']} already won")
            return Response('<?xml version="1.0"?><Response><Hangup/></Response>',
                            mimetype="text/xml")

        # First answer — this lead wins
        batch["winner"] = lead_id
        batch["status"] = "connected"
        lead = db.fetch_lead(lead_id, DB_PATH)
        batch["winner_name"] = lead["name"] if lead else ""
        batch["winner_phone"] = lead["phone"] if lead else ""
        conference = batch["conference"]
        call_sid = request.form.get("CallSid", "")
        winner_from = batch["from_numbers"].get(lead_id)

    # This number reached a human — credit its answer rate.
    if winner_from:
        caller_id_pool.record_connected(winner_from)

    # Log the call interaction
    if lead:
        db.add_interaction(
            lead_id, "call",
            f"Outbound call (multi-line dialer) to {lead['name']}",
            "outbound", DB_PATH, ts=datetime.utcnow(),
        )
        if call_sid:
            _call_sid_to_lead[call_sid] = lead_id

    # Kill all other ringing calls in a background thread
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

    threading.Thread(target=_kill_others, daemon=True).start()

    log.info(f"Multi [{batch_id}] WINNER: lead {lead_id} ({batch['winner_name']})")
    caller_id = pick_from_number(batch["winner_phone"])
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response><Dial callerId="' + caller_id + '"'
        ' record="record-from-ringing-dual"'
        ' recordingStatusCallback="/voice/recording"'
        ' recordingStatusCallbackMethod="POST">'
        f'<Conference startConferenceOnEnter="true" endConferenceOnExit="true"'
        f' waitUrl="" beep="false">{conference}</Conference>'
        '</Dial></Response>'
    )
    return Response(twiml, mimetype="text/xml")


@app.route("/voice/multi-status/<batch_id>/<int:lead_id>", methods=["POST"])
def voice_multi_status(batch_id: str, lead_id: int):
    """Status callback for each outbound call in a multi-line batch."""
    call_status = request.form.get("CallStatus", "")
    log.info(f"Multi [{batch_id}] lead {lead_id} status: {call_status}")

    with _batch_lock:
        batch = _multi_batches.get(batch_id)
        if batch:
            batch["resolved_count"] += 1
            if batch["resolved_count"] >= batch["total"] and batch["winner"] is None:
                batch["status"] = "no_answer"
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
    if lead.get("conversation_stage") == "not_contacted":
        fields["conversation_stage"] = "initial_contact"
    if days is not None:
        fields["next_action_date"] = (date.today() + timedelta(days=days)).isoformat()
    db.update_lead(int(lead_id), fields, DB_PATH)
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

    db.update_lead(int(lead_id), {"status": status}, DB_PATH)
    db.add_interaction(int(lead_id), "note", f"Marked {status.upper()} from dialer",
                       "outbound", DB_PATH, ts=datetime.utcnow())
    log.info(f"Set lead {lead_id} ({lead['name']}) status -> {status}")

    resp = jsonify({"ok": True, "lead_id": lead_id, "status": status})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/dialer/current", methods=["GET", "POST"])
def dialer_current():
    """Shared state between Streamlit and the softphone popup.
    POST: Streamlit writes {lead_id, caller_id, lead_name, lead_phone, ts}.
    GET:  The popup polls this to detect new leads to dial."""
    global _dialer_current
    if request.method == "POST":
        _dialer_current = request.get_json(force=True) or {}
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    resp = jsonify(_dialer_current)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


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
</style>
</head><body>
<h3>📞 CRM Softphone</h3>
<div class="lead-info">
  <div class="lead-name" id="leadName">Waiting for lead...</div>
  <div class="lead-phone" id="leadPhone"></div>
</div>
<div class="timer" id="timer">00:00</div>
<div class="status" id="status">Starting up...</div>
<div class="buttons">
  <button class="btn btn-call" id="btnCall" disabled>📞 Call</button>
  <button class="btn btn-mute" id="btnMute" disabled>🔇 Mute</button>
  <button class="btn btn-hangup" id="btnHangup" disabled>📕 Hang Up</button>
</div>
<div class="disp" id="disp">
  <div class="disp-title">Call outcome — saves to CRM</div>
  <div class="disp-grid" id="dispOutcome">
    <button class="disp-btn" onclick="dispose('No Answer')">No Answer</button>
    <button class="disp-btn" onclick="dispose('Voicemail')">Voicemail</button>
    <button class="disp-btn" onclick="dispose('Connected')">Connected</button>
    <button class="disp-btn" onclick="dispose('Callback')">Callback</button>
    <button class="disp-btn" onclick="dispose('Not Interested')">Not Interested</button>
    <button class="disp-btn" onclick="dispose('Wrong Number')">Wrong Number</button>
  </div>
  <div class="disp-title" style="margin-top:10px">Set lead status</div>
  <div class="disp-grid disp-grid-3" id="dispStatus">
    <button class="disp-btn st-hot" onclick="setLeadStatus('hot')">🔥 Hot</button>
    <button class="disp-btn st-warm" onclick="setLeadStatus('warm')">🌤 Warm</button>
    <button class="disp-btn st-cold" onclick="setLeadStatus('cold')">❄️ Cold</button>
  </div>
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
  const repNameEl = document.getElementById('repName');
  const scOpen1 = document.getElementById('scOpen1');
  const scOpen2 = document.getElementById('scOpen2');
  const scOpen3 = document.getElementById('scOpen3');
  const scConnect = document.getElementById('scConnect');

  let currentLeadId = null;     // lead being handled (null for manual quick-dials)
  let savedParts = [];          // confirmation chips shown after saving
  let lastScriptInfo = null;    // {first_name, address} for the active lead

  // Rep name persists across sessions (so you and your wife each keep yours).
  try { var _rn = localStorage.getItem('crm_rep_name'); if (_rn) repNameEl.value = _rn; } catch (e) {}
  repNameEl.addEventListener('input', function() {
    try { localStorage.setItem('crm_rep_name', repNameEl.value); } catch (e) {}
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

  function loadScriptForLead(leadId) {
    if (!leadId) { renderScript(null); return; }
    fetch('/dialer/lead/' + leadId)
      .then(function(r) { return r.json(); })
      .then(function(info) { renderScript(info); })
      .catch(function() { renderScript(null); });
  }

  renderScript(null);   // show generic script until a lead is dialed

  let device = null;
  let call = null;
  let muted = false;
  let timerInterval = null;
  let callStart = null;
  let deviceReady = false;
  let lastDialTs = null;
  let currentBatchId = null;
  let batchPollInterval = null;

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
  function wireCall(c) {
    call = c;
    callEndHandled = false;
    acceptedFired = false;
    c.on('accept', function() {
      acceptedFired = true;
      if (!currentBatchId) {
        setStatus('Connected', 'connected');
        startTimer();
        logMsg('Call connected');
      }
    });
    c.on('disconnect', function() { handleCallEnd(null); });
    c.on('cancel', function() { handleCallEnd(null); });
    c.on('reject', function() { handleCallEnd(null); });
    c.on('error', function(err) {
      logMsg('Call error: ' + (err.code ? ('[' + err.code + '] ') : '') + (err.message || err));
      handleCallEnd(err);
    });
  }

  function handleCallEnd(err) {
    if (callEndHandled) return;   // ignore duplicate end events for one call
    callEndHandled = true;
    call = null;
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
    call = null;
    currentBatchId = null;
    muted = false;
    stopTimer();
    if (batchPollInterval) { clearInterval(batchPollInterval); batchPollInterval = null; }
    btnCall.disabled = false;
    btnHangup.disabled = true;
    btnMute.disabled = true;
    btnMute.textContent = '🔇 Mute';
    btnMute.classList.remove('active');
    logMsg('Call ended');
    if (currentLeadId) {
      setStatus('Call ended — pick a disposition below', 'ended');
      showDisposition();
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

  function showDisposition() {
    if (currentLeadId) { dispEl.classList.add('show'); }
  }
  function hideDisposition() {
    dispEl.classList.remove('show');
    dispDoneEl.classList.remove('show');
    dispDoneEl.textContent = '';
    dispOutcomeEl.classList.remove('locked');
    dispStatusEl.classList.remove('locked');
    savedParts = [];
  }
  function showSaved(part) {
    savedParts.push(part);
    dispDoneEl.textContent = '✓ Saved: ' + savedParts.join('  ·  ');
    dispDoneEl.classList.add('show');
  }

  // Call outcome -> logs the call + sets the next-call date.
  window.dispose = function(label) {
    if (!currentLeadId) return;
    dispOutcomeEl.classList.add('locked');   // one outcome per call
    fetch('/dialer/dispose', { method: 'POST',
      body: JSON.stringify({ lead_id: currentLeadId, disposition: label }) })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j.ok) { showSaved(label); logMsg('Outcome saved: ' + label); }
        else { dispOutcomeEl.classList.remove('locked'); logMsg('Dispose failed: ' + (j.error || '')); }
      })
      .catch(function(e) { dispOutcomeEl.classList.remove('locked'); logMsg('Dispose failed: ' + e); });
  };

  // Lead status -> sets cold/warm/hot (direct status field update).
  window.setLeadStatus = function(status) {
    if (!currentLeadId) return;
    dispStatusEl.classList.add('locked');    // one status per call
    fetch('/dialer/status', { method: 'POST',
      body: JSON.stringify({ lead_id: currentLeadId, status: status }) })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j.ok) { showSaved(status.toUpperCase()); logMsg('Status set: ' + status); }
        else { dispStatusEl.classList.remove('locked'); logMsg('Status failed: ' + (j.error || '')); }
      })
      .catch(function(e) { dispStatusEl.classList.remove('locked'); logMsg('Status failed: ' + e); });
  };

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
    if (call || !device) return;
    lastSingleParams = lead;
    dialStartTime = Date.now();
    if (!isRetry) { retriedThisDial = false; }
    hideDisposition();
    currentLeadId = lead.lead_id || null;   // null for manual quick-dials
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
    try {
      var c = await device.connect({ params: params });
      wireCall(c);
    } catch (e) {
      setStatus('Connect failed: ' + (e.message || e), 'error');
      logMsg('Connect failed: ' + (e.message || e));
      onCallEnded();
    }
  }

  // ── Multi-line dial ───────────────────────────────────────────────────
  async function joinMulti(data) {
    if (call || !device) return;
    hideDisposition();
    currentLeadId = null;
    lastDialTs = data.ts;
    currentBatchId = data.batch_id;
    nameEl.textContent = data.lines + ' lines ringing...';
    phoneEl.textContent = '';
    timerEl.textContent = '00:00';
    setStatus('Ringing ' + data.lines + ' leads — waiting for first answer', 'ringing');
    btnCall.disabled = true;
    btnHangup.disabled = false;
    btnMute.disabled = false;
    logMsg('Multi-line: joining conference ' + data.conference);
    try {
      var c = await device.connect({ params: { conference: data.conference } });
      wireCall(c);
    } catch (e) {
      setStatus('Connect failed: ' + (e.message || e), 'error');
      logMsg('Connect failed: ' + (e.message || e));
      onCallEnded();
      return;
    }

    if (batchPollInterval) clearInterval(batchPollInterval);
    batchPollInterval = setInterval(function() {
      fetch('/dialer/batch/' + currentBatchId)
        .then(function(r) { return r.json(); })
        .then(function(b) {
          if (b.status === 'connected' && b.winner_name) {
            nameEl.textContent = b.winner_name;
            phoneEl.textContent = b.winner_phone || '';
            currentLeadId = b.winner || null;   // dispose the winning lead
            loadScriptForLead(currentLeadId);   // fill script for the person who answered
            setStatus('Connected — ' + b.winner_name, 'connected');
            logMsg('Connected to ' + b.winner_name);
            startTimer();
            clearInterval(batchPollInterval);
            batchPollInterval = null;
          } else if (b.status === 'no_answer') {
            nameEl.textContent = 'No one answered';
            phoneEl.textContent = '';
            setStatus('All ' + b.total + ' lines — no answer', 'no-answer');
            logMsg('No one answered (' + b.total + ' leads)');
            clearInterval(batchPollInterval);
            batchPollInterval = null;
            if (device) device.disconnectAll();
          }
        })
        .catch(function() {});
    }, 500);
  }

  function hangUp() {
    if (device) device.disconnectAll();
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

  // ── Poll /dialer/current for new leads / multi-batches ────────────────
  function pollForLead() {
    fetch('/dialer/current')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data || !data.ts) return;
        if (data.ts === lastDialTs) return;
        if (call) return;
        if (!deviceReady) return;
        if (data.mode === 'multi') { joinMulti(data); }
        else if (data.lead_id || data.to_number) { dialSingle(data); }
      })
      .catch(function() {});
  }

  setInterval(pollForLead, 500);

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
      fetch('/voice/token').then(function(r) { return r.json(); }).then(function(d) {
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
  logMsg('Fetching access token...');
  fetch('/voice/token')
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
