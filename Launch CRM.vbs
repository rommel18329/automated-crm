' ============================================================
'  Launch CRM  —  double-click to start everything.
'  Starts the Tailscale funnels, the supervisor (webhook +
'  dashboard + AMD), and opens the dashboard in your browser.
'  No command prompt to babysit. To STOP: close the small
'  "run_crm" window in your taskbar.
' ============================================================
Set sh = CreateObject("WScript.Shell")
crmDir = "C:\Users\jimen\Desktop\automated-crm"

' "Launch CRM.vbs silent" = the login auto-start: start the services but
' do NOT pop open the browser. The Desktop shortcut passes no argument.
silent = (WScript.Arguments.Count > 0)
If silent Then
    If WScript.Arguments(0) <> "silent" Then silent = False
End If

Function RunOut(cmd)
    RunOut = sh.Exec("cmd /c " & cmd).StdOut.ReadAll()
End Function

' 0) Always run the newest code: the folder must be on main. If it is on
'    another branch, switch back to main -- unless there are uncommitted
'    changes to tracked files, in which case leave it and warn.
branch = Trim(RunOut("git -C """ & crmDir & """ branch --show-current"))
If branch <> "main" And branch <> "" Then
    dirty = Trim(RunOut("git -C """ & crmDir & """ status --porcelain --untracked-files=no"))
    If dirty = "" Then
        sh.Run "cmd /c git -C """ & crmDir & """ switch main", 0, True
    ElseIf Not silent Then
        MsgBox "The CRM folder is on branch '" & branch & "' with uncommitted changes, " & _
               "so it was NOT switched to main. It may not be the newest version.", 48, "Launch CRM"
    End If
End If

' Already running? (the supervisor holds port 5099)
running = (InStr(RunOut("netstat -ano | findstr LISTENING | findstr :5099"), ":5099") > 0)
If running Then
    If silent Then WScript.Quit
    ans = MsgBox("The CRM is already running." & vbCrLf & vbCrLf & _
                 "Restart it with the latest code?" & vbCrLf & _
                 "(Takes ~15 seconds and interrupts any dialing. " & _
                 "Choose No to just open the dashboard.)", 36, "Launch CRM")
    If ans = 7 Then
        sh.Run "http://localhost:8501", 1, False
        WScript.Quit
    End If
    sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""Get-CimInstance Win32_Process | " & _
           "Where-Object { $_.CommandLine -match 'python.*run_crm\.py' -and $_.ProcessId -ne $PID } | " & _
           "ForEach-Object { taskkill /T /F /PID $_.ProcessId }""", 0, True
    WScript.Sleep 3000
ElseIf silent Then
    WScript.Sleep 15000   ' at login: let the network + Tailscale come up first
End If

' 1) Expose webhook (5001) + AMD (5002) publicly. --bg registers a
'    background Tailscale service and returns immediately (run hidden).
sh.Run "tailscale funnel --bg 5001", 0, False
sh.Run "tailscale funnel --bg --set-path /amd 5002", 0, False

' 2) Start the supervisor. It launches + auto-restarts the webhook
'    server, Streamlit, and the AMD server. Minimized (style 7) so it
'    stays out of the way but you can click it and close it to stop.
sh.Run "cmd /c cd /d """ & crmDir & """ && python run_crm.py", 7, False

' 3) Give the services a few seconds to bind, then open the dashboard.
If Not silent Then
    WScript.Sleep 7000
    sh.Run "http://localhost:8501", 1, False
End If
