[CmdletBinding()]
param(
    [int]$LocalPort = 18080,
    [int]$RemoteLoopbackPort = 18080
)
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$configPath = Join-Path $repo ".deploy\tencent-gz.local.ps1"
if (-not (Test-Path -LiteralPath $configPath)) { throw "Missing ignored local config." }
. $configPath
Write-Host "Tunnel only: http://127.0.0.1:$LocalPort"
ssh -N -p $ServerPort -L "${LocalPort}:127.0.0.1:${RemoteLoopbackPort}" -- \
    "${ServerUser}@${ServerHost}"
