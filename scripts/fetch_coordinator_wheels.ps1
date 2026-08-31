$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'coordinator_wheelhouse_common.ps1')

$workspace = Get-CoordinatorWorkspace
$python = Get-CoordinatorPython $workspace
$wheelhouse = Get-CoordinatorWheelhouse $workspace
Assert-LinuxAmd64Docker

if (Test-CoordinatorWheelhouse $workspace $wheelhouse $python) {
    Write-Host 'Verified Coordinator wheelhouse already exists; no download performed.' -ForegroundColor Green
    exit 0
}

$cacheRoot = Join-Path $workspace '.cache\coordinator-wheelhouse'
$temporary = Join-Path $cacheRoot ('download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $temporary | Out-Null
try {
    docker run --rm --platform linux/amd64 `
        -v "${temporary}:/wheelhouse" `
        python:3.12.13-slim-bookworm `
        python -m pip download `
        --no-deps `
        --only-binary=:all: `
        --index-url https://download.pytorch.org/whl/cpu `
        --dest /wheelhouse `
        'torch==2.13.0+cpu'
    if ($LASTEXITCODE -ne 0) { throw 'Official PyTorch CPU wheel download failed.' }

    $torch = Get-ChildItem -LiteralPath $temporary -Filter 'torch-2.13.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl'
    if (@($torch).Count -ne 1) { throw 'The exact audited Torch wheel was not downloaded.' }
    docker run --rm --platform linux/amd64 `
        -v "${temporary}:/wheelhouse" `
        python:3.12.13-slim-bookworm `
        sh -lc "python -m pip download --only-binary=:all: --index-url https://pypi.org/simple --dest /wheelhouse /wheelhouse/$($torch.Name) numpy==2.3.5 psutil==7.2.2 alembic==1.18.5 asyncpg==0.31.0 fastapi==0.139.2 minio==7.2.20 pydantic-settings==2.14.2 PyYAML==6.0.3 SQLAlchemy==2.0.51 uvicorn==0.51.0"
    if ($LASTEXITCODE -ne 0) { throw 'Official PyPI dependency download failed.' }

    $localManifest = Join-Path $temporary 'manifest.json'
    $localLock = Join-Path $temporary 'coordinator-runtime.lock'
    $sums = Join-Path $temporary 'SHA256SUMS'
    & $python (Join-Path $workspace 'scripts\build_coordinator_wheel_manifest.py') `
        --wheelhouse $temporary `
        --manifest $localManifest `
        --lock $localLock `
        --sums $sums
    if ($LASTEXITCODE -ne 0) { throw 'Downloaded wheelhouse validation failed.' }
    $committedManifest = Get-Content (
        Join-Path $workspace 'backend\requirements\coordinator-wheel-manifest.json'
    ) -Raw -Encoding UTF8
    if ((Get-Content $localManifest -Raw -Encoding UTF8) -ne $committedManifest) {
        throw 'Downloaded wheelhouse differs from the committed manifest.'
    }
    Remove-Item -LiteralPath $localManifest, $localLock
    Remove-CoordinatorDirectory $wheelhouse $cacheRoot
    Move-Item -LiteralPath $temporary -Destination $wheelhouse
    Write-Host 'Coordinator wheelhouse downloaded and verified.' -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-CoordinatorDirectory $temporary $cacheRoot
    }
}
