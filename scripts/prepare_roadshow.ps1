param(
    [switch]$Reset,
    [switch]$Open
)

$ErrorActionPreference = 'Stop'

$workspace = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $workspace '.runtime\phase4-demo-processes.json'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

Push-Location $workspace
try {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $dockerCommand) {
        throw 'Docker CLI is unavailable. Install or start Docker Desktop and run prepare_roadshow.ps1 again.'
    }
    $dockerVersion = @(docker info --format '{{.ServerVersion}}' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $dockerVersion.Count -eq 0) {
        throw 'Docker Desktop is not running. Start Docker Desktop and run prepare_roadshow.ps1 again.'
    }
    $services = @(Get-MedTrustComposeServices -Workspace $workspace)
    Assert-MedTrustComposeService -Services $services -ServiceName 'postgres'
    Assert-MedTrustComposeService -Services $services -ServiceName 'minio'

    & (Join-Path $PSScriptRoot 'stop_phase4_demo.ps1')
    $null = Remove-MedTrustStalePidFile -PidFile $pidFile -Workspace $workspace
    $occupied = @(Get-NetTCPConnection -LocalPort 5173,8000 -State Listen -ErrorAction SilentlyContinue)
    if ($occupied.Count -gt 0) {
        throw 'Ports 5173 or 8000 remain occupied by an unmanaged process. Stop that process and retry.'
    }

    docker compose up -d postgres minio | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not start PostgreSQL and MinIO.' }
    $null = Wait-MedTrustComposeService -Workspace $workspace -ServiceName 'postgres' -Services $services
    $null = Wait-MedTrustComposeService -Workspace $workspace -ServiceName 'minio' -Services $services -ReadyPort 9000

    & (Join-Path $PSScriptRoot 'preflight_phase4_demo.ps1')

    if ($Reset) {
        & (Join-Path $PSScriptRoot 'reset_phase4_demo.ps1')
    }

    & (Join-Path $PSScriptRoot 'start_phase4_demo.ps1')

    $backend = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/v1/health/ready' -UseBasicParsing -TimeoutSec 5
    if ($backend.StatusCode -ne 200) { throw 'Backend HTTP health check did not return 200.' }
    $frontend = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/roadshow' -UseBasicParsing -TimeoutSec 5
    if ($frontend.StatusCode -ne 200) { throw 'Frontend roadshow entry did not return 200.' }

    & (Join-Path $PSScriptRoot 'status_roadshow.ps1')

    $cookie = New-MedTrustLocalDemoSession `
        -Username 'operator.demo' `
        -Password (Get-MedTrustDemoPasswordForUsername -Username 'operator.demo')
    $chains = Invoke-MedTrustUtf8Json -Uri 'http://127.0.0.1:8000/api/v1/roadshow-experience/chains' -Cookie $cookie
    $health = Invoke-MedTrustUtf8Json -Uri 'http://127.0.0.1:8000/api/v1/roadshow-experience/health' -Cookie $cookie

    Write-Host 'Roadshow preparation completed.' -ForegroundColor Green
    Write-Host "  Health: $($health.status)"
    Write-Host "  Reset performed: $Reset"
    Write-Host '  Application start performed: True'
    Write-Host '  Boundary: hard_isolation=false; non-clinical engineering demonstration.'
    Write-Host '  Chains:'
    foreach ($chain in $chains.items) {
        $kind = if ($chain.status -eq 'completed') { 'completed backup' } else { 'live chain' }
        Write-Host "    [$kind] $($chain.application_number) $($chain.completed_nodes)/$($chain.total_nodes)"
    }
    Write-Host '  Usernames: hospital.demo, model.demo, requester.demo, operator.demo'
    Write-Host '  Password: configured locally; not printed'
    Write-Host '  Portals:'
    Write-Host '    Hospital: http://127.0.0.1:5173/demo-login'
    Write-Host '    Model provider: http://127.0.0.1:5173/demo-login'
    Write-Host '    Requester: http://127.0.0.1:5173/demo-login'
    Write-Host '    Operator: http://127.0.0.1:5173/demo-login'
    Write-Host '  Roadshow: http://127.0.0.1:5173/roadshow'

    if ($Open) {
        Start-Process 'http://127.0.0.1:5173/roadshow'
    }
}
finally {
    Pop-Location
}
