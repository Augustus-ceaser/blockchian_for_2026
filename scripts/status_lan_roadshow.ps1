param()

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $workspace 'config\lan-roadshow.local.env'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')
$null = Import-MedTrustDemoEnvironment -Workspace $workspace
$storage = Assert-MedTrustLocalStorage
if (-not (Test-Path -LiteralPath $envFile)) { throw 'LAN configuration is missing. Run get_roadshow_network.ps1 -Select first.' }
$values = @{}
Get-Content -LiteralPath $envFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { $values[$matches[1]] = $matches[2] }
}
$origin = $values['MEDTRUST_PUBLIC_ORIGIN']
Push-Location $workspace
try {
    docker compose --env-file $envFile -f compose.lan.yml ps
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read LAN Compose service status.' }
    $databaseStatus = docker compose --env-file $envFile -f compose.lan.yml exec -T postgres `
        psql -U medtrust -d medtrust_phase4_demo -Atc `
        "SELECT (SELECT version_num FROM public.alembic_version)||'|'||(SELECT count(*) FROM medtrust.external_dataset_records)||'|'||(SELECT count(*) FROM medtrust.external_model_records)||'|'||(SELECT count(*) FROM medtrust.data_products);"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($databaseStatus)) {
        throw 'Unable to read canonical database identity.'
    }
    $parts = $databaseStatus.Trim().Split('|')
    Write-Host "PostgreSQL volume: $($storage.PostgresVolume)"
    Write-Host "MinIO volume: $($storage.MinioVolume)"
    Write-Host "Alembic revision: $($parts[0])"
    Write-Host "External datasets: $($parts[1])"
    Write-Host "External models: $($parts[2])"
    Write-Host "Data products: $($parts[3])"
    Write-Host 'Canonical local environment: yes' -ForegroundColor Green
}
finally { Pop-Location }
try {
    $status = Invoke-MedTrustUtf8Json -Uri "$origin/api/v1/health/deployment"
    Write-Host "Mode: $($status.label)" -ForegroundColor Green
    if ($status.demo_credentials -eq 'weak-lan-only') {
        Write-Host 'Demo credentials: weak / LAN-only' -ForegroundColor Yellow
    }
    Write-Host "Join: $origin/join"
    Write-Host "Hospital: $origin/portal/hospital"
    Write-Host "Model provider: $origin/portal/model-provider"
    Write-Host "Requester: $origin/portal/requester"
    Write-Host "Operator: $origin/portal/operator"
} catch { throw "Gateway is not healthy at $origin. $($_.Exception.Message)" }
& (Join-Path $PSScriptRoot 'configure_lan_firewall.ps1') -Action Show
