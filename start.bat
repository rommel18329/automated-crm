@echo off
echo Starting Silverline CRM...

:: Expose the webhook publicly (permanent URL). Funnel is a long-lived
:: Tailscale service; the supervisor does not manage it.
start "Funnel" cmd /k "tailscale funnel --bg 5001"

:: Supervisor runs the webhook server + Streamlit and auto-restarts either
:: one if it dies or stops responding (no more 502s from a dead webhook).
cd /d C:\Users\jimen\Desktop\automated-crm
python run_crm.py
