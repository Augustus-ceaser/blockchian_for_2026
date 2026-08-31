param()

$ErrorActionPreference = 'Stop'

function ConvertFrom-SecureValue {
    param([Parameter(Mandatory)][Security.SecureString]$Value)

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Set-EnvironmentEntry {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )

    $lines = if (Test-Path -LiteralPath $Path -PathType Leaf) {
        @(Get-Content -LiteralPath $Path -Encoding utf8)
    }
    else {
        @()
    }
    $replacement = "$Name=$Value"
    $updated = [System.Collections.Generic.List[string]]::new()
    $found = $false
    foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Name))=") {
            if (-not $found) {
                $updated.Add($replacement)
                $found = $true
            }
            continue
        }
        $updated.Add($line)
    }
    if (-not $found) {
        if ($updated.Count -gt 0 -and $updated[$updated.Count - 1] -ne '') {
            $updated.Add('')
        }
        $updated.Add($replacement)
    }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Set-Content -LiteralPath $Path -Encoding utf8 -Value $updated
}

$firstSecure = Read-Host 'Enter the shared local demo password' -AsSecureString
$secondSecure = Read-Host 'Enter the password again to confirm' -AsSecureString
$first = ConvertFrom-SecureValue -Value $firstSecure
$second = ConvertFrom-SecureValue -Value $secondSecure
try {
    if ($first -cne $second) {
        throw 'The two password entries do not match.'
    }
    if ($first.Length -lt 12 -or $first.Length -gt 128) {
        throw 'The password must contain 12 to 128 characters.'
    }
    if ($first -notmatch '^[A-Za-z0-9!@#$%^&*._+\-]+$') {
        throw 'Use only letters, numbers, and the characters !@#$%^&*._+-.'
    }

    $workspace = Split-Path -Parent $PSScriptRoot
    Set-EnvironmentEntry `
        -Path (Join-Path $workspace 'config\phase4-demo.env') `
        -Name 'MEDTRUST_LOCAL_DEMO_PASSWORD' `
        -Value $first
    Set-EnvironmentEntry `
        -Path (Join-Path $workspace 'backend\.env.local') `
        -Name 'MEDTRUST_LOCAL_DEMO_PASSWORD' `
        -Value $first

    Write-Host 'The local demo password was written to Git-ignored configuration files.' -ForegroundColor Green
    Write-Host 'Next: .\scripts\prepare_roadshow.ps1'
}
finally {
    $first = $null
    $second = $null
}
