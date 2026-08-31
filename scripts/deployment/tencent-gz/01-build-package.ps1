[CmdletBinding()]
param(
    [string]$OutputDirectory = "D:\MedTrustData\deployment-packages"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
& (Join-Path $PSScriptRoot "00-check-local.ps1")

$commit = (git -C $repo rev-parse HEAD).Trim()
$short = (git -C $repo rev-parse --short=12 HEAD).Trim()
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$baseName = "medtrust-public-alpha-$short-$timestamp"
$packagePath = Join-Path $OutputDirectory "$baseName.tar.gz"
$manifestPath = Join-Path $OutputDirectory "$baseName.files.txt"
$metadataPath = Join-Path $OutputDirectory "$baseName.metadata.json"
$hashPath = "$packagePath.sha256"

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$archivePaths = @(
    ".dockerignore",
    ".gitattributes",
    "backend",
    "frontend",
    "gateway",
    "deploy/tencent-gz-public-alpha",
    "docs/deployment",
    "scripts/deployment/tencent-gz"
)
git -C $repo archive --format=tar.gz --output=$packagePath HEAD -- $archivePaths
if ($LASTEXITCODE -ne 0) { throw "git archive failed." }

tar --options hdrcharset=UTF-8 -tzf $packagePath |
    Sort-Object |
    Set-Content -LiteralPath $manifestPath -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw "Package manifest generation failed." }
$hash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash *$([IO.Path]::GetFileName($packagePath))" |
    Set-Content -LiteralPath $hashPath -Encoding ASCII

[pscustomobject]@{
    package = $packagePath
    sha256 = $hash
    git_commit = $commit
    baseline_tag = "v0.14-hospital-controlled-execution-alpha"
    branch = "deployment/tencent-gz-public-alpha-v014"
    bytes = (Get-Item -LiteralPath $packagePath).Length
    built_at = (Get-Date).ToUniversalTime().ToString("o")
    source = "git archive HEAD"
} | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding UTF8

& (Join-Path $PSScriptRoot "02-verify-package.ps1") -PackagePath $packagePath
Write-Host "Package created: $packagePath"
