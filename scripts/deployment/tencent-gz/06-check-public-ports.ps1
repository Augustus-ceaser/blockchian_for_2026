[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$ServerHost
)

$results = foreach ($port in 22, 80, 443, 5432, 8000, 8080, 9000, 9001) {
    $test = Test-NetConnection -ComputerName $ServerHost -Port $port -WarningAction SilentlyContinue
    [pscustomobject]@{ Port = $port; Reachable = $test.TcpTestSucceeded }
}
$results | Format-Table -AutoSize
if ($results | Where-Object { $_.Port -in 5432, 8000, 8080, 9000, 9001 -and $_.Reachable }) {
    throw "One or more internal application ports are publicly reachable."
}
