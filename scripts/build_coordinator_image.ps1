param(
    [string]$Image = 'medtrust-space-coordinator:phase5.10'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'coordinator_wheelhouse_common.ps1')

$workspace = Get-CoordinatorWorkspace
$python = Get-CoordinatorPython $workspace
$wheelhouse = Get-CoordinatorWheelhouse $workspace
Assert-LinuxAmd64Docker
if (-not (Test-CoordinatorWheelhouse $workspace $wheelhouse $python)) {
    throw 'Coordinator wheelhouse is missing or invalid.'
}

$cacheRoot = Join-Path $workspace '.cache'
$context = Join-Path $cacheRoot 'coordinator-build-context'
Remove-CoordinatorDirectory $context $cacheRoot
try {
    New-Item -ItemType Directory -Force -Path `
        (Join-Path $context 'backend\requirements'), `
        (Join-Path $context 'wheelhouse'), `
        (Join-Path $context 'registered_assets'), `
        (Join-Path $context 'smoke_test_plans') | Out-Null
    Copy-Item -LiteralPath (Join-Path $workspace 'backend\requirements\coordinator-runtime.lock') `
        -Destination (Join-Path $context 'backend\requirements\coordinator-runtime.lock')
    Copy-Item -LiteralPath (Join-Path $workspace 'backend\app') `
        -Destination (Join-Path $context 'backend\app') -Recurse
    Copy-Item -Path (Join-Path $workspace 'registered_assets\*') `
        -Destination (Join-Path $context 'registered_assets') -Recurse
    Copy-Item -Path (Join-Path $workspace 'smoke_test_plans\*') `
        -Destination (Join-Path $context 'smoke_test_plans') -Recurse
    Copy-Item -Path (Join-Path $wheelhouse '*.whl') `
        -Destination (Join-Path $context 'wheelhouse')
    docker build --network=none --platform=linux/amd64 `
        -f (Join-Path $workspace 'docker\coordinator.Dockerfile') `
        -t $Image `
        $context
    if ($LASTEXITCODE -ne 0) { throw 'Coordinator image build failed.' }
}
finally {
    Remove-CoordinatorDirectory $context $cacheRoot
}
