param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

$workspace = Get-MedTrustWorkspace
$null = Import-MedTrustDemoEnvironment -Workspace $workspace
$databaseName = Get-MedTrustPhase4DatabaseName
$expectedDatabase = 'medtrust_phase4_demo'
$databaseUrl = Get-MedTrustPhase4DatabaseUrl
$backendPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Backend Python' -EnvironmentVariable 'MEDTRUST_BACKEND_PYTHON' -CommandNames @('python') -FallbackPaths @('backend\.venv\Scripts\python.exe') -ProbeArguments @('-c', 'import uvicorn, sqlalchemy, alembic')
$pidFile = Join-Path $workspace '.runtime\phase4-demo-processes.json'
if ($databaseName -ne $expectedDatabase) { throw 'Refusing to reset an unexpected database.' }
if (Test-Path -LiteralPath $pidFile) { throw 'Stop the Phase 4 demo processes before reset.' }

Push-Location $workspace
try {
    $services = @(Get-MedTrustComposeServices -Workspace $workspace)
    Assert-MedTrustComposeService -Services $services -ServiceName 'postgres'
    Assert-MedTrustComposeService -Services $services -ServiceName 'minio'
    docker compose up -d postgres minio | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not start PostgreSQL and MinIO.' }
    if ($services -contains 'backend') {
        Stop-MedTrustComposeService -Workspace $workspace -ServiceName 'backend' -Services $services
    }
    $postgresContainer = Wait-MedTrustComposeService -Workspace $workspace -ServiceName 'postgres' -Services $services
    $null = Wait-MedTrustComposeService -Workspace $workspace -ServiceName 'minio' -Services $services -ReadyPort 9000
    docker exec $postgresContainer dropdb -U medtrust --if-exists --force $databaseName
    if ($LASTEXITCODE -ne 0) { throw 'The dedicated demo database could not be dropped.' }
    docker exec $postgresContainer createdb -U medtrust $databaseName
    if ($LASTEXITCODE -ne 0) { throw 'The dedicated demo database could not be created.' }
    $env:PYTHONPATH = Join-Path $workspace 'backend'
    $env:MEDTRUST_DATABASE_URL = $databaseUrl
    Push-Location (Join-Path $workspace 'backend')
    try {
        & $backendPython -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed during demo reset.' }
        & $backendPython -m app.tools.prepare_phase4_demo --database-url $databaseUrl --workspace $workspace | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Demo baseline preparation failed.' }
        & $backendPython -m app.tools.clear_phase4_release_bucket --bucket medtrust-phase4-approved-results
        if ($LASTEXITCODE -ne 0) { throw 'Release bucket cleanup failed.' }
        & $backendPython -m app.tools.clear_phase4_release_bucket --bucket medtrust-phase56-quarantined-results
        if ($LASTEXITCODE -ne 0) { throw 'Quarantine bucket cleanup failed.' }
    } finally { Pop-Location }
    Write-Host 'The dedicated demo database, release bucket and quarantine bucket were reset.' -ForegroundColor Green
}
finally { Pop-Location }
