param(
    [Parameter(Mandatory)]
    [string]$DatabaseUrl
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

$workspace = Get-MedTrustWorkspace
$null = Import-MedTrustDemoEnvironment -Workspace $workspace
$backendPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Backend Python' -EnvironmentVariable 'MEDTRUST_BACKEND_PYTHON' -CommandNames @('python') -FallbackPaths @('backend\.venv\Scripts\python.exe') -ProbeArguments @('-c', 'import httpx, pytest, minio')
$executorPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Controlled execution Python' -EnvironmentVariable 'MEDTRUST_EXECUTOR_PYTHON' -CommandNames @('python') -FallbackPaths @('.runtime\pathmnist-py312\Scripts\python.exe') -ProbeArguments @('-c', 'import torch, numpy')
$executorRoot = Split-Path -Parent (Split-Path -Parent $executorPython)
$executorSitePackages = Join-Path $executorRoot 'Lib\site-packages'
$dataset = Resolve-MedTrustAsset -Workspace $workspace -Description 'PathMNIST dataset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_DATASET_PATH'
$model = Resolve-MedTrustAsset -Workspace $workspace -Description 'Fixed model asset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_MODEL_PATH'
$runtime = Join-Path $workspace '.runtime\phase57-test-baseline'

New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$env:MEDTRUST_PHASE5_TEST_DATABASE_URL = $DatabaseUrl

Push-Location (Join-Path $workspace 'backend')
try {
    & $backendPython -m pytest tests\integration\test_phase5_execution_readiness_postgresql.py -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) {
        throw 'Phase 5.5 formal baseline preparation failed.'
    }
    & $backendPython -m app.tools.complete_phase57_test_baseline `
        --database-url $DatabaseUrl `
        --dataset-asset $dataset `
        --model-asset $model `
        --repository-root $workspace `
        --executor-site-packages $executorSitePackages
    if ($LASTEXITCODE -ne 0) {
        throw 'Phase 5.7 controlled execution baseline failed.'
    }
}
finally {
    Pop-Location
}

Write-Host 'Phase 5.7 test baseline prepared through Phase 5 APIs and two controlled executions.' -ForegroundColor Green
Write-Host '  Expected: ComputeJob=2, ComputeRun=2, Artifact=2 quarantined.'
