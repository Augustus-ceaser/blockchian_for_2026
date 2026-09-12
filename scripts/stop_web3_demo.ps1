param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$workspace = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$pidFile = Join-Path $workspace '.runtime\web3-demo-process.json'
if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host 'No MedTrust Web3 demo node is recorded.'
    exit 0
}

$record = Get-Content -LiteralPath $pidFile -Raw -Encoding utf8 | ConvertFrom-Json
if (
    $null -eq $record.pid -or
    [string]::IsNullOrWhiteSpace([string]$record.hardhatCli) -or
    [System.IO.Path]::GetFullPath([string]$record.workspace) -ne $workspace
) {
    throw 'The Web3 PID record is invalid; no process was stopped.'
}

$processId = [int]$record.pid
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "Removed stale Web3 demo PID record for PID $processId." -ForegroundColor Yellow
    exit 0
}

$expectedCli = [System.IO.Path]::GetFullPath([string]$record.hardhatCli)
$commandLine = [string]$process.CommandLine
if (
    $process.Name -ne 'node.exe' -or
    -not $commandLine.Contains($expectedCli, [System.StringComparison]::OrdinalIgnoreCase) -or
    $commandLine -notmatch '(?:^|\s)node(?:\s|$)'
) {
    throw "PID $processId is not the recorded Hardhat node; no process was stopped."
}

$allProcesses = @(Get-CimInstance Win32_Process)
$descendants = [System.Collections.Generic.List[int]]::new()
$frontier = [System.Collections.Generic.Queue[int]]::new()
$frontier.Enqueue($processId)
while ($frontier.Count -gt 0) {
    $parent = $frontier.Dequeue()
    foreach ($child in @($allProcesses | Where-Object { $_.ParentProcessId -eq $parent })) {
        $childId = [int]$child.ProcessId
        $descendants.Add($childId)
        $frontier.Enqueue($childId)
    }
}

$targets = @($descendants.ToArray())
[array]::Reverse($targets)
$targets += $processId
foreach ($target in $targets) {
    if ($null -ne (Get-Process -Id $target -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $target -Force -ErrorAction Stop
    }
}
foreach ($target in $targets) {
    try {
        Wait-Process -Id $target -Timeout 10 -ErrorAction Stop
    }
    catch {
        if ($null -ne (Get-Process -Id $target -ErrorAction SilentlyContinue)) {
            throw "Web3 demo process $target did not stop within 10 seconds."
        }
    }
}

Remove-Item -LiteralPath $pidFile -Force
Write-Host "Stopped the MedTrust Web3 demo node (PID $processId)." -ForegroundColor Green
