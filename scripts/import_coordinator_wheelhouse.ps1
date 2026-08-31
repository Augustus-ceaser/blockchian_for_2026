param(
    [Parameter(Mandatory)]
    [string]$ArchivePath
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'coordinator_wheelhouse_common.ps1')

$workspace = Get-CoordinatorWorkspace
$python = Get-CoordinatorPython $workspace
$cacheRoot = Join-Path $workspace '.cache\coordinator-wheelhouse'
$wheelhouse = Get-CoordinatorWheelhouse $workspace
$archive = (Resolve-Path -LiteralPath $ArchivePath).Path
$temporary = Join-Path $cacheRoot ('import-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $temporary | Out-Null
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $seen = @{}
        foreach ($entry in $zip.Entries) {
            $rawName = $entry.FullName
            $name = $rawName.Replace('/', '\')
            if ([string]::IsNullOrWhiteSpace($name) -or
                [System.IO.Path]::IsPathRooted($name) -or
                $name -match '^[A-Za-z]:' -or
                $name -match '(^|\\)\.\.?(\\|$)') {
                throw "Archive contains an unsafe path: $name"
            }
            $canonical = $name.TrimEnd('\').ToLowerInvariant()
            if ($seen.ContainsKey($canonical)) {
                throw "Archive contains a duplicate path: $name"
            }
            $seen[$canonical] = $true
            $segments = @($name.TrimEnd('\').Split('\'))
            if ($segments | Where-Object { $_.StartsWith('.') }) {
                throw "Archive contains a hidden path: $name"
            }
            $isDirectory = $rawName.EndsWith('/')
            if ($isDirectory) {
                if ($segments.Count -ne 1 -or $segments[0] -ne 'wheelhouse') {
                    throw "Archive contains an unsupported directory: $name"
                }
                continue
            }
            $allowedRootFiles = @(
                'coordinator-wheel-manifest.json',
                'coordinator-runtime.in',
                'coordinator-runtime.lock'
            )
            $allowedWheelhouseFile = (
                $segments.Count -eq 2 -and
                $segments[0] -eq 'wheelhouse' -and
                ($segments[1] -eq 'SHA256SUMS' -or $segments[1].EndsWith('.whl'))
            )
            if (-not ($segments.Count -eq 1 -and $segments[0] -in $allowedRootFiles) -and
                -not $allowedWheelhouseFile) {
                throw "Archive contains an unsupported file: $name"
            }
            if (($entry.ExternalAttributes -band 0xF0000000) -eq 0xA0000000) {
                throw "Archive contains a symbolic link: $name"
            }
        }
    }
    finally {
        $zip.Dispose()
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $temporary
    foreach ($name in 'coordinator-wheel-manifest.json','coordinator-runtime.in','coordinator-runtime.lock') {
        $imported = Join-Path $temporary $name
        $committed = Join-Path $workspace "backend\requirements\$name"
        if (-not (Test-Path -LiteralPath $imported -PathType Leaf) -or
            (Get-FileHash $imported -Algorithm SHA256).Hash -ne
            (Get-FileHash $committed -Algorithm SHA256).Hash) {
            throw "Imported $name does not match the committed file."
        }
    }
    $candidate = Join-Path $temporary 'wheelhouse'
    if (-not (Test-CoordinatorWheelhouse $workspace $candidate $python)) {
        throw 'Imported wheelhouse verification failed.'
    }
    Remove-CoordinatorDirectory $wheelhouse $cacheRoot
    Move-Item -LiteralPath $candidate -Destination $wheelhouse
    Write-Host 'Imported and verified Coordinator wheelhouse.' -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-CoordinatorDirectory $temporary $cacheRoot
    }
}
