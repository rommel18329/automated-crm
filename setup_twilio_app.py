"""One-time setup: create a Twilio TwiML App and API Key for browser calling.

Run this once:  python setup_twilio_app.py

It creates:
  1. A TwiML App (tells Twilio where to POST when the browser dials out)
  2. An API Key + Secret (used to mint short-lived access tokens for the browser)

Both are saved to .env so the webhook server picks them up on next restart.
"""
from dotenv import load_dotenv, set_key
import os

load_dotenv(".env")

from twilio.rest import Client

sid = os.getenv("TWILIO_ACCOUNT_SID")
token = os.getenv("TWILIO_AUTH_TOKEN")
base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
client = Client(sid, token)

print("Creating TwiML App...")
app = client.applications.create(
    friendly_name="CRM Browser Dialer",
    voice_url=f"{base}/voice/browser-connect",
    voice_method="POST",
)
print(f"  TwiML App SID: {app.sid}")

print("Creating API Key...")
key = client.new_keys.create(friendly_name="CRM Browser Dialer Key")
print(f"  API Key SID:    {key.sid}")
print(f"  API Secret:     {key.secret}")

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
set_key(env_path, "TWILIO_TWIML_APP_SID", app.sid)
set_key(env_path, "TWILIO_API_KEY", key.sid)
set_key(env_path, "TWILIO_API_SECRET", key.secret)

print(f"\nSaved to {env_path}:")
print(f"  TWILIO_TWIML_APP_SID={app.sid}")
print(f"  TWILIO_API_KEY={key.sid}")
print(f"  TWILIO_API_SECRET={key.secret}")
print("\nRestart webhook_server.py to pick up the new settings.")
