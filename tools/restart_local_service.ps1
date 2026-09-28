# Restart the local server onto the current code.
#
# Needed because Flask does not reload: a server started before an edit keeps
# serving the old code indefinitely, and now that it runs hidden as a
# scheduled task there is no console output to give that away.
#
# Stop-ScheduledTask alone is not enough. It does not reliably take the python
# process down with it, and serve_local.ps1 deliberately exits when something
# is already listening on 5000 -- so a plain stop-then-start can leave the old
# server running while the task reports success. The port has to be cleared
# first, explicitly.
#
#     powershell -ExecutionPolicy Bypass -File tools\restart_local_service.ps1

$ErrorActionPreference = "Stop"
$TaskName = "Crossprint local server"

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) {
    throw "something is still listening on port 5000; not starting a second copy"
}

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 12

# Report the build actually answering, not merely that the task started --
# a commit hash in /healthz is the only thing that proves which code is live.
try {
    (Invoke-WebRequest -Uri "http://127.0.0.1:5000/healthz" -TimeoutSec 15 -UseBasicParsing).Content
} catch {
    throw "restarted, but nothing answered on 127.0.0.1:5000 : $($_.Exception.Message)"
}
