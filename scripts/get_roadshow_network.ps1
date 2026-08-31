param([switch]$Select, [string]$InterfaceAlias)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$outputPath = Join-Path $workspace 'config\lan-roadshow.local.env'

$profiles = @{}
Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {
    $profiles[$_.InterfaceIndex] = $_.NetworkCategory.ToString()
}
$defaultIndexes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Where-Object { $_.NextHop -ne '0.0.0.0' } | Select-Object -ExpandProperty InterfaceIndex -Unique)

$candidates = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
    Where-Object {
        $_.AddressState -eq 'Preferred' -and
        $_.IPAddress -ne '127.0.0.1' -and
        -not $_.IPAddress.StartsWith('169.254.')
    } |
    ForEach-Object {
        $adapter = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue
        if ($null -eq $adapter -or $adapter.Status -ne 'Up') { return }
        $description = "$($adapter.Name) $($adapter.InterfaceDescription)"
        $suspectedVirtual = $description -match '(?i)virtual|hyper-v|vethernet|docker|wsl|loopback'
        $suspectedVpn = $description -match '(?i)vpn|sangfor|tap|tun|wireguard|anyconnect'
        [pscustomobject]@{
            InterfaceAlias = $adapter.Name
            InterfaceIndex = $_.InterfaceIndex
            IPv4 = $_.IPAddress
            NetworkCategory = if ($profiles.ContainsKey($_.InterfaceIndex)) { $profiles[$_.InterfaceIndex] } else { 'Unknown' }
            HasDefaultRoute = $defaultIndexes -contains $_.InterfaceIndex
            Kind = if ($suspectedVirtual) { 'Virtual' } elseif ($suspectedVpn) { 'VPN' } else { 'Physical' }
            Recommended = -not $suspectedVirtual -and -not $suspectedVpn -and ($defaultIndexes -contains $_.InterfaceIndex)
        }
    })

$candidates | Format-Table InterfaceAlias, IPv4, NetworkCategory, HasDefaultRoute, Kind, Recommended -AutoSize
if (-not $Select) { return }

$eligible = @($candidates | Where-Object { $_.Kind -eq 'Physical' })
if ($InterfaceAlias) {
    $chosen = @($eligible | Where-Object { $_.InterfaceAlias -eq $InterfaceAlias })
} else {
    $chosen = @($eligible | Where-Object Recommended)
}
if ($chosen.Count -ne 1) {
    throw 'LAN interface selection is ambiguous. Re-run with -Select -InterfaceAlias <name>. VPN and virtual adapters are not selected automatically.'
}
if ($chosen[0].NetworkCategory -ne 'Private') {
    throw "Selected network '$($chosen[0].InterfaceAlias)' is $($chosen[0].NetworkCategory). LAN roadshow requires a Private Windows network profile."
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null
@(
    "MEDTRUST_GATEWAY_BIND_IP=$($chosen[0].IPv4)"
    'MEDTRUST_GATEWAY_PORT=8080'
    "MEDTRUST_PUBLIC_ORIGIN=http://$($chosen[0].IPv4):8080"
) | Set-Content -LiteralPath $outputPath -Encoding ASCII
Write-Host "Saved ignored LAN configuration: $outputPath" -ForegroundColor Green
