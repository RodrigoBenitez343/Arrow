
# Restart LoOper Server Script
# This script finds the process listening on port 8000, kills it, and restarts the LoOper API server.

$port = 8000
$process = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique

if ($process) {
    Write-Host "Found process with PID $process listening on port $port. Killing it..."
    Stop-Process -Id $process -Force
    Write-Host "Process killed."
    Start-Sleep -Seconds 2
} else {
    Write-Host "No process found listening on port $port."
}

Write-Host "Starting LoOper API Server..."
# Start the server in a new window so this script can finish
Start-Process python -ArgumentList "OverWatch/launcher.py", "api", "--debug" -WorkingDirectory "$PSScriptRoot"

Write-Host "Server restart initiated. Please wait a few seconds for it to initialize."
