param([ValidateSet('Loopback','Lan')][string]$Mode = 'Loopback')

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'roadshow_common.ps1')
$workspace = Get-MedTrustWorkspace
$state = Get-RoadshowState -Workspace $workspace
$storage = Get-MedTrustLocalStorageConfig
$services = @(Get-RoadshowServiceState -Workspace $workspace)

Write-Host "mode=$Mode"
Write-Host 'url=http://127.0.0.1:5173/roadshow'
Write-Host "commit=$(git -C $workspace rev-parse --short HEAD)"
Write-Host "alembic=$($state.alembic_head)"
Write-Host "postgres_volume=$($storage.PostgresVolume)"
Write-Host "minio_volume=$($storage.MinioVolume)"
Write-Host 'postgres_single_writer=true'
foreach ($service in $services) { Write-Host "service_$($service.Name)=$($service.State)" }
Write-Host "datasets=$($state.counts.external_dataset_records)"
Write-Host "models=$($state.counts.external_model_records)"
Write-Host "published_data=$($state.status_counts.published_external_data_products)"
Write-Host "published_models=$($state.status_counts.published_external_model_products)"
Write-Host "relations=$($state.counts.relations)"
Write-Host "verified_relations=$(if ($state.reference_relation.current_status -eq 'verified') { 1 } else { 0 })"
Write-Host "approved_plans=$($state.status_counts.approved_materialization_plans)"
Write-Host "compute_runs=$($state.counts.compute_runs)"
Write-Host "minio_objects=$($state.storage.object_count)"
Write-Host "audit_valid=$($state.audit.chain_valid)"
& (Join-Path $PSScriptRoot 'roadshow-preflight.ps1') -Mode $Mode
