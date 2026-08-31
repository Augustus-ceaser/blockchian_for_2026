param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

$workspace = Get-MedTrustWorkspace
$errors = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$details = [System.Collections.Generic.List[string]]::new()
$dockerCliReady = $false
$dockerReady = $false
$composeServices = @()

function Add-Check {
    param([string]$Name, [scriptblock]$Check, [switch]$WarningOnly)
    try {
        $value = & $Check
        $script:details.Add("[OK] $Name$(if ($null -ne $value -and $value -ne '') { ": $value" })")
    } catch {
        $message = "${Name}: $($_.Exception.Message)"
        if ($WarningOnly) { $script:warnings.Add($message) } else { $script:errors.Add($message) }
    }
}

$configPath = Import-MedTrustDemoEnvironment -Workspace $workspace
$details.Add("[INFO] PowerShell execution policy: $(Get-ExecutionPolicy)")
$details.Add("[INFO] Local config: $(if ($configPath) { 'loaded' } else { 'not found; process environment only' })")

Add-Check 'Docker CLI' {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $dockerCommand) {
        throw 'Docker CLI is unavailable. Install or start Docker Desktop and retry.'
    }
    $script:dockerCliReady = $true
    'available'
}
Add-Check 'Docker Engine' {
    if (-not $script:dockerCliReady) { throw 'Docker CLI is unavailable.' }
    $version = @(docker info --format '{{.ServerVersion}}' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $version.Count -eq 0) {
        throw 'Docker Desktop is not running. Start Docker Desktop and retry.'
    }
    $script:dockerReady = $true
    [string]($version | Select-Object -First 1)
}
Add-Check 'Compose configuration' {
    if (-not $script:dockerReady) { throw 'Docker Engine is unavailable.' }
    Push-Location $workspace
    try {
        docker compose config --quiet
        if ($LASTEXITCODE -ne 0) { throw 'docker compose config failed' }
        $script:composeServices = @(Get-MedTrustComposeServices -Workspace $workspace)
        "valid; services=$($script:composeServices -join ',')"
    } finally { Pop-Location }
}

$backendPython = $null
$executorPython = $null
$node = $null
Add-Check 'Backend Python' {
    $script:backendPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Backend Python' -EnvironmentVariable 'MEDTRUST_BACKEND_PYTHON' -CommandNames @('python') -FallbackPaths @('backend\.venv\Scripts\python.exe') -ProbeArguments @('-c', 'import uvicorn, sqlalchemy, alembic')
    'available'
}
Add-Check 'Executor Python' {
    $script:executorPython = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Executor Python' -EnvironmentVariable 'MEDTRUST_EXECUTOR_PYTHON' -CommandNames @('python') -FallbackPaths @('.runtime\pathmnist-py312\Scripts\python.exe') -ProbeArguments @('-c', 'import torch, numpy')
    'available'
}
Add-Check 'Node.js' {
    $script:node = Resolve-MedTrustExecutable -Workspace $workspace -Description 'Node.js' -EnvironmentVariable 'MEDTRUST_NODE' -CommandNames @('node')
    & $script:node --version
}
Add-Check 'pnpm' {
    $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
    if ($null -eq $pnpmCommand) {
        throw 'pnpm is unavailable. Install pnpm or add it to PATH.'
    }
    & $pnpmCommand.Source --version | Select-Object -First 1
}
Add-Check 'Frontend dependencies' {
    $vite = Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite -PathType Leaf)) { throw 'frontend node_modules is missing; run pnpm install' }
    'available'
}
Add-Check 'PathMNIST dataset' {
    $null = Resolve-MedTrustAsset -Workspace $workspace -Description 'PathMNIST dataset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_DATASET_PATH'
    'available'
}
Add-Check 'Fixed model asset' {
    $null = Resolve-MedTrustAsset -Workspace $workspace -Description 'Fixed model asset' -EnvironmentVariable 'MEDTRUST_PATHMNIST_MODEL_PATH'
    'available'
}
Add-Check 'Local demo password' {
    $null = Get-MedTrustLocalDemoPassword
    'configured; value hidden'
}

