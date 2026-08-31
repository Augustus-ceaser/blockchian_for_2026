param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

$workspace = Get-MedTrustWorkspace
$databaseName = 'medtrust_demo'
$dumpPath = Join-Path $workspace 'tmp\medtrust-v0.2-controlled-smoke.dump'
$backendPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Backend Python' -EnvironmentVariable 'MEDTRUST_BACKEND_PYTHON' -CommandNames @('python') -FallbackPaths @('backend\.venv\Scripts\python.exe') -ProbeArguments @('-c', 'import uvicorn, sqlalchemy, alembic')
$databaseUrl = 'postgresql+asyncpg://medtrust:medtrust_dev_only@127.0.0.1:5432/medtrust_demo'

if ($databaseName -ne 'medtrust_demo') { throw 'Refusing to reset an unexpected database.' }
if (-not (Test-Path -LiteralPath $dumpPath -PathType Leaf)) { throw "Frozen demo backup is missing: $dumpPath" }

Push-Location $workspace
try {
    $env:PYTHONPATH = Join-Path $workspace 'backend'
    docker compose up -d postgres minio | Out-Host
    & cmd.exe /d /c "docker compose stop backend >nul 2>&1"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to stop the compose backend service.' }
    docker exec $container dropdb -U medtrust --if-exists --force $databaseName
    docker exec $container createdb -U medtrust $databaseName
    docker cp $dumpPath "${container}:/tmp/medtrust-v0.2-controlled-smoke.dump" | Out-Null
    docker exec $container pg_restore -U medtrust -d $databaseName --no-owner --no-privileges /tmp/medtrust-v0.2-controlled-smoke.dump
    & $backendPython -m app.tools.prepare_pathmnist_demo_baseline --database-url $databaseUrl --run-limit 20
    if ($LASTEXITCODE -ne 0) { throw 'Demo baseline preparation failed.' }
    Write-Host 'Demo database restored to the frozen v0.2 baseline and prepared for a new controlled run.' -ForegroundColor Green
}
finally {
    Pop-Location
}
    $container = (docker compose ps -q postgres).Trim()
    if (-not $container) { throw 'PostgreSQL container is unavailable.' }
