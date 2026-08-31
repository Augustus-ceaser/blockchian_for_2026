param()

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $workspace 'config\lan-roadshow.local.env'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')
$null = Import-MedTrustDemoEnvironment -Workspace $workspace
if ([string]::IsNullOrWhiteSpace($env:MEDTRUST_DEMO_CATALOG_CURATOR_PASSWORD)) {
    $env:MEDTRUST_DEMO_CATALOG_CURATOR_PASSWORD = $env:MEDTRUST_LOCAL_DEMO_PASSWORD
}

$names = @(
    'MEDTRUST_DEMO_HOSPITAL_PASSWORD',
    'MEDTRUST_DEMO_MODEL_PASSWORD',
    'MEDTRUST_DEMO_REQUESTER_PASSWORD',
    'MEDTRUST_DEMO_OPERATOR_PASSWORD',
    'MEDTRUST_DEMO_CATALOG_CURATOR_PASSWORD'
)
foreach ($name in $names) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$name must be configured in the ignored config/phase4-demo.env file."
    }
}

Push-Location $workspace
try {
    docker compose --env-file $envFile -f compose.lan.yml run --rm backend `
        python -m app.tools.update_demo_credentials
    if ($LASTEXITCODE -ne 0) {
        throw 'Demo credential rotation failed.'
    }
}
finally {
    Pop-Location
}

Write-Host 'Local demo credential hashes were configured.' -ForegroundColor Green
