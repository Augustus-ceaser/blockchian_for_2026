param(
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

$workspace = Get-MedTrustWorkspace
$null = Import-MedTrustDemoEnvironment -Workspace $workspace -IncludeLocalOverrides
$runtimeRoot = Join-Path $workspace '.runtime'
$logRoot = Join-Path $runtimeRoot 'demo-logs'
$pidFile = Join-Path $runtimeRoot 'demo-processes.json'
$backendPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Backend Python' -EnvironmentVariable 'MEDTRUST_BACKEND_PYTHON' -CommandNames @('python') -FallbackPaths @('backend\.venv\Scripts\python.exe') -ProbeArguments @('-c', 'import uvicorn, sqlalchemy, alembic')
$executorPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Executor Python' -EnvironmentVariable 'MEDTRUST_EXECUTOR_PYTHON' -CommandNames @('python') -FallbackPaths @('.runtime\pathmnist-py312\Scripts\python.exe') -ProbeArguments @('-c', 'import torch, numpy')
$node = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Node.js' -EnvironmentVariable 'MEDTRUST_NODE' -CommandNames @('node')
$vite = Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'
$databaseUrl = 'postgresql+asyncpg://medtrust:medtrust_dev_only@127.0.0.1:5432/medtrust_demo'
$datasetPath = Resolve-MedTrustAsset -Workspace $workspace -Description 'PathMNIST dataset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_DATASET_PATH'
$modelPath = Resolve-MedTrustAsset -Workspace $workspace -Description 'Fixed model asset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_MODEL_PATH'

foreach ($required in ($vite, $datasetPath, $modelPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required runtime is missing: $required" }
}
if (Test-Path -LiteralPath $pidFile) { throw 'Demo appears to be running. Use scripts\stop_demo.ps1 first.' }
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

Push-Location $workspace
try {
    docker compose up -d postgres minio | Out-Host
    & cmd.exe /d /c "docker compose stop backend >nul 2>&1"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to stop the compose backend service.' }
    if ($Reset) { & (Join-Path $PSScriptRoot 'reset_demo.ps1') }

    $env:PYTHONPATH = Join-Path $workspace 'backend'
    & $backendPython -m app.tools.prepare_pathmnist_demo_baseline --database-url $databaseUrl --run-limit 20 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Demo baseline refresh failed.' }

    $env:MEDTRUST_DATABASE_URL = $databaseUrl
    $env:MEDTRUST_DEMO_API_ENABLED = 'true'
    $env:MEDTRUST_CORS_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
    $env:MEDTRUST_OUTBOX_PUBLISHER = 'database_inbox'
    $env:MEDTRUST_EXECUTION_COORDINATOR_PATHMNIST = 'true'
    $env:MEDTRUST_PATHMNIST_DATASET_PATH = $datasetPath
    $env:MEDTRUST_PATHMNIST_MODEL_PATH = $modelPath
    if ([string]::IsNullOrWhiteSpace($env:MEDTRUST_LOCAL_EXECUTOR_WORKSPACE)) {
        $env:MEDTRUST_LOCAL_EXECUTOR_WORKSPACE = Join-Path $runtimeRoot 'pathmnist-demo-workspaces'
    }
    $env:VITE_DATA_MODE = 'api'
    $env:VITE_API_BASE_URL = 'http://127.0.0.1:8000/api/v1'

    $processes = @()
    function Start-DemoProcess([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
        $stdout = Join-Path $logRoot "$Name.out.log"
        $stderr = Join-Path $logRoot "$Name.err.log"
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $script:processes += [pscustomobject]@{ name = $Name; pid = $process.Id; stdout = $stdout; stderr = $stderr }
    }

    Start-DemoProcess 'backend' $backendPython @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') (Join-Path $workspace 'backend')
    Start-DemoProcess 'dispatcher' $backendPython @('-m','app.workers.outbox_dispatcher') (Join-Path $workspace 'backend')
    Start-DemoProcess 'coordinator-local-executor' $executorPython @('-m','app.workers.execution_coordinator') (Join-Path $workspace 'backend')
    Start-DemoProcess 'callback-worker' $backendPython @('-m','app.workers.execution_callback_worker') (Join-Path $workspace 'backend')
    Start-DemoProcess 'frontend' $node @($vite,'--host','127.0.0.1','--port','5173') (Join-Path $workspace 'frontend')
    $processes | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health/ready' -TimeoutSec 2
            if ($response.status -eq 'ok') { $healthy = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $healthy) { throw "Backend did not become ready. Inspect $logRoot" }
    Write-Host 'MedTrust Space real-data demo is ready:' -ForegroundColor Green
    Write-Host '  Frontend: http://127.0.0.1:5173'
    Write-Host '  API docs: http://127.0.0.1:8000/docs'
    Write-Host 'Artifact release and download remain disabled.'
}
catch {
    if (Test-Path -LiteralPath $pidFile) { & (Join-Path $PSScriptRoot 'stop_demo.ps1') }
    throw
}
finally {
    Pop-Location
}
