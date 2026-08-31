param(
    [ValidateSet('Loopback','Lan')][string]$Mode = 'Loopback',
    [switch]$SkipHttp
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'roadshow_common.ps1')
$workspace = Get-MedTrustWorkspace
$failures = [System.Collections.Generic.List[string]]::new()

try {
    $docker = @(docker info --format '{{.ServerVersion}}' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $docker.Count -eq 0) { throw 'Docker Desktop is unavailable.' }
    $storage = Assert-MedTrustLocalStorage
    $state = Get-RoadshowState -Workspace $workspace
    $expected = Get-RoadshowExpectedState
    foreach ($failure in @(Test-RoadshowState -State $state -Expected $expected)) {
        $failures.Add($failure)
    }

    $manifestPath = Join-Path $workspace 'docs\roadshow\ROADSHOW-STATE-MANIFEST.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        $failures.Add('Roadshow State Manifest is missing.')
    }
    else {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($manifest.manifest_digest -notmatch '^sha256:[0-9a-f]{64}$') {
            $failures.Add('Manifest digest is invalid.')
        }
        if ($manifest.minio_object_count -ne $state.storage.object_count) {
            $failures.Add('Manifest MinIO count differs from canonical state.')
        }
        if ($manifest.audit_head_digest -ne $state.audit.head_digest) {
            $failures.Add('Manifest audit head differs from canonical state.')
        }
    }

    $postgresUsers = @(docker ps --filter "volume=$($storage.PostgresVolume)" --format '{{.Names}}')
    if ($postgresUsers.Count -ne 1 -or $postgresUsers[0] -ne 'medtrust-space-postgres-1') {
        $failures.Add("Canonical PostgreSQL single-writer check failed: $($postgresUsers -join ', ')")
    }
    if ($Mode -eq 'Loopback') {
        $infraPorts = @(5432,9000,9001)
        foreach ($port in $infraPorts) {
            $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
            if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -notin @('127.0.0.1','::1')) {
                $failures.Add("Infrastructure port $port is not loopback-only.")
            }
        }
    }

    if (-not $SkipHttp) {
        foreach ($uri in @(
            'http://127.0.0.1:8000/api/v1/health/ready',
            'http://127.0.0.1:5173/roadshow'
        )) {
            try {
                $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5
                if ($response.StatusCode -ne 200) { $failures.Add("$uri did not return 200.") }
            }
            catch { $failures.Add("$uri is unavailable.") }
        }
        $services = @(Get-RoadshowServiceState -Workspace $workspace)
        foreach ($name in @('backend','frontend','outbox-dispatcher','execution-coordinator','callback-worker')) {
            $service = $services | Where-Object Name -eq $name | Select-Object -First 1
            if ($null -eq $service -or $service.State -ne 'managed') {
                $failures.Add("Service $name is not managed and running.")
            }
        }
    }

    $driveC = Get-Volume -DriveLetter C
    $driveD = Get-Volume -DriveLetter D
    if ($driveD.SizeRemaining -lt 5GB) { $failures.Add('D drive has less than 5 GB free.') }
    Write-Host "mode=$Mode"
    Write-Host "commit=$(git -C $workspace rev-parse --short HEAD)"
    Write-Host "alembic=$($state.alembic_head)"
    Write-Host "datasets=$($state.counts.external_dataset_records)"
    Write-Host "models=$($state.counts.external_model_records)"
    Write-Host "relations=$($state.counts.relations)"
    Write-Host "verified=$($state.status_counts.verified_evidences)"
    Write-Host "compute_runs=$($state.counts.compute_runs)"
    Write-Host "minio_objects=$($state.storage.object_count)"
    Write-Host "audit_valid=$($state.audit.chain_valid)"
    Write-Host "disk_c_free_bytes=$($driveC.SizeRemaining)"
    Write-Host "disk_d_free_bytes=$($driveD.SizeRemaining)"
}
catch {
    $failures.Add($_.Exception.Message)
}

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) { Write-Host "FAIL: $failure" -ForegroundColor Red }
    Write-Host 'roadshow_ready=false' -ForegroundColor Red
    exit 1
}
Write-Host 'roadshow_ready=true' -ForegroundColor Green
