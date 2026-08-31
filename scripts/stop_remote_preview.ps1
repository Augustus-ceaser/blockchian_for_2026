param()

$workspace = Split-Path -Parent $PSScriptRoot
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Push-Location $workspace
try { docker compose -f compose.remote-preview.yml down }
finally { Pop-Location }
Write-Host 'Remote preview stopped; no tunnel process remains.' -ForegroundColor Green
