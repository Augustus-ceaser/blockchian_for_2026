# Phase 5.12.2 External Model Catalog Connector

MedTrust now synchronizes the versioned LXLTX public medical model candidate
catalog as metadata only.

## Architecture

- Shared: `ExternalCatalogSource`, `ExternalCatalogSyncRun`, bounded GET client,
  SSRF/transport checks, ETag handling, D-drive snapshots and audit chain.
- Model-specific: `ExternalModelRecord`, `ExternalModelVersion`, manifest and
  record validation, model presentation.
- Source: `lxltx-public-medical-models`
- Resource kind: `model`
- Schema: `1.0`
- Catalog: `2026.07.27-5b4e0326`
- Models SHA-256:
  `5b4e032610d71328b1a17e9a04300226c156dfce508a2f7eba98f49887908e8d`

The connector requests only the configured catalog manifest and static models
JSON. It never requests paper, repository, model-card, training-data or weight
URLs. Local HTTP is permitted only for the explicit local/roadshow setting;
remote-preview and production reject it.

All imported models remain `not_materialized`, have no execution image and are
not `ModelProduct` objects. `public_available` is an upstream metadata value,
not proof that MedTrust possesses or can execute the weights.
