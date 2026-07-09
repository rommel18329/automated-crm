' ============================================================
'  Launch CRM  —  double-click to start everything.
'  Starts the Tailscale funnels, the supervisor (webhook +
'  dashboard + AMD), and opens the dashboard in your browser.
'  No command prompt to babysit. To STOP: close the small
'  "run_crm" window in your taskbar.
' ============================================================
Set sh = CreateObject("WScript.Shell")
crmDir = "C:\Users\jimen\Desktop\automated-crm"

' 1) Expose webhook (5001) + AMD (5002) publicly. --bg registers a
'    background Tailscale service and returns immediately (run hidden).
sh.Run "tailscale funnel --bg 5001", 0, False
sh.Run "tailscale funnel --bg --set-path /amd 5002", 0, False

' 2) Start the supervisor. It launches + auto-restarts the webhook
'    server, Streamlit, and the AMD server. Minimized (style 7) so it
'    stays out of the way but you can click it and close it to stop.
sh.Run "cmd /c cd /d """ & crmDir & """ && python run_crm.py", 7, False

' 3) Give the services a few seconds to bind, then open the dashboard.
WScript.Sleep 7000
sh.Run "http://localhost:8501", 1, False
