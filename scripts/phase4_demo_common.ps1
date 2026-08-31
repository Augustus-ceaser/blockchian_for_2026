Set-StrictMode -Version Latest

function Get-MedTrustWorkspace {
    return (Split-Path -Parent $PSScriptRoot)
}

function Get-MedTrustLocalStorageConfig {
    $postgresVolume = if ([string]::IsNullOrWhiteSpace($env:MEDTRUST_POSTGRES_VOLUME_NAME)) {
        'medtrust-space_postgres_data'
    } else {
        $env:MEDTRUST_POSTGRES_VOLUME_NAME.Trim()
    }
    $minioVolume = if ([string]::IsNullOrWhiteSpace($env:MEDTRUST_MINIO_VOLUME_NAME)) {
        'medtrust-space_minio_data'
    } else {
        $env:MEDTRUST_MINIO_VOLUME_NAME.Trim()
    }
    return [pscustomobject]@{
        PostgresVolume = $postgresVolume
        MinioVolume = $minioVolume
    }
}

function Assert-MedTrustLocalStorage {
    $storage = Get-MedTrustLocalStorageConfig
    $availableVolumes = @(docker volume ls --format '{{.Name}}' 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to enumerate Docker volumes.'
    }
    $expectedContainers = @{
        $storage.PostgresVolume = 'medtrust-space-postgres-1'
        $storage.MinioVolume = 'medtrust-space-minio-1'
    }
    foreach ($volumeName in @($storage.PostgresVolume, $storage.MinioVolume)) {
        if ($availableVolumes -notcontains $volumeName) {
            throw "Required canonical local volume '$volumeName' does not exist. Startup stopped to avoid creating an empty business volume."
        }
        $volumeEntries = @(
            docker run --rm --mount "type=volume,source=$volumeName,target=/volume,readonly" `
                postgres:16-alpine find /volume -mindepth 1 -print -quit 2>$null
        )
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect required canonical local volume '$volumeName'."
        }
        if ($volumeEntries.Count -eq 0) {
            throw "Required canonical local volume '$volumeName' is empty. Startup stopped before database or object-store initialization."
        }
        $runningUsers = @(
            docker ps --filter "volume=$volumeName" --format '{{.Names}}' 2>$null |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect running containers for volume '$volumeName'."
        }
        if ($runningUsers.Count -gt 1) {
            throw "Canonical volume '$volumeName' is mounted by multiple running containers: $($runningUsers -join ', '). Stop the duplicate infrastructure before startup."
        }
        if ($runningUsers.Count -eq 1 -and $runningUsers[0] -ne $expectedContainers[$volumeName]) {
            throw "Canonical volume '$volumeName' is mounted by unexpected container '$($runningUsers[0])'. Stop it before Compose startup."
        }
    }
    return $storage
}

function Get-MedTrustComposeServices {
    param([Parameter(Mandatory)][string]$Workspace)

    Push-Location $Workspace
    try {
        $output = @(docker compose config --services 2>$null)
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw 'Unable to read Docker Compose services. Check Docker Desktop and compose.yaml.'
    }
    $services = @(
        $output |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_.Trim() }
    )
    if ($services.Count -eq 0) {
        throw 'Docker Compose configuration contains no services.'
    }
    return $services
}

function Assert-MedTrustComposeService {
    param(
        [Parameter(Mandatory)][string[]]$Services,
        [Parameter(Mandatory)][string]$ServiceName
    )

    if ($Services -notcontains $ServiceName) {
        throw "Docker Compose service '$ServiceName' is missing. Available services: $($Services -join ', ')."
    }
}

function Get-MedTrustComposeContainerId {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [Parameter(Mandatory)][string]$ServiceName,
        [string[]]$Services
    )

    $knownServices = @($Services)
    if ($knownServices.Count -eq 0) {
        $knownServices = @(Get-MedTrustComposeServices -Workspace $Workspace)
    }
    Assert-MedTrustComposeService -Services $knownServices -ServiceName $ServiceName

    Push-Location $Workspace
    try {
        $output = @(docker compose ps -q $ServiceName 2>$null)
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw "Docker Compose could not inspect service '$ServiceName'."
    }
    $ids = @(
        $output |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_.Trim() }
    )
    if ($ids.Count -eq 0) {
        return $null
    }
    if ($ids.Count -gt 1) {
        throw "Docker Compose returned multiple running containers for service '$ServiceName'."
    }
    return $ids[0]
}

function Stop-MedTrustComposeService {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [Parameter(Mandatory)][string]$ServiceName,
        [Parameter(Mandatory)][string[]]$Services
    )

    Assert-MedTrustComposeService -Services $Services -ServiceName $ServiceName
    $previousPreference = $ErrorActionPreference
    Push-Location $Workspace
    try {
        $ErrorActionPreference = 'Continue'
        docker compose stop $ServiceName *> $null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw "Docker Compose could not stop service '$ServiceName'."
    }
}

function Get-MedTrustContainerState {
    param([Parameter(Mandatory)][string]$ContainerId)

    $runningOutput = @(docker inspect --format '{{.State.Running}}' $ContainerId 2>$null)
    $runningExit = $LASTEXITCODE
    $healthOutput = @(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $ContainerId 2>$null)
    $healthExit = $LASTEXITCODE
    if ($runningExit -ne 0 -or $healthExit -ne 0) {
        throw 'Docker could not inspect a Compose container.'
    }
    $running = [string]($runningOutput | Select-Object -First 1)
    $health = [string]($healthOutput | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($running)) {
        throw 'Docker returned an empty container running state.'
    }
    return [pscustomobject]@{
        Running = $running.Trim().ToLowerInvariant() -eq 'true'
        Health = if ([string]::IsNullOrWhiteSpace($health)) { 'none' } else { $health.Trim().ToLowerInvariant() }
    }
}

function Wait-MedTrustComposeService {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [Parameter(Mandatory)][string]$ServiceName,
        [Parameter(Mandatory)][string[]]$Services,
        [int]$TimeoutSeconds = 90,
        [int]$PollMilliseconds = 1000,
        [int]$ReadyPort = 0
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastStatus = ''
    while ([DateTime]::UtcNow -lt $deadline) {
        $containerId = Get-MedTrustComposeContainerId -Workspace $Workspace -ServiceName $ServiceName -Services $Services
        if ([string]::IsNullOrWhiteSpace([string]$containerId)) {
            $status = 'container not running'
        }
        else {
            $state = Get-MedTrustContainerState -ContainerId $containerId
            $portReady = $true
            if ($ReadyPort -gt 0) {
                $portReady = @(Get-NetTCPConnection -LocalPort $ReadyPort -State Listen -ErrorAction SilentlyContinue).Count -gt 0
            }
            if (-not $state.Running) {
                $status = 'container not running'
            }
            elseif ($state.Health -eq 'unhealthy') {
                throw "Docker Compose service '$ServiceName' is unhealthy."
            }
            elseif ($state.Health -eq 'starting') {
                $status = 'health starting'
            }
            elseif (-not $portReady) {
                $status = "waiting for port $ReadyPort"
            }
            else {
                Write-Host "Docker Compose service '$ServiceName' is ready." -ForegroundColor Green
                return $containerId
            }
        }
        if ($status -ne $lastStatus) {
            Write-Host "Waiting for Docker Compose service '$ServiceName': $status"
            $lastStatus = $status
        }
        Start-Sleep -Milliseconds $PollMilliseconds
    }
    throw "Timed out waiting for Docker Compose service '$ServiceName'. Run: docker compose ps -a"
}

function Get-MedTrustPidEntries {
    param([Parameter(Mandatory)][string]$PidFile)

    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return @()
    }
    try {
        $content = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($content)) {
            return @()
        }
        $parsed = $content | ConvertFrom-Json
        if ($parsed -is [System.Array]) {
            foreach ($item in $parsed) {
                Write-Output $item
            }
            return
        }
        return $parsed
    }
    catch {
        throw "The Phase 4 PID file is invalid and was preserved for inspection: $PidFile"
    }
}

function Get-MedTrustEntryValue {
    param(
        [Parameter(Mandatory)]$Entry,
        [Parameter(Mandatory)][string]$Name
    )

    $property = $Entry.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-MedTrustManagedProcessState {
    param(
        [Parameter(Mandatory)]$Entry,
        [Parameter(Mandatory)][string]$Workspace
    )

    $name = [string](Get-MedTrustEntryValue -Entry $Entry -Name 'name')
    $pidValue = Get-MedTrustEntryValue -Entry $Entry -Name 'pid'
    $pidNumber = 0
    if ([string]::IsNullOrWhiteSpace([string]$pidValue) -or -not [int]::TryParse([string]$pidValue, [ref]$pidNumber) -or $pidNumber -le 0) {
        return [pscustomobject]@{ Name = $name; Pid = $pidNumber; State = 'invalid'; Process = $null; Reason = 'invalid PID value' }
    }
    $process = Get-Process -Id $pidNumber -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return [pscustomobject]@{ Name = $name; Pid = $pidNumber; State = 'missing'; Process = $null; Reason = 'process no longer exists' }
    }

    $expectedTokens = @{
        'backend' = @('app.main:app', '8000')
        'outbox-dispatcher' = @('app.workers.outbox_dispatcher')
        'execution-coordinator' = @('app.workers.execution_coordinator')
        'callback-worker' = @('app.workers.execution_callback_worker')
        'frontend' = @('vite', '5173')
    }
    if (-not $expectedTokens.ContainsKey($name)) {
        return [pscustomobject]@{ Name = $name; Pid = $pidNumber; State = 'mismatch'; Process = $process; Reason = 'unknown managed process name' }
    }

    try {
        $commandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId = $pidNumber" -ErrorAction Stop).CommandLine
    }
    catch {
        $commandLine = ''
    }
    $logRoot = [System.IO.Path]::GetFullPath((Join-Path $Workspace '.runtime\phase4-demo-logs'))
    $stdout = [string](Get-MedTrustEntryValue -Entry $Entry -Name 'stdout')
    $pathOwned = $false
    if (-not [string]::IsNullOrWhiteSpace($stdout)) {
        try {
            $pathOwned = [System.IO.Path]::GetFullPath($stdout).StartsWith($logRoot, [System.StringComparison]::OrdinalIgnoreCase)
        }
        catch {
            $pathOwned = $false
        }
    }
    $commandOwned = -not [string]::IsNullOrWhiteSpace($commandLine)
    foreach ($token in $expectedTokens[$name]) {
        if ($commandLine.IndexOf($token, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            $commandOwned = $false
        }
    }
    if (-not $pathOwned -or -not $commandOwned) {
        return [pscustomobject]@{ Name = $name; Pid = $pidNumber; State = 'mismatch'; Process = $process; Reason = 'PID does not match the recorded project command' }
    }
    return [pscustomobject]@{ Name = $name; Pid = $pidNumber; State = 'managed'; Process = $process; Reason = 'verified project process' }
}

function Get-MedTrustDescendantProcessIds {
    param([Parameter(Mandatory)][int]$ParentProcessId)

    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $pending.Enqueue($ParentProcessId)
    $descendants = [System.Collections.Generic.List[int]]::new()
    while ($pending.Count -gt 0) {
        $current = $pending.Dequeue()
        foreach ($child in $processes | Where-Object { [int]$_.ParentProcessId -eq $current }) {
            $childId = [int]$child.ProcessId
            if (-not $descendants.Contains($childId)) {
                $descendants.Add($childId)
                $pending.Enqueue($childId)
            }
        }
    }
    return $descendants.ToArray()
}

function Remove-MedTrustStalePidFile {
    param(
        [Parameter(Mandatory)][string]$PidFile,
        [Parameter(Mandatory)][string]$Workspace
    )

    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return $false
    }
    $states = @(Get-MedTrustPidEntries -PidFile $PidFile | ForEach-Object {
        Get-MedTrustManagedProcessState -Entry $_ -Workspace $Workspace
    })
    $active = @($states | Where-Object { $_.State -eq 'managed' })
    if ($active.Count -gt 0) {
        throw 'Phase 4 demo appears to be running. Stop it first.'
    }
    $unsafe = @($states | Where-Object { $_.State -notin @('missing') })
    if ($unsafe.Count -gt 0) {
        throw "The Phase 4 PID file references a process that cannot be verified as this project. The file was preserved: $PidFile"
    }
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host 'Removed a stale Phase 4 PID file after confirming all recorded processes are absent.' -ForegroundColor Yellow
    return $true
}

function Invoke-MedTrustUtf8Json {
    param(
        [Parameter(Mandatory)]
        [string]$Uri,
        [hashtable]$Headers = @{},
        [string]$Cookie = ''
    )

    $client = [System.Net.WebClient]::new()
    try {
        foreach ($entry in $Headers.GetEnumerator()) {
            $client.Headers.Add([string]$entry.Key, [string]$entry.Value)
        }
        if (-not [string]::IsNullOrWhiteSpace($Cookie)) {
            $client.Headers.Add('Cookie', $Cookie)
        }
        $bytes = $client.DownloadData($Uri)
        return ([System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
    }
    finally {
        $client.Dispose()
    }
}

function Get-MedTrustLocalDemoPassword {
    $password = $env:MEDTRUST_LOCAL_DEMO_PASSWORD
    if ([string]::IsNullOrWhiteSpace($password) -or $password.Length -lt 12) {
        throw 'MEDTRUST_LOCAL_DEMO_PASSWORD must be set locally and contain at least 12 characters.'
    }
    return $password
}

function Get-MedTrustDemoPasswordForUsername {
    param([Parameter(Mandatory)][string]$Username)

    $passwordVariable = switch ($Username) {
        'hospital.demo' { 'MEDTRUST_DEMO_HOSPITAL_PASSWORD' }
        'model.demo' { 'MEDTRUST_DEMO_MODEL_PASSWORD' }
        'requester.demo' { 'MEDTRUST_DEMO_REQUESTER_PASSWORD' }
        'operator.demo' { 'MEDTRUST_DEMO_OPERATOR_PASSWORD' }
        'catalog.curator.demo' { 'MEDTRUST_DEMO_CATALOG_CURATOR_PASSWORD' }
        default { $null }
    }
    if ([string]::IsNullOrWhiteSpace($passwordVariable)) {
        return Get-MedTrustLocalDemoPassword
    }

    $password = [Environment]::GetEnvironmentVariable(
        $passwordVariable,
        [EnvironmentVariableTarget]::Process
    )
    if ([string]::IsNullOrWhiteSpace($password)) {
        return Get-MedTrustLocalDemoPassword
    }
    $deploymentMode = ([string]$env:MEDTRUST_DEPLOYMENT_MODE).Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($deploymentMode)) { $deploymentMode = 'local' }
    $allowWeakLocal = ([string]$env:MEDTRUST_ALLOW_WEAK_LOCAL_DEMO_CREDENTIALS).Trim().ToLowerInvariant() -eq 'true'
    $publicDemoUsernames = @('hospital.demo', 'model.demo', 'requester.demo', 'operator.demo')
    $minimumLength = if (
        $deploymentMode -eq 'local' -and
        $allowWeakLocal -and
        $publicDemoUsernames -contains $Username
    ) { 3 } else { 12 }
    if ($password.Length -lt $minimumLength) {
        throw "$passwordVariable must contain at least $minimumLength characters."
    }
    return $password
}

function New-MedTrustLocalDemoSession {
    param(
        [Parameter(Mandatory)][string]$Username,
        [Parameter(Mandatory)][string]$Password
    )

    $client = [System.Net.WebClient]::new()
    try {
        $client.Headers.Add('Content-Type', 'application/json')
        $body = @{ username = $Username; password = $Password } | ConvertTo-Json -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $null = $client.UploadData(
            'http://127.0.0.1:8000/api/v1/auth/login',
            'POST',
            $bytes
        )
        $setCookie = [string]$client.ResponseHeaders['Set-Cookie']
        if ([string]::IsNullOrWhiteSpace($setCookie)) {
            throw 'Login response did not establish a local demo session.'
        }
        return $setCookie.Split(';')[0]
    }
    finally {
        $client.Dispose()
    }
}

function Import-MedTrustDemoEnvironment {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [switch]$IncludeLocalOverrides
    )

    $configPath = $env:MEDTRUST_PHASE4_CONFIG
    if ([string]::IsNullOrWhiteSpace($configPath)) {
        $configPath = Join-Path $Workspace 'config\phase4-demo.env'
    } elseif (-not [System.IO.Path]::IsPathRooted($configPath)) {
        $configPath = Join-Path $Workspace $configPath
    }

    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return $null
    }

    $environmentFiles = @(
        [pscustomobject]@{ Path = $configPath; OverrideExisting = $false }
    )
    if ($IncludeLocalOverrides) {
        $localOverridePath = Join-Path $Workspace 'config\phase4-demo.local.env'
        if (Test-Path -LiteralPath $localOverridePath -PathType Leaf) {
            $environmentFiles += [pscustomobject]@{
                Path = $localOverridePath
                OverrideExisting = $true
            }
        }
    }

    foreach ($environmentFile in $environmentFiles) {
        foreach ($rawLine in Get-Content -LiteralPath $environmentFile.Path -Encoding utf8) {
            $line = $rawLine.Trim()
            if (-not $line -or $line.StartsWith('#')) { continue }
            $parts = $line.Split('=', 2)
            if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
                throw "Invalid environment entry in $($environmentFile.Path): $rawLine"
            }
            $name = $parts[0]
            $value = $parts[1].Trim()
            if (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            if (
                $environmentFile.OverrideExisting -or
                [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, 'Process'))
            ) {
                [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            }
        }
    }
    return (Resolve-Path -LiteralPath $configPath).Path
}

function Resolve-MedTrustPath {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [Parameter(Mandatory)][string]$Value
    )

    $expanded = [Environment]::ExpandEnvironmentVariables($Value)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $Workspace $expanded))
}

function Resolve-MedTrustExecutable {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][string]$EnvironmentVariable,
        [string[]]$CommandNames = @(),
        [string[]]$FallbackPaths = @(),
        [string[]]$ProbeArguments = @('--version')
    )

    $candidates = [System.Collections.Generic.List[string]]::new()
    $override = [Environment]::GetEnvironmentVariable($EnvironmentVariable, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($override)) {
        $candidates.Add((Resolve-MedTrustPath -Workspace $Workspace -Value $override))
    }
    foreach ($name in $CommandNames) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
            $candidates.Add($command.Source)
        }
    }
    foreach ($fallback in $FallbackPaths) {
        $candidates.Add((Resolve-MedTrustPath -Workspace $Workspace -Value $fallback))
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        & $candidate @ProbeArguments *> $null
        if ($LASTEXITCODE -eq 0) { return (Resolve-Path -LiteralPath $candidate).Path }
    }

    throw "$Description is unavailable. Put it on PATH or set $EnvironmentVariable to a working executable."
}

function Resolve-MedTrustAsset {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][string]$EnvironmentVariable,
        [string[]]$RelativeCandidates = @()
    )

    $configured = [Environment]::GetEnvironmentVariable($EnvironmentVariable, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($configured)) {
        $resolved = Resolve-MedTrustPath -Workspace $Workspace -Value $configured
        if (Test-Path -LiteralPath $resolved -PathType Leaf) { return $resolved }
        throw "$Description does not exist at the configured path: $resolved"
    }

    foreach ($candidate in $RelativeCandidates) {
        $resolved = Resolve-MedTrustPath -Workspace $Workspace -Value $candidate
        if (Test-Path -LiteralPath $resolved -PathType Leaf) { return $resolved }
    }

    throw "$Description is required. Set $EnvironmentVariable or create config\phase4-demo.env from the example."
}

function Get-MedTrustPhase4DatabaseUrl {
    $configured = $env:MEDTRUST_PHASE4_DATABASE_URL
    if (-not [string]::IsNullOrWhiteSpace($configured)) { return $configured }
    return 'postgresql+asyncpg://medtrust:medtrust_dev_only@127.0.0.1:5432/medtrust_phase4_demo'
}

function Get-MedTrustPhase4DatabaseName {
    $configured = $env:MEDTRUST_PHASE4_DATABASE_NAME
    if (-not [string]::IsNullOrWhiteSpace($configured)) { return $configured }
    return 'medtrust_phase4_demo'
}
