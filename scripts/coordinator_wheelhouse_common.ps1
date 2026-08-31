$ErrorActionPreference = 'Stop'

function Get-CoordinatorWorkspace {
    return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

function Get-CoordinatorPython([string]$Workspace) {
    $candidate = Join-Path $Workspace 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw 'Backend Python environment is required.'
    }
    & $candidate -c 'import packaging' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Backend Python requires the packaging module.' }
    return $candidate
}

function Get-CoordinatorWheelhouse([string]$Workspace) {
    return Join-Path $Workspace '.cache\coordinator-wheelhouse\staging'
}

function Assert-LinuxAmd64Docker {
    $architecture = (& docker info --format '{{.Architecture}}').Trim()
    $osType = (& docker info --format '{{.OSType}}').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Docker Engine is unavailable.' }
    if ($architecture -notin @('x86_64', 'amd64') -or $osType -ne 'linux') {
        throw "Coordinator runtime requires linux/amd64; found $osType/$architecture."
    }
}

function Test-CoordinatorWheelhouse(
    [string]$Workspace,
    [string]$Wheelhouse,
    [string]$Python
) {
    $manifest = Join-Path $Workspace 'backend\requirements\coordinator-wheel-manifest.json'
    if (-not (Test-Path -LiteralPath $Wheelhouse -PathType Container)) { return $false }
    & $Python (Join-Path $Workspace 'scripts\verify_coordinator_wheelhouse.py') `
        --wheelhouse $Wheelhouse `
        --manifest $manifest
    return $LASTEXITCODE -eq 0
}

function Remove-CoordinatorDirectory([string]$Path, [string]$AllowedRoot) {
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($AllowedRoot)
    if (-not $resolvedPath.StartsWith(
        $resolvedRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove path outside the allowed root: $resolvedPath"
    }
    if (Test-Path -LiteralPath $resolvedPath) {
        Remove-Item -LiteralPath $resolvedPath -Recurse -Force
    }
}
