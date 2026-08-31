# Phase 5.13C Local Asset Registry Design

Phase 5.13C adds a hospital-side metadata registry and a central metadata mirror.
It does not add data access, transfer, execution, materialization, or product
publication.

## Local ownership

SQLite stores descriptors, append-only versions, local-only location
references, quality profiles, reviews, and metadata bundles. Location references
never participate in bundle assembly. The demonstration fixture is public
PathMNIST metadata and contains no image payload.

## Central ownership

PostgreSQL stores `connector_asset_mirrors` and append-only
`connector_asset_mirror_versions`. They have no foreign key or service path to
DataProduct, ModelProduct, Application, Contract, ComputeJob, ComputeRun,
Artifact, ReleasePackage, DownloadGrant, or MaterializationPlan.

## Capabilities

Enabled: local asset registry, metadata sync, data-quality summary.

Disabled: raw data transfer, model transfer, execution, artifact egress, hard
isolation.
