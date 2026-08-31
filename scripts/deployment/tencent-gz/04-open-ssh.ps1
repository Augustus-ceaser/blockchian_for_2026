[CmdletBinding()]
param()
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$configPath = Join-Path $repo ".deploy\tencent-gz.local.ps1"
if (-not (Test-Path -LiteralPath $configPath)) { throw "Missing ignored local config." }
. $configPath
ssh -p $ServerPort -- "${ServerUser}@${ServerHost}"