$managedPidFile = Join-Path $workspace '.runtime\phase4-demo-processes.json'
$managedStates = @()
$managedProcessIds = [System.Collections.Generic.HashSet[int]]::new()
if (Test-Path -LiteralPath $managedPidFile -PathType Leaf) {
    try {
        $managedStates = @(Get-MedTrustPidEntries -PidFile $managedPidFile | ForEach-Object {
            Get-MedTrustManagedProcessState -Entry $_ -Workspace $workspace
        })
        foreach ($state in $managedStates | Where-Object { $_.State -eq 'managed' }) {
            $null = $managedProcessIds.Add([int]$state.Pid)
            foreach ($descendantPid in @(Get-MedTrustDescendantProcessIds -ParentProcessId ([int]$state.Pid))) {
                $null = $managedProcessIds.Add([int]$descendantPid)
            }
        }
        $managedCount = @($managedStates | Where-Object { $_.State -eq 'managed' }).Count
        $staleCount = @($managedStates | Where-Object { $_.State -eq 'missing' }).Count
        $unsafeCount = @($managedStates | Where-Object { $_.State -notin @('managed', 'missing') }).Count
        if ($managedCount -gt 0) { $details.Add("[INFO] Recorded application processes: $managedCount running") }
        if ($staleCount -gt 0) { $warnings.Add("PID file contains $staleCount stale process record(s). Run .\scripts\stop_phase4_demo.ps1.") }
        if ($unsafeCount -gt 0) { $errors.Add("PID file contains $unsafeCount unverified process record(s); no process will be stopped automatically.") }
    }
    catch {
        $errors.Add("PID file check: $($_.Exception.Message)")
    }
}
foreach ($port in 5173, 8000) {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) {
        $details.Add("[OK] Port ${port}: available")
    } elseif (@($listeners | Where-Object { $managedProcessIds.Contains([int]$_.OwningProcess) }).Count -gt 0) {
        $warnings.Add("Port $port is occupied by a recorded Phase 4 demo process.")
    } else {
        $errors.Add("Port $port is occupied by an unmanaged process.")
    }
}

if ($dockerReady -and $composeServices.Count -gt 0) {
    $requiredServices = @('postgres', 'minio')
    foreach ($serviceName in $requiredServices) {
        try {
            Assert-MedTrustComposeService -Services $composeServices -ServiceName $serviceName
            $containerId = Get-MedTrustComposeContainerId -Workspace $workspace -ServiceName $serviceName -Services $composeServices
            if ([string]::IsNullOrWhiteSpace([string]$containerId)) {
                $errors.Add("Docker Compose service '$serviceName' is not running. Start infrastructure with: docker compose up -d postgres minio")
                continue
            }
            $state = Get-MedTrustContainerState -ContainerId $containerId
            if (-not $state.Running) {
                $errors.Add("Docker Compose service '$serviceName' is not running. Start infrastructure with: docker compose up -d postgres minio")
            }
            elseif ($state.Health -eq 'unhealthy') {
                $errors.Add("Docker Compose service '$serviceName' is unhealthy. Inspect with: docker compose ps -a")
            }
            elseif ($state.Health -eq 'starting') {
                $errors.Add("Docker Compose service '$serviceName' is still starting. Retry preflight after it becomes healthy.")
            }
            else {
                $details.Add("[OK] Docker Compose service '$serviceName': running$(if ($state.Health -ne 'none') { "; health=$($state.Health)" })")
            }
        }
        catch {
            $errors.Add("Docker Compose service '$serviceName': $($_.Exception.Message)")
        }
    }

    $postgresContainer = $null
    try {
        $postgresContainer = Get-MedTrustComposeContainerId -Workspace $workspace -ServiceName 'postgres' -Services $composeServices
    }
    catch {
        $errors.Add("PostgreSQL inspection: $($_.Exception.Message)")
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$postgresContainer) -and -not [string]::IsNullOrWhiteSpace([string]$backendPython)) {
        Add-Check 'Phase 4 Alembic current' {
            $env:MEDTRUST_DATABASE_URL = Get-MedTrustPhase4DatabaseUrl
            Push-Location (Join-Path $workspace 'backend')
            try {
                $output = @(& $backendPython -m alembic current)
                if ($LASTEXITCODE -ne 0 -or $output.Count -eq 0) { throw 'alembic current failed' }
                [string]($output | Select-Object -Last 1)
            } finally { Pop-Location }
        }
    }
}

$details | ForEach-Object { Write-Host $_ }
$warnings | ForEach-Object { Write-Warning $_ }
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Host "[FAIL] $_" -ForegroundColor Red }
    throw "Phase 4 preflight failed with $($errors.Count) error(s)."
}
Write-Host "Phase 4 preflight passed with $($warnings.Count) warning(s)." -ForegroundColor Green
