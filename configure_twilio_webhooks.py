"""Set SMS + Voice webhooks on both Twilio numbers to point at the CRM."""
from dotenv import load_dotenv
import os
load_dotenv(".env")

from twilio.rest import Client

sid = os.getenv("TWILIO_ACCOUNT_SID")
token = os.getenv("TWILIO_AUTH_TOKEN")
base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
client = Client(sid, token)

numbers = client.incoming_phone_numbers.list()
for n in numbers:
    n.update(
        sms_url=f"{base}/twilio/inbound",
        sms_method="POST",
        voice_url=f"{base}/voice/incoming",
        voice_method="POST",
    )
    print(f"Configured {n.phone_number}: SMS -> {base}/twilio/inbound | Voice -> {base}/voice/incoming")
