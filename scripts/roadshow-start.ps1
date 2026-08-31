param([ValidateSet('Loopback','Lan')][string]$Mode = 'Loopback')

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'roadshow_common.ps1')
$workspace = Get-MedTrustWorkspace

if ($Mode -eq 'Lan') {
    throw 'LAN startup is intentionally not automated by this seal. Use the existing reviewed Private-LAN procedure without firewall changes.'
}

$existing = @(Get-RoadshowServiceState -Workspace $workspace)
if ($existing.Count -gt 0 -and @($existing | Where-Object State -eq 'managed').Count -eq $existing.Count) {
    & (Join-Path $PSScriptRoot 'roadshow-preflight.ps1') -Mode Loopback
    Write-Host 'Roadshow services were already running; no duplicate processes were started.'
    Write-Host 'URL=http://127.0.0.1:5173/roadshow'
    exit 0
}

& (Join-Path $PSScriptRoot 'roadshow-preflight.ps1') -Mode Loopback -SkipHttp
& (Join-Path $PSScriptRoot 'start_phase4_demo.ps1') -ReadOnly
& (Join-Path $PSScriptRoot 'roadshow-preflight.ps1') -Mode Loopback
Write-Host 'URL=http://127.0.0.1:5173/roadshow'
Write-Host 'Credentials are configured locally and were not printed.'
