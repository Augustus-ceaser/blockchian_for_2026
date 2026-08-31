param([switch]$AccessProtectionConfirmed)

$ErrorActionPreference = 'Stop'
if (-not $AccessProtectionConfirmed) { throw 'Remote preview refuses to start until Cloudflare Access protection is explicitly confirmed.' }
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) { throw 'cloudflared is not installed. Remote preview remains a manual gate.' }
$workspace = Split-Path -Parent $PSScriptRoot
$config = Join-Path $workspace 'config\remote-preview.local.yml'
if (-not (Test-Path -LiteralPath $config)) { throw "Missing ignored Cloudflare configuration: $config" }
Push-Location $workspace
try {
    docker compose -f compose.remote-preview.yml up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'Remote-preview origin startup failed.' }
    cloudflared tunnel --config $config run
} finally { Pop-Location }
