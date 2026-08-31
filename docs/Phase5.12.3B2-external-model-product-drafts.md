# Phase 5.12.3B2 External Model Product Drafts

Date: 2026-07-27

## Decision

`Phase 5.12.3B2 accepted = true`

The three B1-eligible records were converted through the authenticated operator API
into immutable, metadata-only `ModelProduct` drafts:

| External model | Product code | Model version ID | State |
|---|---|---|---|
| CONCH | `EXT-MP-AC887C58A0` | `87bf1cd5-3be2-5393-b7cd-34f1998305b2` | draft |
| UNI | `EXT-MP-AB7E9193C7` | `5ad11992-a1db-525b-ab83-03f26a373e7d` | draft |
| Prov-GigaPath | `EXT-MP-EF77C69C86` | `3609c6f0-4b33-5363-8555-dfe6c1f8bc69` | draft |

## Implemented

- Added migration `20260727_0044` and the immutable
  `model_product_external_source_links` table.
- Bound every draft to the exact external record/version, source digest,
  governance profile, governance snapshot digest and all 12 latest review IDs.
- Added operator-only create and authenticated detail APIs.
- Added an API-only idempotent application script.
- Added operator UI status and create action in the external model governance drawer.
- Added model materialization gates to application selection, submission,
  readiness confirmation and compute authorization.

## Runtime Evidence

Canonical Phase 4 demo database after creation:

```text
external model source links = 3
external_metadata_only ModelVersion = 3
ModelProduct total = 4 (1 historical executable + 3 metadata-only)
ComputeRun = 2
```

Every new graph has:

```text
ModelProduct.lifecycle_status = draft
ModelVersion.status = draft
runtime = external_metadata_only
entrypoint_id = external-metadata-only
materialization_status = metadata_only
weight_holder_status = external_upstream
execution_readiness = not_ready
platform_validation = not_validated
review_count = 12
```

The application script was run twice. The first pass created three drafts and
the second pass returned the same three graphs as `existing`; no duplicate rows
were created.

Permission and immutability checks:

```text
hospital.demo create attempt = HTTP 403
source-link UPDATE = rejected by PostgreSQL immutable trigger
```

## Validation

```text
Python compileall = passed
new focused pytest = 9 passed
backend test suite = exit 0 (PostgreSQL-gated suites skipped by test configuration)
frontend pnpm typecheck = passed
frontend pnpm build = passed
Alembic = one head at 20260727_0044
OpenAPI = 149 operations, 0 duplicate operation IDs
roadshow health = ok
audit chain valid = true
hard_isolation = false
```

The frontend build retains the pre-existing large-chunk warning. Pytest also
reports that its cache directory cannot be created; neither warning changes the
test result.

## Explicit Boundary

This phase does not download or store weights, build an execution image, create
an Executor registry entry, validate model behavior, publish a product, or make
the models selectable in an Application. The digest stored in the model version
describes the metadata manifest, not model weights.

`eligible_for_model_draft` means only that the governance evidence is sufficient
to create this metadata record. It is not evidence that a model is suitable for
local materialization, executable on this computer, clinically valid, or approved
for production use.

The next phase may submit one or two drafts for catalog publication, but must
preserve the same `metadata_only / not_ready / not_validated` boundary.

No tag was created. In particular, `v0.13` was not created.
