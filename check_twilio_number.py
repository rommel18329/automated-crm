from dotenv import load_dotenv
import os
load_dotenv(".env")

sid = os.getenv("TWILIO_ACCOUNT_SID", "")
token = os.getenv("TWILIO_AUTH_TOKEN", "")
from_num = os.getenv("TWILIO_FROM_NUMBER", "")
print(f"Configured FROM number: {from_num}")

from twilio.rest import Client
client = Client(sid, token)

numbers = client.incoming_phone_numbers.list()
print(f"\nNumbers on account ({len(numbers)} total):")
for n in numbers:
    caps = n.capabilities
    print(f"  {n.phone_number}  |  SMS: {caps['sms']}  Voice: {caps['voice']}  MMS: {caps['mms']}  |  {n.friendly_name or '(no label)'}")

# Also check account status
account = client.api.accounts(sid).fetch()
print(f"\nAccount status: {account.status}")
