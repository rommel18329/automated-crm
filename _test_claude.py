import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
key = os.getenv("ANTHROPIC_API_KEY", "")
print(f"Key: {key[:20]}...{key[-4:]}" if key else "NOT SET")

import anthropic
c = anthropic.Anthropic(api_key=key)
r = c.messages.create(
    model="claude-haiku-4-5",
    max_tokens=20,
    messages=[{"role": "user", "content": "Say OK"}]
)
print("Claude says:", r.content[0].text)
