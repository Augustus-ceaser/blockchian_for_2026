param([switch]$Reset, [switch]$ConfigureFirewall, [switch]$ConfirmedFirewall)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $workspace 'config\lan-roadshow.local.env'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')
$null = Import-MedTrustDemoEnvironment -Workspace $workspace
$storage = Assert-MedTrustLocalStorage
Write-Host "Canonical PostgreSQL volume: $($storage.PostgresVolume)"
Write-Host "Canonical MinIO volume: $($storage.MinioVolume)"
$null = Resolve-MedTrustAsset `
    -Workspace $workspace `
    -Description 'Fixed PathMNIST dataset' `
    -EnvironmentVariable 'MEDTRUST_PATHMNIST_DATASET_PATH'
$null = Resolve-MedTrustAsset `
    -Workspace $workspace `
    -Description 'Fixed PathMNIST model' `
    -EnvironmentVariable 'MEDTRUST_PATHMNIST_MODEL_PATH'

& (Join-Path $PSScriptRoot 'stop_phase4_demo.ps1')
$services = @(Get-MedTrustComposeServices -Workspace $workspace)
foreach ($serviceName in 'backend', 'postgres', 'minio') {
    Stop-MedTrustComposeService `
        -Workspace $workspace `
        -ServiceName $serviceName `
        -Services $services
}
if (-not (Test-Path -LiteralPath $envFile)) {
    & (Join-Path $PSScriptRoot 'get_roadshow_network.ps1') -Select
}
if ($Reset) {
    Write-Host 'Reset was explicitly requested; applying the existing official reset before LAN startup.' -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot 'reset_phase4_demo.ps1')
}
Push-Location $workspace
try {
    docker compose --env-file $envFile -f compose.lan.yml up -d postgres minio
    if ($LASTEXITCODE -ne 0) { throw 'LAN PostgreSQL/MinIO startup failed.' }
    docker compose --env-file $envFile -f compose.lan.yml run --rm backend alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'LAN database migration failed.' }
    docker compose --env-file $envFile -f compose.lan.yml up -d --build `
        --wait --wait-timeout 180 `
        backend dispatcher coordinator callback gateway
    if ($LASTEXITCODE -ne 0) { throw 'LAN Compose startup failed.' }
} finally { Pop-Location }
if ($ConfigureFirewall) {
    & (Join-Path $PSScriptRoot 'configure_lan_firewall.ps1') -Action Add -Port 8080 -Confirmed:$ConfirmedFirewall
}
& (Join-Path $PSScriptRoot 'status_lan_roadshow.ps1')
