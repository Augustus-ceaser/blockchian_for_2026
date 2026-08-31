[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

$ErrorActionPreference = "Stop"
$PackagePath = (Resolve-Path -LiteralPath $PackagePath).Path
$verifyRoot = "D:\MedTrustData\deployment-package-verification"
$runRoot = Join-Path $verifyRoot ([IO.Path]::GetFileNameWithoutExtension(
    [IO.Path]::GetFileNameWithoutExtension($PackagePath)
))
if (Test-Path -LiteralPath $runRoot) {
    Remove-Item -LiteralPath $runRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
tar --options hdrcharset=UTF-8 -xzf $PackagePath -C $runRoot
if ($LASTEXITCODE -ne 0) { throw "Package extraction failed." }

$violations = [Collections.Generic.List[string]]::new()
$files = Get-ChildItem -LiteralPath $runRoot -Recurse -File
foreach ($file in $files) {
    $relative = $file.FullName.Substring($runRoot.Length + 1).Replace("\", "/")
    $lower = $relative.ToLowerInvariant()
    if ($lower -match '(^|/)\.env($|\.)' -and $lower -notmatch '\.env\.example$') {
        $violations.Add("local env: $relative")
    }
    if ($lower -match '\.(db|sqlite|sqlite3|pem|key|p12|pfx)$') {
        $violations.Add("forbidden extension: $relative")
    }
    if ($lower -match '(^|/)(node_modules|\.venv|__pycache__|browser-profile|downloads?|volumes?)(/|$)') {
        $violations.Add("forbidden directory: $relative")
    }
    if ($lower -match '(demo-accounts|credentials\.local|cookie|session|token)\.(txt|json|csv)$') {
        $violations.Add("credential-like file: $relative")
    }
    if ($lower -match '\.(dcm|dicom|svs|ndpi|mrxs|h5ad)$') {
        $violations.Add("medical data file: $relative")
    }
    if ($lower -match '\.(pt|pth|onnx|safetensors|ckpt)$') {
        $violations.Add("model weight file: $relative")
    }
}

$textExtensions = @(".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".sh", ".ps1", ".txt", ".env", ".example")
foreach ($file in $files | Where-Object { $textExtensions -contains $_.Extension.ToLowerInvariant() }) {
    $relative = $file.FullName.Substring($runRoot.Length + 1).Replace("\", "/")
    $content = [IO.File]::ReadAllText($file.FullName)
    if ($content -match '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----') {
        $violations.Add("private key block: $relative")
    }
    if ($relative -notlike "scripts/deployment/tencent-gz/*" -and
        $content -match '[A-Za-z]:\\(Users|MedTrustData)\\') {
        $violations.Add("Windows absolute path: $relative")
    }
}

$dataFiles = $files | Where-Object {
    $_.Extension.ToLowerInvariant() -in @(".csv", ".json", ".parquet", ".sql", ".dump")
}
foreach ($file in $dataFiles) {
    $relative = $file.FullName.Substring($runRoot.Length + 1).Replace("\", "/")
    if ($relative -notlike "frontend/src/*" -and $relative -notlike "backend/app/*") {
        $sample = [IO.File]::ReadAllText($file.FullName)
        if ($sample -match '(?i)(patient[_ -]?id|medical[_ -]?record|\u8eab\u4efd\u8bc1|\u4f4f\u9662\u53f7)') {
            $violations.Add("patient identifier marker: $relative")
        }
    }
}

if ($violations.Count -gt 0) {
    $violations | Sort-Object -Unique | ForEach-Object { Write-Error $_ }
    throw "Package verification failed with $($violations.Count) violation(s)."
}

$required = @(
    "deploy/tencent-gz-public-alpha/compose.production.yaml",
    "deploy/tencent-gz-public-alpha/compose.pre-icp.yaml",
    "deploy/tencent-gz-public-alpha/compose.public.yaml",
    "deploy/tencent-gz-public-alpha/init-secrets.sh"
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $runRoot $relative))) {
        throw "Required package file is missing: $relative"
    }
}

Write-Host "Package verification PASS: forbidden files found = 0"
