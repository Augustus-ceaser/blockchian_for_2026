param([switch]$Reset, [switch]$ReadOnly)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

$workspace = Get-MedTrustWorkspace
$null = Import-MedTrustDemoEnvironment -Workspace $workspace -IncludeLocalOverrides
$runtimeRoot = Join-Path $workspace '.runtime'
$logRoot = Join-Path $runtimeRoot 'phase4-demo-logs'
$pidFile = Join-Path $runtimeRoot 'phase4-demo-processes.json'
$backendPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Backend Python' -EnvironmentVariable 'MEDTRUST_BACKEND_PYTHON' -CommandNames @('python') -FallbackPaths @('backend\.venv\Scripts\python.exe') -ProbeArguments @('-c', 'import uvicorn, sqlalchemy, alembic')
$executorPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Executor Python' -EnvironmentVariable 'MEDTRUST_EXECUTOR_PYTHON' -CommandNames @('python') -FallbackPaths @('.runtime\pathmnist-py312\Scripts\python.exe') -ProbeArguments @('-c', 'import torch, numpy')
$node = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Node.js' -EnvironmentVariable 'MEDTRUST_NODE' -CommandNames @('node')
$vite = Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'
$databaseName = Get-MedTrustPhase4DatabaseName
$databaseUrl = Get-MedTrustPhase4DatabaseUrl
$datasetPath = Resolve-MedTrustAsset -Workspace $workspace -Description 'PathMNIST dataset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_DATASET_PATH'
$modelPath = Resolve-MedTrustAsset -Workspace $workspace -Description 'Fixed model asset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_MODEL_PATH'

