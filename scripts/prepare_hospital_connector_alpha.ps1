Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dataRoot = 'D:\MedTrustData\hospital-connector-alpha'
$revokeDataRoot = 'D:\MedTrustData\hospital-connector-alpha-revoke'
$cacheRoot = 'D:\MedTrustCache\hospital-connector-alpha'
$pkiRoot = Join-Path $dataRoot 'pki'
$envFile = Join-Path $workspace 'config\hospital-connector-alpha.env'
$opensslCommand = Get-Command openssl -ErrorAction SilentlyContinue
$opensslCandidates = @(
    $env:MEDTRUST_OPENSSL,
    'C:\Program Files\Git\usr\bin\openssl.exe',
    'C:\Program Files\Git\mingw64\bin\openssl.exe'
)
if ($null -ne $opensslCommand) {
    $opensslCandidates = @($opensslCommand.Source) + $opensslCandidates
}
$openssl = $opensslCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
    Select-Object -First 1
$useDockerOpenSsl = -not $openssl
if ($useDockerOpenSsl -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'OpenSSL or Docker with the existing medtrust-space-backend image is required.'
}

foreach ($path in @(
    $dataRoot, $revokeDataRoot, $cacheRoot, $pkiRoot,
    (Join-Path $dataRoot 'identity'), (Join-Path $dataRoot 'certificates'),
    (Join-Path $dataRoot 'state'), (Join-Path $dataRoot 'capabilities'),
    (Join-Path $dataRoot 'audit'), (Join-Path $dataRoot 'logs'),
    (Join-Path $dataRoot 'tmp'), (Join-Path $dataRoot 'backups'),
    (Join-Path $revokeDataRoot 'identity'), (Join-Path $revokeDataRoot 'certificates'),
    (Join-Path $revokeDataRoot 'state'), (Join-Path $revokeDataRoot 'capabilities'),
    (Join-Path $revokeDataRoot 'audit'), (Join-Path $revokeDataRoot 'logs'),
    (Join-Path $revokeDataRoot 'tmp'), (Join-Path $revokeDataRoot 'backups')
)) { New-Item -ItemType Directory -Force -Path $path | Out-Null }

$caKey = Join-Path $pkiRoot 'local-test-ca.key.pem'
$caCert = Join-Path $pkiRoot 'local-test-ca.cert.pem'
if (-not (Test-Path -LiteralPath $caKey)) {
    if ($useDockerOpenSsl) {
        docker run --rm -v "${pkiRoot}:/pki" medtrust-space-backend:latest `
            sh -lc "openssl req -x509 -newkey rsa:3072 -nodes -days 30 -subj '/CN=MedTrust Local Test CA/O=Non-Production' -keyout /pki/local-test-ca.key.pem -out /pki/local-test-ca.cert.pem 2>/dev/null"
    } else {
        & $openssl req -x509 -newkey rsa:3072 -nodes -days 30 `
            -subj '/CN=MedTrust Local Test CA/O=Non-Production' `
            -keyout $caKey -out $caCert 2>$null
    }
    if ($LASTEXITCODE -ne 0) { throw 'Local Test CA generation failed.' }
}
$ingressKey = Join-Path $pkiRoot 'ingress.key.pem'
$ingressCsr = Join-Path $pkiRoot 'ingress.csr.pem'
$ingressCert = Join-Path $pkiRoot 'ingress.cert.pem'
if (-not (Test-Path -LiteralPath $ingressCert)) {
    if ($useDockerOpenSsl) {
        docker run --rm -v "${pkiRoot}:/pki" medtrust-space-backend:latest `
            sh -lc "openssl req -new -newkey rsa:3072 -nodes -subj '/CN=connector-ingress/O=MedTrust Local Test' -addext 'subjectAltName=DNS:connector-ingress' -keyout /pki/ingress.key.pem -out /pki/ingress.csr.pem 2>/dev/null"
        docker run --rm -v "${pkiRoot}:/pki" medtrust-space-backend:latest `
            sh -lc "openssl x509 -req -in /pki/ingress.csr.pem -CA /pki/local-test-ca.cert.pem -CAkey /pki/local-test-ca.key.pem -CAcreateserial -days 30 -sha256 -copy_extensions copy -out /pki/ingress.cert.pem 2>/dev/null"
    } else {
        & $openssl req -new -newkey rsa:3072 -nodes `
            -subj '/CN=connector-ingress/O=MedTrust Local Test' `
            -addext 'subjectAltName=DNS:connector-ingress' `
            -keyout $ingressKey -out $ingressCsr 2>$null
        & $openssl x509 -req -in $ingressCsr -CA $caCert -CAkey $caKey `
            -CAcreateserial -days 30 -sha256 -copy_extensions copy -out $ingressCert 2>$null
    }
    Remove-Item -LiteralPath $ingressCsr -Force
    if ($LASTEXITCODE -ne 0) { throw 'Ingress certificate generation failed.' }
}

@"
MEDTRUST_CONNECTOR_DATA_ROOT=$($dataRoot.Replace('\','/'))
MEDTRUST_CONNECTOR_REVOKE_DATA_ROOT=$($revokeDataRoot.Replace('\','/'))
MEDTRUST_CONNECTOR_PKI_ROOT=$($pkiRoot.Replace('\','/'))
MEDTRUST_CONNECTOR_CACHE_ROOT=$($cacheRoot.Replace('\','/'))
"@ | Set-Content -LiteralPath $envFile -Encoding Ascii

Write-Host 'Hospital Connector Alpha local test directories prepared.'
Write-Host "data_root=$dataRoot"
Write-Host "pki=Local Test CA / non-production / loopback-only"
Write-Host 'hard_isolation=false'
