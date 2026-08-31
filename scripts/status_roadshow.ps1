param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')
$workspace = Get-MedTrustWorkspace
$null = Import-MedTrustDemoEnvironment -Workspace $workspace -IncludeLocalOverrides
$cookie = New-MedTrustLocalDemoSession `
    -Username 'operator.demo' `
    -Password (Get-MedTrustDemoPasswordForUsername -Username 'operator.demo')

$health = Invoke-MedTrustUtf8Json -Uri 'http://127.0.0.1:8000/api/v1/roadshow-experience/health' -Cookie $cookie
$chains = Invoke-MedTrustUtf8Json -Uri 'http://127.0.0.1:8000/api/v1/roadshow-experience/chains' -Cookie $cookie
$local = Invoke-MedTrustUtf8Json -Uri 'http://127.0.0.1:8000/api/v1/auth/status' -Cookie $cookie

Write-Host "Roadshow health: $($health.status)" -ForegroundColor $(if ($health.status -eq 'ok') { 'Green' } else { 'Red' })
foreach ($service in $health.services) {
    Write-Host "  $($service.label): $($service.status)"
}
Write-Host "  Audit chain valid: $($health.audit_chain_valid)"
Write-Host "  hard_isolation: $($health.hard_isolation)"
Write-Host '  Public portal roles: 4'
Write-Host "  Active browser sessions: $($local.active_sessions)"
Write-Host "  Pending lifecycle requests: $($local.pending_lifecycle_requests)"
Write-Host "  Unpublished products: $($local.unpublished_products)"
Write-Host "  Archived products: $($local.archived_products)"
Write-Host 'Portals:'
Write-Host '  Hospital: http://127.0.0.1:5173/demo-login'
Write-Host '  Model provider: http://127.0.0.1:5173/demo-login'
Write-Host '  Requester: http://127.0.0.1:5173/demo-login'
Write-Host '  Operator: http://127.0.0.1:5173/demo-login'
Write-Host 'Business chains:'
foreach ($chain in $chains.items) {
    Write-Host "  $($chain.application_number): $($chain.completed_nodes)/$($chain.total_nodes), status=$($chain.status)"
    Write-Host "    next_role=$($chain.next_role); next_action=$($chain.next_action)"
}
