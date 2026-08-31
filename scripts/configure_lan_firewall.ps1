param(
    [ValidateSet('Show', 'Add', 'Remove')][string]$Action = 'Show',
    [int]$Port = 8080,
    [switch]$Confirmed
)

$ErrorActionPreference = 'Stop'
$ruleName = 'MedTrust Space LAN Roadshow Gateway'
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($Action -eq 'Show') {
    if ($existing) { $existing | Get-NetFirewallPortFilter | Format-Table Protocol, LocalPort }
    else { Write-Host 'No MedTrust LAN gateway firewall rule exists.' }
    return
}
if (-not $Confirmed) { throw 'Firewall changes require explicit -Confirmed authorization.' }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run PowerShell as Administrator for firewall changes.'
}
if ($Action -eq 'Remove') {
    if ($existing) { $existing | Remove-NetFirewallRule }
    Write-Host 'MedTrust LAN gateway firewall rule removed.' -ForegroundColor Green
    return
}
$privateProfile = @(Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Private')
if ($privateProfile.Count -eq 0) { throw 'No active Private network profile exists. No firewall rule was added.' }
if ($existing) { $existing | Remove-NetFirewallRule }
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
Write-Host "Private-profile TCP firewall rule added for gateway port $Port." -ForegroundColor Green
