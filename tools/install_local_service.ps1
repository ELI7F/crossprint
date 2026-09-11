# Register (or re-register) the scheduled task that keeps the local converter
# running, so http://127.0.0.1:5000 is simply there after every logon.
#
# Why a scheduled task rather than a Windows service: the server is a Flask
# development server run out of a venv in a user profile, and it only ever
# binds 127.0.0.1. A service would run as SYSTEM, in a different profile,
# with no reason to. "At logon, as me" is what actually matches it.
#
#     powershell -ExecutionPolicy Bypass -File tools\install_local_service.ps1
#
# To remove it:  Unregister-ScheduledTask -TaskName "Crossprint local server"

$ErrorActionPreference = "Stop"

$TaskName = "Crossprint local server"
$launcher = Join-Path $PSScriptRoot "serve_local.ps1"
if (-not (Test-Path $launcher)) { throw "launcher not found: $launcher" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File `"$launcher`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# ExecutionTimeLimit 0 means "no limit". The default is three days, after
# which Windows would kill a server that is behaving perfectly.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description "Serves the Crossprint 3MF converter on http://127.0.0.1:5000 for this user." | Out-Null

"registered: $TaskName"