foreach ($required in ($vite, $datasetPath, $modelPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required runtime is missing: $required" }
}
$null = Remove-MedTrustStalePidFile -PidFile $pidFile -Workspace $workspace
if (Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Ports 8000 or 5173 are already in use. Stop the existing demo first.'
}
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

Push-Location $workspace
try {
    $services = @(Get-MedTrustComposeServices -Workspace $workspace)
    Assert-MedTrustComposeService -Services $services -ServiceName 'postgres'
    Assert-MedTrustComposeService -Services $services -ServiceName 'minio'
    $previousIgnoreOrphans = $env:COMPOSE_IGNORE_ORPHANS
    $previousErrorAction = $ErrorActionPreference
    try {
        $env:COMPOSE_IGNORE_ORPHANS = 'True'
        $ErrorActionPreference = 'Continue'
        docker compose up -d postgres minio | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not start PostgreSQL and MinIO.' }
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
        $env:COMPOSE_IGNORE_ORPHANS = $previousIgnoreOrphans
    }
    if ($services -contains 'backend') {
        Stop-MedTrustComposeService -Workspace $workspace -ServiceName 'backend' -Services $services
    }
    $postgresContainer = Wait-MedTrustComposeService -Workspace $workspace -ServiceName 'postgres' -Services $services
    $null = Wait-MedTrustComposeService -Workspace $workspace -ServiceName 'minio' -Services $services -ReadyPort 9000
    $existsOutput = @(docker exec $postgresContainer psql -U medtrust -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$databaseName'")
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL database inventory failed.' }
    $exists = [string]($existsOutput | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($exists)) {
        docker exec $postgresContainer createdb -U medtrust $databaseName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The dedicated demo database could not be created.' }
    }
    if ($Reset) { & (Join-Path $PSScriptRoot 'reset_phase4_demo.ps1') }

    $env:PYTHONPATH = Join-Path $workspace 'backend'
    $env:MEDTRUST_DATABASE_URL = $databaseUrl
    if (-not $ReadOnly) {
        Push-Location (Join-Path $workspace 'backend')
        try {
            & $backendPython -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed during demo start.' }
            & $backendPython -m app.tools.prepare_phase4_demo --database-url $databaseUrl --workspace $workspace | Out-Host
            if ($LASTEXITCODE -ne 0) { throw 'Demo baseline preparation failed during start.' }
        } finally { Pop-Location }
    }

    $env:MEDTRUST_DEMO_API_ENABLED = 'true'
    $env:MEDTRUST_CORS_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8080'
    $env:MEDTRUST_OUTBOX_PUBLISHER = 'database_inbox'
    $env:MEDTRUST_EXECUTION_COORDINATOR_PATHMNIST = 'true'
    $env:MEDTRUST_PATHMNIST_DATASET_PATH = $datasetPath
    $env:MEDTRUST_PATHMNIST_MODEL_PATH = $modelPath
    if ([string]::IsNullOrWhiteSpace($env:MEDTRUST_LOCAL_EXECUTOR_WORKSPACE)) {
        $env:MEDTRUST_LOCAL_EXECUTOR_WORKSPACE = Join-Path $runtimeRoot 'phase4-pathmnist-workspaces'
    }
    $env:MEDTRUST_MINIO_ENDPOINT = '127.0.0.1:9000'
    $env:MEDTRUST_MINIO_ACCESS_KEY = 'medtrust'
    $env:MEDTRUST_MINIO_SECRET_KEY = 'medtrust_dev_only'
    $env:MEDTRUST_MINIO_RELEASE_BUCKET = 'medtrust-phase4-approved-results'
    $env:MEDTRUST_MINIO_QUARANTINE_BUCKET = 'medtrust-phase56-quarantined-results'
    $env:VITE_DATA_MODE = 'api'
    $env:VITE_API_BASE_URL = 'http://127.0.0.1:8000/api/v1'

    $processes = @()
    function Start-Phase4Process([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
        $stdout = Join-Path $logRoot "$Name.out.log"
        $stderr = Join-Path $logRoot "$Name.err.log"
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $script:processes += [pscustomobject]@{ name = $Name; pid = $process.Id; stdout = $stdout; stderr = $stderr }
    }

    Start-Phase4Process 'backend' $backendPython @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') (Join-Path $workspace 'backend')
    Start-Phase4Process 'outbox-dispatcher' $backendPython @('-m','app.workers.outbox_dispatcher') (Join-Path $workspace 'backend')
    Start-Phase4Process 'execution-coordinator' $executorPython @('-m','app.workers.execution_coordinator') (Join-Path $workspace 'backend')
    Start-Phase4Process 'callback-worker' $backendPython @('-m','app.workers.execution_callback_worker') (Join-Path $workspace 'backend')
    Start-Phase4Process 'frontend' $node @('node_modules\vite\bin\vite.js','--host','127.0.0.1','--port','5173') (Join-Path $workspace 'frontend')
    $processes | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

    $backendHealthy = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health/ready' -TimeoutSec 2
            if ($response.status -eq 'ok') { $backendHealthy = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $backendHealthy) { throw "Backend did not become ready. Inspect $logRoot" }

    $frontendHealthy = $false
    $frontendFailure = 'no response'
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/demo-login' -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $frontendHealthy = $true; break }
        } catch {
            $frontendFailure = $_.Exception.Message
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $frontendHealthy) { throw "Frontend did not become ready: $frontendFailure. Inspect $logRoot" }
    foreach ($entry in $processes) {
        if ($null -eq (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue)) {
            throw "Managed process '$($entry.name)' exited during startup. Inspect the demo logs."
        }
    }
    Write-Host 'MedTrust Space Phase 4 roadshow is ready:' -ForegroundColor Green
    Write-Host '  Browser: http://127.0.0.1:5173'
    Write-Host '  API docs: http://127.0.0.1:8000/docs'
    Write-Host '  Boundary: hard_isolation=false; not clinical or production certified.'
}
catch {
    if (Test-Path -LiteralPath $pidFile) { & (Join-Path $PSScriptRoot 'stop_phase4_demo.ps1') }
    throw
}
finally { Pop-Location }
