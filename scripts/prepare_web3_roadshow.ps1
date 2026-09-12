param([switch]$Open)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$workspace = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$blockchainRoot = Join-Path $workspace 'blockchain'
$runtimeRoot = Join-Path $workspace '.runtime\web3-demo'
$pidFile = Join-Path $workspace '.runtime\web3-demo-process.json'
$hardhatCli = [System.IO.Path]::GetFullPath(
    (Join-Path $blockchainRoot 'node_modules\hardhat\dist\src\cli.js')
)
$deploymentFile = Join-Path $blockchainRoot 'deployments\localhost.json'
$phase4Config = Join-Path $workspace 'config\phase4-demo.env'
$backendOverride = Join-Path $workspace 'backend\.env.local'

if (-not (Test-Path -LiteralPath $phase4Config -PathType Leaf)) {
    throw 'Missing config\phase4-demo.env. Copy config\phase4-demo.example.env, fill the local asset paths/passwords, then retry.'
}
if (-not (Test-Path -LiteralPath $hardhatCli -PathType Leaf)) {
    throw 'Blockchain dependencies are missing. Run pnpm install --frozen-lockfile in the blockchain directory first.'
}
$node = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $node) { throw 'Node.js is unavailable.' }

if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
    & (Join-Path $PSScriptRoot 'stop_web3_demo.ps1')
}
$occupied = @(Get-NetTCPConnection -LocalPort 8545 -State Listen -ErrorAction SilentlyContinue)
if ($occupied.Count -gt 0) {
    throw 'Port 8545 is already occupied by an unmanaged process. Stop it before starting the Web3 roadshow.'
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$stdout = Join-Path $runtimeRoot 'hardhat-node.out.log'
$stderr = Join-Path $runtimeRoot 'hardhat-node.err.log'
$nodeProcess = Start-Process `
    -FilePath $node.Source `
    -ArgumentList @($hardhatCli, 'node') `
    -WorkingDirectory $blockchainRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

[ordered]@{
    pid = $nodeProcess.Id
    workspace = $workspace
    hardhatCli = $hardhatCli
    stdout = $stdout
    stderr = $stderr
    startedAt = [DateTimeOffset]::UtcNow.ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

try {
    $rpcReady = $false
    $rpcBody = '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($null -eq (Get-Process -Id $nodeProcess.Id -ErrorAction SilentlyContinue)) {
            throw "Hardhat node exited during startup. Inspect $stderr"
        }
        try {
            $response = Invoke-RestMethod `
                -Uri 'http://127.0.0.1:8545' `
                -Method Post `
                -ContentType 'application/json' `
                -Body $rpcBody `
                -TimeoutSec 2
            if ($response.result -eq '0x7a69') { $rpcReady = $true; break }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $rpcReady) { throw "Hardhat RPC did not become ready. Inspect $stderr" }

    Push-Location $blockchainRoot
    try {
        & $node.Source $hardhatCli run scripts/deploy-local.ts --network localhost | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Local Web3 contract deployment failed.' }
    }
    finally { Pop-Location }

    if (-not (Test-Path -LiteralPath $deploymentFile -PathType Leaf)) {
        throw 'Deployment completed without a localhost manifest.'
    }
    $deployment = Get-Content -LiteralPath $deploymentFile -Raw -Encoding utf8 | ConvertFrom-Json
    if ([string]$deployment.chainId -ne '31337') {
        throw 'Deployment manifest does not describe the expected local chain.'
    }
    $contracts = $deployment.contracts
    $addresses = @(
        [string]$contracts.roleCredential,
        [string]$contracts.agreementRegistry,
        [string]$contracts.escrow,
        [string]$contracts.settlementToken
    )
    if (@($addresses | Where-Object { $_ -notmatch '^0x[0-9a-fA-F]{40}$' }).Count -gt 0) {
        throw 'Deployment manifest contains an invalid contract address.'
    }
    $spaceScopeDigest = [string]$deployment.spaceScopeDigest
    if (
        $spaceScopeDigest -notmatch '^0x[0-9a-fA-F]{64}$' -or
        $spaceScopeDigest -match '^0x0{64}$'
    ) {
        throw 'Deployment manifest contains an invalid or empty space scope digest.'
    }

    $web3Values = [ordered]@{
        MEDTRUST_WEB3_ENABLED = 'true'
        MEDTRUST_WEB3_CHAIN_ID = '31337'
        MEDTRUST_WEB3_RPC_URL = 'http://127.0.0.1:8545'
        MEDTRUST_WEB3_REQUIRED_CONFIRMATIONS = '1'
        MEDTRUST_WEB3_SIWE_DOMAIN = '127.0.0.1:5173'
        MEDTRUST_WEB3_SIWE_URI = 'http://127.0.0.1:5173'
        MEDTRUST_WEB3_SPACE_SCOPE_DIGEST = $spaceScopeDigest.ToLowerInvariant()
        MEDTRUST_WEB3_ROLE_CREDENTIAL_ADDRESS = [string]$contracts.roleCredential
        MEDTRUST_WEB3_AGREEMENT_REGISTRY_ADDRESS = [string]$contracts.agreementRegistry
        MEDTRUST_WEB3_ESCROW_ADDRESS = [string]$contracts.escrow
        MEDTRUST_WEB3_SETTLEMENT_TOKEN_ADDRESS = [string]$contracts.settlementToken
        MEDTRUST_WEB3_CREDENTIAL_ISSUER_ADDRESS = [string]$deployment.localDemoAccounts.operator
        MEDTRUST_WEB3_EXECUTION_ATTESTOR_ADDRESS = [string]$deployment.localDemoAccounts.executionAttestor
        MEDTRUST_WEB3_DELIVERY_ATTESTOR_ADDRESS = [string]$deployment.localDemoAccounts.deliveryAttestor
        MEDTRUST_WEB3_SETTLEMENT_TOKEN_DECIMALS = '6'
        MEDTRUST_WEB3_ESCROW_REFUND_SECONDS = '86400'
    }
    $existingLines = @()
    if (Test-Path -LiteralPath $backendOverride -PathType Leaf) {
        $existingLines = @(Get-Content -LiteralPath $backendOverride -Encoding utf8)
    }
    $managedNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($name in $web3Values.Keys) { $null = $managedNames.Add($name) }
    $preserved = @(
        $existingLines | Where-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) {
                return $true
            }
            $name = $line.Split('=', 2)[0].Trim()
            return -not $managedNames.Contains($name)
        }
    )
    $newLines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in $preserved) { $newLines.Add($line) }
    if ($newLines.Count -gt 0 -and $newLines[$newLines.Count - 1] -ne '') {
        $newLines.Add('')
    }
    $newLines.Add('# Generated by scripts/prepare_web3_roadshow.ps1 for the disposable local chain.')
    foreach ($entry in $web3Values.GetEnumerator()) {
        $newLines.Add("$($entry.Key)=$($entry.Value)")
    }
    $newLines | Set-Content -LiteralPath $backendOverride -Encoding utf8

    Write-Host 'Web3 contracts and backend addresses are ready.' -ForegroundColor Green
    Write-Host "  Chain: Local Hardhat (31337)"
    Write-Host "  Deployment: $deploymentFile"
    Write-Host '  Boundary: disposable demo chain; no real token value and no production security audit.'

    & (Join-Path $PSScriptRoot 'prepare_roadshow.ps1') -Reset -Open:$Open
}
catch {
    if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        & (Join-Path $PSScriptRoot 'stop_web3_demo.ps1')
    }
    throw
}
