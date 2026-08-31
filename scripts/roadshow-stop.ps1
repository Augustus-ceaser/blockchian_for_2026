param()

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'stop_phase4_demo.ps1')

$occupied = @(Get-NetTCPConnection -LocalPort 3000,8000,4173,5173,8080 -State Listen -ErrorAction SilentlyContinue)
if ($occupied.Count -gt 0) {
    $ports = $occupied | ForEach-Object LocalPort | Sort-Object -Unique
    throw "Application ports remain occupied: $($ports -join ', ')"
}
Push-Location $workspace
try {
    $previousIgnoreOrphans = $env:COMPOSE_IGNORE_ORPHANS
    $previousErrorAction = $ErrorActionPreference
    try {
        $env:COMPOSE_IGNORE_ORPHANS = 'True'
        $ErrorActionPreference = 'Continue'
        docker compose up -d postgres minio | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not preserve canonical PostgreSQL and MinIO.' }
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
        $env:COMPOSE_IGNORE_ORPHANS = $previousIgnoreOrphans
    }
}
finally { Pop-Location }
Write-Host 'roadshow_stopped=true'
Write-Host 'application_ports_free=true'
Write-Host 'canonical_postgres=running'
Write-Host 'canonical_minio=running'
