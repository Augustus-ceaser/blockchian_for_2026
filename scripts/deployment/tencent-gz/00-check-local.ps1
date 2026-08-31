[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Push-Location $repo
try {
    $status = git status --porcelain
    if ($status) { throw "Working tree must be clean before packaging." }
    $branch = git branch --show-current
    if ($branch -ne "deployment/tencent-gz-public-alpha-v014") {
        throw "Unexpected branch: $branch"
    }
    $baseline = git rev-list -n 1 v0.14-hospital-controlled-execution-alpha
    $mergeBase = git merge-base HEAD v0.14-hospital-controlled-execution-alpha
    if ($baseline -ne $mergeBase) { throw "Current branch is not based on v0.14." }
    if (git remote) { throw "This deployment workflow expects no configured remote." }
    git diff --check
    docker compose version
    docker version --format "{{.Server.Version}}"
    Write-Host "Local deployment preflight PASS."
}
finally {
    Pop-Location
}
