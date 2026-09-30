# AI draft instructions and coaching

Approved by user on September 30, 2026 after checkout and Claude-change verification.

## Changes
- `crm_engine/ai_message_generator.py`: prioritize current agent instructions over
  default strategy and old conversation history; prohibit invented appointments;
  separate coaching from seller-facing text and explain wording choices; fix missing
  datetime import and handle timezone-aware and legacy UTC contact dates, ignoring
  internal notes when calculating the contact gap.
- `dashboard.py`: retain and display coaching beneath the shared AI draft controls
  in Messages and lead detail; replace prior coaching on each generation.
- `tests/test_ai_message_generator.py`: date handling, instruction propagation through
  the follow-up wrapper, and Streamlit coaching persistence/replacement.

Existing work checkpoint: `e0b2e43`. No schema, scoring, send, or follow-up schedule changes.

## Validation
- Full suite: `python -m pytest tests/ -q` — 65 passed; six existing UTC deprecation warnings.
- Python compile checks and `git diff --check` passed.
- The Streamlit test executes the shared composer function and verifies coaching
  survives reruns and is cleared when the next result has no coaching.
- A live model check using screenshot conversation text was blocked by automatic
  approval review because it would share private conversation text with Anthropic.
  No external check was performed; model adherence is not live-verified. No messages sent.
