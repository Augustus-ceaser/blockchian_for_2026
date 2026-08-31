# Phase 5.12.2 Model Catalog Integration Design

## Decision

Reuse the mature external catalog source, sync-run, HTTP/SSRF, response-size,
atomic snapshot and audit infrastructure. Add model-specific records, versions,
validation and presentation. Do not place model fields in dataset records and do
not copy the transport client.

## Shared Infrastructure

`ExternalCatalogSource` gains `resource_kind` (`dataset` or `model`). A source is
still independently keyed by `space_id + source_code`, so the dataset and model
feeds keep separate base URLs, ETags, catalog versions and last-successful
digests. `ExternalCatalogSyncRun` gains `resource_kind` and `models_digest`; the
existing dataset fields remain intact for compatibility.

The model service reuses the existing URL validator and bounded GET client. Only
the configured source base URL may be requested. Record-level paper, repository,
model-card and weight metadata URLs are never requested by MedTrust.

## Model Domain

`ExternalModelRecord` stores normalized metadata, current digest, discovery
timestamps and catalog status. `ExternalModelVersion` stores immutable normalized
payload and source evidence. A version is created only when the record digest
changes. Missing records become `stale`; they are never deleted.

All synchronized records must have `execution_status=not_materialized`.
`weights_status=public_available` is upstream metadata only. It never means that
MedTrust downloaded, verified or can execute the model.

## Sync Protocol

1. GET `/model-catalog/manifest` with the last successful ETag.
2. A 304 creates a `not_modified` SyncRun and does not request models.
3. For 200, validate schema, catalog identity, record count, digest and
   `weight_assets_included=false`.
4. GET the versioned static `/catalog/v1/models.json`.
5. Verify SHA-256 and all record invariants before any database mutation.
6. Write manifest, models and provenance through a D-drive `.partial` staging
   directory, then atomically rename.
7. Apply records and versions in the same database transaction.

Failures preserve the last successful source state and snapshots. Error summaries
are bounded and do not include credentials, local paths or record-level URLs.

## Authorization

All four demo roles may list and inspect model candidates. Only the space
operator may inspect source/sync details or trigger synchronization. No endpoint
creates `ModelProduct`, executable `ModelVersion`, dataset-model evidence,
application, contract, job or run.

## Storage

- `D:\MedTrustData\model-catalog-manifests`
- `D:\MedTrustData\model-catalog-snapshots`
- `D:\MedTrustData\model-catalog-provenance`
- temporary: `D:\MedTrustCache\partial`

No model asset directory or weight file is created.
