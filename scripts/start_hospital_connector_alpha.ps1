Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
& (Join-Path $PSScriptRoot 'prepare_hospital_connector_alpha.ps1')
$envFile = Join-Path $workspace 'config\hospital-connector-alpha.env'
docker compose --project-name medtrust-hospital-connector-alpha `
    --env-file $envFile -f (Join-Path $workspace 'compose.hospital-connector-alpha.yml') `
    up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'local_page=http://127.0.0.1:18600/local'
Write-Host 'mtls_ingress=https://127.0.0.1:18443'
