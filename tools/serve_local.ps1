# Launcher for the "Crossprint local server" scheduled task.
#
# run_web.ps1 is the thing you run by hand: it takes over a console window and
# opens a browser at the end. Neither is wanted at logon, and a scheduled task
# cannot set an environment variable or guard against a double start on its
# own, so those three differences live here rather than being wired into the
# task's argument string -- where the nested quoting is its own hazard.
#
# Register the task with tools/install_local_service.ps1.

$ErrorActionPreference = "Stop"

# Already serving? Then this is a second copy -- a logon after the server was
# started by hand, say. Flask would fail to bind and the task would look
# broken, so leave the running one alone and say nothing.
$listening = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
if ($listening) { exit 0 }

# run_web.ps1 opens a browser when it starts. Fine when you ran it yourself;
# at every logon it is a tab nobody asked for.
$env:THREEMF_BRIDGE_NO_BROWSER = "1"

& "$PSScriptRoot\..\run_web.ps1"
