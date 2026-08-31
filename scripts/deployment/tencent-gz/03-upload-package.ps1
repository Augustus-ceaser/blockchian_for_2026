[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$configPath = Join-Path $repo ".deploy\tencent-gz.local.ps1"
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Create ignored local config: $configPath"
}
. $configPath

foreach ($name in "ServerHost", "ServerUser", "ServerPort", "PackagePath", "RemotePath") {
    if (-not (Get-Variable -Name $name -ValueOnly -ErrorAction SilentlyContinue)) {
        throw "Missing local setting: $name"
    }
}

& (Join-Path $PSScriptRoot "02-verify-package.ps1") -PackagePath $PackagePath
$hash = (Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash
Write-Host "Target: $ServerUser@$ServerHost`:$RemotePath"
Write-Host "Package SHA-256: $hash"
$answer = Read-Host "Type UPLOAD to continue"
if ($answer -cne "UPLOAD") { throw "Upload cancelled." }

scp -P $ServerPort -- $PackagePath "${ServerUser}@${ServerHost}:$RemotePath"
if ($LASTEXITCODE -ne 0) { throw "scp failed." }
Write-Host "Upload completed. No remote command was executed."
