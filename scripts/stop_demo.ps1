param()

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $workspace '.runtime\demo-processes.json'

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host 'No managed demo processes are recorded.'
    exit 0
}

$entries = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
foreach ($entry in $entries) {
    $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-Process -Id $process.Id -Force
        Write-Host "Stopped $($entry.name) (PID $($entry.pid))."
    }
}
Remove-Item -LiteralPath $pidFile -Force
Write-Host 'MedTrust demo application processes stopped. PostgreSQL and MinIO were left running.' -ForegroundColor Green
