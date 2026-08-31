Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'phase4_demo_common.ps1')

function Get-RoadshowExpectedState {
    return [ordered]@{
        external_dataset_records = 982
        external_model_records = 16
        published_external_data_products = 3
        published_external_model_products = 2
        relations = 7
        evidences = 8
        static_transformation_relations = 4
        static_incompatible_relations = 2
        executed_evidences = 1
        verified_evidences = 1
        approved_materialization_plans = 0
        compute_runs = 2
        minio_objects = 30
        alembic_head = '20260728_0049'
        reference_relation_id = 'df7ec70c-f4cb-5df7-842d-bf2af6d66961'
    }
}

function Get-RoadshowState {
    param([Parameter(Mandatory)][string]$Workspace)

    $python = Resolve-MedTrustExecutable -Workspace $Workspace -Description 'Backend Python' `
        -EnvironmentVariable 'MEDTRUST_BACKEND_PYTHON' -CommandNames @('python') `
        -FallbackPaths @('backend\.venv\Scripts\python.exe') `
        -ProbeArguments @('-c', 'import sqlalchemy, minio')
    $runtime = Join-Path $Workspace '.runtime\phase5127'
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    $statePath = Join-Path $runtime 'roadshow-state.json'
    $databaseUrl = Get-MedTrustPhase4DatabaseUrl
    $previousDatabase = $env:MEDTRUST_DATABASE_URL
    $previousMinio = $env:MEDTRUST_MINIO_ENDPOINT
    try {
        $env:MEDTRUST_DATABASE_URL = $databaseUrl
        $env:MEDTRUST_MINIO_ENDPOINT = '127.0.0.1:9000'
        Push-Location (Join-Path $Workspace 'backend')
        try {
            & $python -m app.tools.generate_roadshow_state --kind business-state `
                --output $statePath --database-url $databaseUrl *> $null
            if ($LASTEXITCODE -ne 0) { throw 'Roadshow state generator failed.' }
        }
        finally { Pop-Location }
    }
    finally {
        $env:MEDTRUST_DATABASE_URL = $previousDatabase
        $env:MEDTRUST_MINIO_ENDPOINT = $previousMinio
    }
    return Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
}

function Test-RoadshowState {
    param(
        [Parameter(Mandatory)]$State,
        [Parameter(Mandatory)]$Expected
    )

    $failures = [System.Collections.Generic.List[string]]::new()
    function Require-Equal([string]$Label, $Actual, $Wanted) {
        if ($Actual -ne $Wanted) {
            $failures.Add("$Label expected=$Wanted actual=$Actual")
        }
    }
    Require-Equal 'external datasets' $State.counts.external_dataset_records $Expected.external_dataset_records
    Require-Equal 'external models' $State.counts.external_model_records $Expected.external_model_records
    Require-Equal 'published data products' $State.status_counts.published_external_data_products $Expected.published_external_data_products
    Require-Equal 'published model products' $State.status_counts.published_external_model_products $Expected.published_external_model_products
    Require-Equal 'relations' $State.counts.relations $Expected.relations
    Require-Equal 'evidences' $State.counts.evidences $Expected.evidences
    Require-Equal 'transformation relations' $State.status_counts.static_transformation_relations $Expected.static_transformation_relations
    Require-Equal 'incompatible relations' $State.status_counts.static_incompatible_relations $Expected.static_incompatible_relations
    Require-Equal 'executed evidence' $State.status_counts.executed_evidences $Expected.executed_evidences
    Require-Equal 'verified evidence' $State.status_counts.verified_evidences $Expected.verified_evidences
    Require-Equal 'approved plans' $State.status_counts.approved_materialization_plans $Expected.approved_materialization_plans
    Require-Equal 'compute runs' $State.counts.compute_runs $Expected.compute_runs
    Require-Equal 'MinIO objects' $State.storage.object_count $Expected.minio_objects
    Require-Equal 'Alembic' $State.alembic_head $Expected.alembic_head
    Require-Equal 'reference relation' ([string]$State.reference_relation.id) $Expected.reference_relation_id
    Require-Equal 'reference status' $State.reference_relation.current_status 'verified'
    Require-Equal 'reference level' $State.reference_relation.strongest_evidence_level 'platform_verification'
    Require-Equal 'audit chain valid' $State.audit.chain_valid $true
    Require-Equal 'external model materialized' $State.boundaries.external_model_materialized $false
    Require-Equal 'external Executor registered' $State.boundaries.external_executor_registered $false
    foreach ($model in @($State.external_models)) {
        Require-Equal "$($model.name) runtime" $model.runtime 'external_metadata_only'
        Require-Equal "$($model.name) execution" $model.execution_status 'not_materialized'
    }
    return $failures
}

function Get-RoadshowServiceState {
    param([Parameter(Mandatory)][string]$Workspace)

    $pidFile = Join-Path $Workspace '.runtime\phase4-demo-processes.json'
    $services = @()
    if (Test-Path -LiteralPath $pidFile) {
        foreach ($entry in @(Get-MedTrustPidEntries -PidFile $pidFile)) {
            $state = Get-MedTrustManagedProcessState -Entry $entry -Workspace $Workspace
            $services += [pscustomobject]@{
                Name = $state.Name
                State = $state.State
                Pid = $state.Pid
            }
        }
    }
    return $services
}
