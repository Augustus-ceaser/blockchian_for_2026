Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $workspace 'config\hospital-connector-alpha.env'
if (Test-Path -LiteralPath $envFile) {
    docker compose --project-name medtrust-hospital-connector-alpha `
        --env-file $envFile -f (Join-Path $workspace 'compose.hospital-connector-alpha.yml') `
        stop
}
Write-Host 'Hospital Connector Alpha applications stopped; local state retained.'
