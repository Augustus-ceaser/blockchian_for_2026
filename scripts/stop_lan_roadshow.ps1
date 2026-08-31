param([switch]$RemoveFirewall, [switch]$ConfirmedFirewall)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $workspace 'config\lan-roadshow.local.env'
if (Test-Path -LiteralPath $envFile) {
    Push-Location $workspace
    try { docker compose --env-file $envFile -f compose.lan.yml down }
    finally { Pop-Location }
}
Push-Location $workspace
try {
    docker compose up -d postgres minio | Out-Null
} finally { Pop-Location }
if ($RemoveFirewall) {
    & (Join-Path $PSScriptRoot 'configure_lan_firewall.ps1') -Action Remove -Confirmed:$ConfirmedFirewall
}
Write-Host 'LAN roadshow stopped. Volumes were preserved.' -ForegroundColor Green
