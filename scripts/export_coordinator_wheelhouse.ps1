param(
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'coordinator_wheelhouse_common.ps1')

$workspace = Get-CoordinatorWorkspace
$python = Get-CoordinatorPython $workspace
$wheelhouse = Get-CoordinatorWheelhouse $workspace
if (-not (Test-CoordinatorWheelhouse $workspace $wheelhouse $python)) {
    throw 'Coordinator wheelhouse is missing or invalid.'
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $workspace '.cache\coordinator-wheelhouse-export.zip'
}
$temporary = Join-Path $workspace ('.cache\coordinator-export-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path (Join-Path $temporary 'wheelhouse') | Out-Null
try {
    Copy-Item -Path (Join-Path $wheelhouse '*.whl') -Destination (Join-Path $temporary 'wheelhouse')
    Copy-Item -LiteralPath (Join-Path $wheelhouse 'SHA256SUMS') -Destination (Join-Path $temporary 'wheelhouse\SHA256SUMS')
    Copy-Item -LiteralPath (Join-Path $workspace 'backend\requirements\coordinator-wheel-manifest.json') -Destination $temporary
    Copy-Item -LiteralPath (Join-Path $workspace 'backend\requirements\coordinator-runtime.in') -Destination $temporary
    Copy-Item -LiteralPath (Join-Path $workspace 'backend\requirements\coordinator-runtime.lock') -Destination $temporary
    if (Test-Path -LiteralPath $OutputPath) { Remove-Item -LiteralPath $OutputPath -Force }
    Compress-Archive -Path (Join-Path $temporary '*') -DestinationPath $OutputPath -CompressionLevel Optimal
    Write-Host "Exported verified Coordinator wheelhouse: $OutputPath" -ForegroundColor Green
}
finally {
    Remove-CoordinatorDirectory $temporary (Join-Path $workspace '.cache')
}
