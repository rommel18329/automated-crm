param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$taskName = 'Silverline CRM Gmail Sync'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output 'Email synchronization task removed.'
    exit
}
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExe = (Get-Command python).Source
$windowlessPython = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (Test-Path -LiteralPath $windowlessPython) { $pythonExe = $windowlessPython }
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument '-m crm_engine.email_sync' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Refresh Gmail review suggestions every 15 minutes. Never sends email.' -Force | Out-Null
Write-Output "Installed $taskName. Runs while $identity is signed in; catches up when available."
