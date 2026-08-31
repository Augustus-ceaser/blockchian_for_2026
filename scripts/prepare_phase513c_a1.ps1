Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$root = 'D:\MedTrustData\phase5.13C-A1'
$cacheRoot = 'D:\MedTrustCache\phase5.13C-A1'
$pkiRoot = Join-Path $root 'pki'
$connectorRoot = Join-Path $root 'connector'
$evidenceRoot = Join-Path $root 'browser-evidence\screenshots'
$envFile = Join-Path $workspace 'config\phase513c-a1.env'
$openssl = @(
    $env:MEDTRUST_OPENSSL,
    'C:\Program Files\Git\usr\bin\openssl.exe',
    'C:\Program Files\Git\mingw64\bin\openssl.exe'
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
$useDockerOpenSsl = -not $openssl
if ($useDockerOpenSsl -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'OpenSSL or Docker is required for the isolated A1 test PKI.'
}

foreach ($path in @(
    $root, $cacheRoot, $pkiRoot, $connectorRoot, $evidenceRoot,
    (Join-Path $root 'storage'), (Join-Path $connectorRoot 'state'),
    (Join-Path $connectorRoot 'identity'), (Join-Path $connectorRoot 'certificates')
)) { New-Item -ItemType Directory -Force -Path $path | Out-Null }

$caKey = Join-Path $pkiRoot 'local-test-ca.key.pem'
$caCert = Join-Path $pkiRoot 'local-test-ca.cert.pem'
$ingressKey = Join-Path $pkiRoot 'ingress.key.pem'
$ingressCsr = Join-Path $pkiRoot 'ingress.csr.pem'
$ingressCert = Join-Path $pkiRoot 'ingress.cert.pem'
if (-not (Test-Path -LiteralPath $caCert)) {
    if ($useDockerOpenSsl) {
        docker run --rm -v "${pkiRoot}:/pki" medtrust-space-backend:latest `
            sh -lc "openssl req -x509 -newkey rsa:3072 -nodes -days 14 -subj '/CN=MedTrust Phase 5.13C A1 Local Test CA/O=Non-Production' -keyout /pki/local-test-ca.key.pem -out /pki/local-test-ca.cert.pem 2>/dev/null"
    } else {
        & $openssl req -x509 -newkey rsa:3072 -nodes -days 14 `
            -subj '/CN=MedTrust Phase 5.13C A1 Local Test CA/O=Non-Production' `
            -keyout $caKey -out $caCert 2>$null
    }
}
if (-not (Test-Path -LiteralPath $ingressCert)) {
    if ($useDockerOpenSsl) {
        docker run --rm -v "${pkiRoot}:/pki" medtrust-space-backend:latest `
            sh -lc "openssl req -new -newkey rsa:3072 -nodes -subj '/CN=connector-ingress/O=MedTrust A1 Local Test' -addext 'subjectAltName=DNS:connector-ingress' -keyout /pki/ingress.key.pem -out /pki/ingress.csr.pem 2>/dev/null"
        docker run --rm -v "${pkiRoot}:/pki" medtrust-space-backend:latest `
            sh -lc "openssl x509 -req -in /pki/ingress.csr.pem -CA /pki/local-test-ca.cert.pem -CAkey /pki/local-test-ca.key.pem -CAcreateserial -days 14 -sha256 -copy_extensions copy -out /pki/ingress.cert.pem 2>/dev/null"
    } else {
        & $openssl req -new -newkey rsa:3072 -nodes `
            -subj '/CN=connector-ingress/O=MedTrust A1 Local Test' `
            -addext 'subjectAltName=DNS:connector-ingress' `
            -keyout $ingressKey -out $ingressCsr 2>$null
        & $openssl x509 -req -in $ingressCsr -CA $caCert -CAkey $caKey `
            -CAcreateserial -days 14 -sha256 -copy_extensions copy -out $ingressCert 2>$null
    }
    Remove-Item -LiteralPath $ingressCsr -Force
}

function New-Secret([int]$bytes = 24) {
    $buffer = New-Object byte[] $bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($buffer).Replace('+','A').Replace('/','B').TrimEnd('=')
}

if (-not (Test-Path -LiteralPath $envFile)) {
    @"
PHASE513C_A1_POSTGRES_PASSWORD=$(New-Secret)
PHASE513C_A1_MINIO_PASSWORD=$(New-Secret)
PHASE513C_A1_HOSPITAL_PASSWORD=$(New-Secret)
PHASE513C_A1_MODEL_PASSWORD=$(New-Secret)
PHASE513C_A1_REQUESTER_PASSWORD=$(New-Secret)
PHASE513C_A1_OPERATOR_PASSWORD=$(New-Secret)
PHASE513C_A1_CATALOG_CURATOR_PASSWORD=$(New-Secret)
PHASE513C_A1_LOCAL_CURATOR_PASSWORD=$(New-Secret)
PHASE513C_A1_LOCAL_REVIEWER_PASSWORD=$(New-Secret)
PHASE513C_A1_PKI_ROOT=$($pkiRoot.Replace('\','/'))
PHASE513C_A1_CONNECTOR_ROOT=$($connectorRoot.Replace('\','/'))
PHASE513C_A1_STORAGE_ROOT=$((Join-Path $root 'storage').Replace('\','/'))
PHASE513C_A1_CACHE_ROOT=$($cacheRoot.Replace('\','/'))
"@ | Set-Content -LiteralPath $envFile -Encoding Ascii
}

Write-Host 'Phase 5.13C-A1 isolated directories and ignored credentials are ready.'
Write-Host "evidence_root=$evidenceRoot"
Write-Host 'loopback_only=true'
Write-Host 'hard_isolation=false'
