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
from datetime import datetime
from pathlib import Path

import requests as _http
from flask import Flask, request, Response, jsonify
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=True)

from crm_engine import db

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

# In-memory map: CallSid → lead_id, used to attach recordings to the right lead
_call_sid_to_lead: dict[str, int] = {}


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
    Reads lead_id + caller_id from the custom params and bridges to the lead."""
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


@app.route("/softphone")
def softphone_page():
    """Serve the browser softphone UI. Loaded in an iframe by the Streamlit dialer."""
    lead_id = request.args.get("lead_id", "")
    caller_id = request.args.get("caller_id", "")
    lead_name = request.args.get("lead_name", "")
    lead_phone = request.args.get("lead_phone", "")
    autoconnect = request.args.get("autoconnect", "0") == "1"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#fafbfa; padding:12px; }}
  .status {{ text-align:center; padding:10px; font-size:14px; color:#555;
             border-radius:8px; margin-bottom:10px; background:#f0f0f0; }}
  .status.connecting {{ background:#fff3cd; color:#856404; }}
  .status.ringing {{ background:#fff3cd; color:#856404; }}
  .status.connected {{ background:#d4edda; color:#155724; }}
  .status.ended {{ background:#f0f0f0; color:#555; }}
  .status.error {{ background:#f8d7da; color:#721c24; }}
  .lead-info {{ text-align:center; margin-bottom:12px; }}
  .lead-name {{ font-size:16px; font-weight:700; color:#1a1a1a; }}
  .lead-phone {{ font-size:13px; color:#666; }}
  .timer {{ text-align:center; font-size:24px; font-weight:700; color:#1a1a1a;
            margin:8px 0; font-variant-numeric:tabular-nums; }}
  .buttons {{ display:flex; gap:8px; justify-content:center; flex-wrap:wrap; }}
  .btn {{ padding:10px 20px; border:none; border-radius:8px; font-size:14px;
          font-weight:600; cursor:pointer; transition:all 0.15s; }}
  .btn:hover {{ transform:scale(1.03); }}
  .btn:disabled {{ opacity:0.4; cursor:not-allowed; transform:none; }}
  .btn-call {{ background:#28a745; color:#fff; }}
  .btn-hangup {{ background:#dc3545; color:#fff; }}
  .btn-mute {{ background:#6c757d; color:#fff; }}
  .btn-mute.active {{ background:#ffc107; color:#000; }}
</style>
</head><body>
<div class="lead-info">
  <div class="lead-name">{lead_name}</div>
  <div class="lead-phone">{lead_phone}</div>
</div>
<div class="timer" id="timer">00:00</div>
<div class="status" id="status">Loading softphone...</div>
<div class="buttons">
  <button class="btn btn-call" id="btnCall" disabled>📞 Call</button>
  <button class="btn btn-mute" id="btnMute" disabled>🔇 Mute</button>
  <button class="btn btn-hangup" id="btnHangup" disabled>📕 Hang Up</button>
</div>

<script src="https://sdk.twilio.com/js/client/releases/1.14/twilio.min.js"></script>
<script>
(function() {{
  const statusEl = document.getElementById('status');
  const timerEl  = document.getElementById('timer');
  const btnCall  = document.getElementById('btnCall');
  const btnMute  = document.getElementById('btnMute');
  const btnHangup= document.getElementById('btnHangup');

  let connection = null;
  let muted = false;
  let timerInterval = null;
  let callStart = null;

  function setStatus(text, cls) {{
    statusEl.textContent = text;
    statusEl.className = 'status ' + (cls || '');
  }}

  function updateTimer() {{
    if (!callStart) return;
    const secs = Math.floor((Date.now() - callStart) / 1000);
    const m = String(Math.floor(secs / 60)).padStart(2, '0');
    const s = String(secs % 60).padStart(2, '0');
    timerEl.textContent = m + ':' + s;
  }}

  function startTimer() {{
    callStart = Date.now();
    timerInterval = setInterval(updateTimer, 1000);
    updateTimer();
  }}

  function stopTimer() {{
    clearInterval(timerInterval);
    timerInterval = null;
  }}

  function makeCall() {{
    if (connection) return;
    setStatus('Connecting...', 'connecting');
    btnCall.disabled = true;
    btnHangup.disabled = false;
    btnMute.disabled = false;
    connection = Twilio.Device.connect({{
      lead_id: '{lead_id}',
      caller_id: '{caller_id}'
    }});
  }}

  function hangUp() {{
    Twilio.Device.disconnectAll();
  }}

  function toggleMute() {{
    if (!connection) return;
    muted = !muted;
    connection.mute(muted);
    btnMute.textContent = muted ? '🔊 Unmute' : '🔇 Mute';
    btnMute.classList.toggle('active', muted);
  }}

  btnCall.addEventListener('click', makeCall);
  btnHangup.addEventListener('click', hangUp);
  btnMute.addEventListener('click', toggleMute);

  // Fetch token and init
  setStatus('Fetching token...', '');
  fetch('/voice/token')
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{
        setStatus(data.error, 'error');
        return;
      }}
      Twilio.Device.setup(data.token, {{ debug: false }});
    }})
    .catch(err => setStatus('Token fetch failed: ' + err, 'error'));

  Twilio.Device.ready(function() {{
    setStatus('Ready to call', '');
    btnCall.disabled = false;
    {'setTimeout(makeCall, 300);' if autoconnect else ''}
  }});

  Twilio.Device.connect(function(conn) {{
    connection = conn;
    setStatus('Connected', 'connected');
    startTimer();
  }});

  Twilio.Device.disconnect(function() {{
    connection = null;
    muted = false;
    stopTimer();
    setStatus('Call ended', 'ended');
    btnCall.disabled = false;
    btnHangup.disabled = true;
    btnMute.disabled = true;
    btnMute.textContent = '🔇 Mute';
    btnMute.classList.remove('active');
  }});

  Twilio.Device.error(function(err) {{
    setStatus('Error: ' + err.message, 'error');
    connection = null;
    stopTimer();
    btnCall.disabled = false;
    btnHangup.disabled = true;
    btnMute.disabled = true;
  }});
}})();
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
    print("Twilio webhook server running on http://localhost:5001")
    print("Expose with: ngrok http 5001")
    print("Then set Twilio inbound webhook to: https://<ngrok-url>/twilio/inbound")
    app.run(host="0.0.0.0", port=5001, debug=False)
