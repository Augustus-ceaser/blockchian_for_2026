param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

$workspace = Get-MedTrustWorkspace
$pidFile = Join-Path $workspace '.runtime\phase4-demo-processes.json'
if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { Write-Host 'No Phase 4 demo processes are recorded.'; exit 0 }

$entries = @(Get-MedTrustPidEntries -PidFile $pidFile)
$states = @($entries | ForEach-Object {
    Get-MedTrustManagedProcessState -Entry $_ -Workspace $workspace
})
$unsafe = @($states | Where-Object { $_.State -notin @('managed', 'missing') })
if ($unsafe.Count -gt 0) {
    foreach ($state in $unsafe) {
        Write-Host "[FAIL] Refusing to stop PID $($state.Pid) recorded as '$($state.Name)': $($state.Reason)." -ForegroundColor Red
    }
    throw 'The PID file was preserved because one or more processes could not be verified as this project.'
}

foreach ($state in $states) {
    if ($state.State -eq 'missing') {
        Write-Host "Stale PID record confirmed for $($state.Name) (PID $($state.Pid)); no process was stopped." -ForegroundColor Yellow
        continue
    }
    $targets = @(Get-MedTrustDescendantProcessIds -ParentProcessId $state.Pid)
    [array]::Reverse($targets)
    $targets += $state.Pid
    foreach ($targetPid in $targets) {
        if ($null -ne (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
            try {
                Stop-Process -Id $targetPid -Force -ErrorAction Stop
            }
            catch {
                if ($null -ne (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
                    throw
                }
            }
        }
    }
    foreach ($targetPid in $targets) {
        try {
            Wait-Process -Id $targetPid -Timeout 10 -ErrorAction Stop
        }
        catch {
            if ($null -ne (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
                throw "Managed process tree for '$($state.Name)' did not stop within 10 seconds."
            }
        }
    }
    Write-Host "Stopped $($state.Name) process tree (recorded PID $($state.Pid))."
}
Remove-Item -LiteralPath $pidFile -Force
Write-Host 'Phase 4 application processes stopped; PostgreSQL and MinIO remain running.' -ForegroundColor Green
