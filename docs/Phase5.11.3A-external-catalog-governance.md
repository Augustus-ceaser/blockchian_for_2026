# Phase 5.11.3A External Catalog Governance

## Scope

Phase 5.11.3A adds a governance overlay to the 982 immutable external catalog
records. It does not change `ExternalDatasetRecord`, source payloads, catalog
versions, or the Phase 5.1-5.11.2 business state machines.

The overlay contains:

- one current `ExternalDatasetGovernanceProfile` per external record;
- append-only `ExternalDatasetGovernanceReview` decisions;
- non-destructive `ExternalDatasetDuplicateResolution` records.

No external source URL is visited by governance calculation. No dataset,
model, or weight is downloaded. No DataProduct, ModelProduct, Application, or
ComputeJob is created.

## Rules

The fixed primary-state priority is:

`rejected > blocked > duplicate_pending > in_review > needs_license_review >
needs_source_review > needs_access_review > metadata_incomplete >
eligible_for_draft > unreviewed`.

Automatic initialization uses only synchronized metadata. It does not create
human Review rows or infer license/access conclusions. Legacy HTTP is a
warning; missing or malformed links are blockers. Duplicate members remain
independent records and are never deleted or merged.

`eligible_for_draft` only permits entry to a future DataProduct draft flow. It
does not mean downloaded, owned, redistributable, published, or executable.

## API

Four authenticated roles can read:

- `GET /api/v1/external-catalog/governance/summary`
- `GET /api/v1/external-catalog/datasets/{record_id}/governance`
- `GET /api/v1/external-catalog/duplicate-groups`
- `GET /api/v1/external-catalog/duplicate-groups/{group_id}`

Only the operator can write:

- `POST /api/v1/external-catalog/datasets/{record_id}/reviews`
- `POST /api/v1/external-catalog/duplicate-groups/{group_id}/resolve`
- `POST /api/v1/external-catalog/governance/recalculate`

The client cannot set `primary_status` or `productization_eligible`; the domain
service always recalculates them. Writes require idempotency keys and produce
AuditEvent/Outbox evidence.

## Database

- `20260727_0035`: governance tables, constraints, indexes, and append-only DB trigger.
- `20260727_0036`: governance audit vocabulary.
- `20260727_0037`: formal audit contract names.
- `20260727_0038`: duplicate resolution status/type database constraints.

Downgrades remove only Phase 5.11.3A objects and vocabulary.
