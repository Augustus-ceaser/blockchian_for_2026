# Phase 5.11.3B2 First External DataProduct Drafts

## 1. Acceptance

**Active B2 draft objective: accepted.** Five real B1-eligible external public
dataset candidates now have metadata-only DataProduct drafts. The phase does
not claim that exactly five database rows were ever inserted: one additional
BRCA draft was created by a controlled negative permission test, then archived
through the formal API so its audit trace remains intact.

Branch: `phase5.10-lan-deployment-wip`

Baseline: accepted Phase 5.11.3B1 commit `7a6eea7`

Migration head: `20260727_0040`

No tag was created. `v0.13` was not created.

## 2. Runtime State Evidence

| Object / invariant | B1 baseline | After B2 | Result |
| --- | ---: | ---: | --- |
| ExternalDatasetRecord | 982 | 982 | unchanged |
| ExternalDatasetVersion | 982 | 982 | unchanged |
| GovernanceProfile | 982 | 982 | unchanged |
| DataProduct | 1 | 7 | 5 active drafts + 1 archived test graph |
| DataProductVersion | 1 | 7 | one draft version per external graph |
| DataResource | 1 | 7 | metadata resource only |
| DataProductExternalSourceLink | 0 | 6 | 5 selected + 1 archived cleanup |
| Active DataProductPublication | 1 | 1 | no new publication |
| ModelProduct | 1 | 1 | unchanged |
| Application | 3 | 3 | unchanged |
| ComputeJob | 3 | 3 | unchanged |
| ComputeRun | 2 | 2 | unchanged |

The five active selected products are all `draft`; the sixth external graph is
`archived` and is retained only for traceability. No source record, source
version, governance profile, or review was changed by draft creation.

## 3. Formal API and Authorization

Implemented endpoints:

- `GET /api/v1/external-catalog/datasets/{record_id}/data-product-draft`
  - authenticated read for all four roles;
- `POST /api/v1/external-catalog/datasets/{record_id}/data-product-draft`
  - operator only, idempotency key required;
- `POST /api/v1/external-catalog/datasets/{record_id}/data-product-draft/discard`
  - narrow operator-only archival cleanup for an unpublished metadata draft.

The five selected graphs were created or replayed through the operator route.
The same CPTAC-COAD request replayed to the same graph; reusing a request key
with a different curator note returned `409`. Hospital, model, and requester
write attempts returned `403`. An ineligible CoNIC record returned `409` and
created no product.

The reusable API-only command is
`scripts/apply_phase5113b2_external_product_drafts.py`. It performs no SQL,
does not download upstream data, and is safe to rerun against already-created
selected drafts because it reads the formal draft endpoint first.

## 4. Traceability and Safety

The new `data_product_external_source_links` table uses restrictive foreign
keys, unique product/version/record links, and fixed checks for
`metadata_only`, `external_upstream`, and `not_ready`. Each link preserves:

- immutable external record and current version IDs;
- source record digest;
- governance profile and governance snapshot digest;
- source, license, access, and productization Review IDs;
- upstream official URL;
- curator organization and creator;
- redistribution conclusion without turning it into ownership.

The DataProductVersion and resource contain only catalog metadata and explicit
policy/provenance statements. No file, image, weight, source payload, or
execution input was copied. No publication, application, contract, job, run,
result, or download grant was created.

## 5. Validation

- frontend focused tests: `57 passed`;
- `pnpm typecheck`: passed;
- `pnpm build`: passed; only the existing large-chunk warning remains;
- backend focused productization tests: `3 passed`;
- backend full pytest: exit code 0; environment-gated PostgreSQL and controlled
  smoke suites remain skipped when their dedicated variables are absent;
- Python compile: passed with `backend\\.venv\\Scripts\\python.exe`;
- migration: `alembic upgrade head` and `alembic current` at `20260727_0040`; the source-link UPDATE/DELETE guard is present;
- browser: four isolated real accounts at `390x844`; operator saw the draft,
  non-operator roles had read-only views, all layouts were contained, no page
  errors or unexpected Console errors remained, and no external requests were
  observed;
- OpenAPI and route checks: the two draft command paths are present and
  operation IDs are unique;
- `hard_isolation=false` remains unchanged.

`alembic check` still reports a pre-existing repository-wide schema/FK diff
from the current Alembic environment configuration. It is not used as the
B2 acceptance gate; the new migration was applied and the head/table/constraint
structure was checked directly.

Final runtime state: backend, frontend, workers, coordinator and gateway are
stopped; ports `8000`, `5173` and `8080` are free; PostgreSQL and MinIO remain
bound to `127.0.0.1` only.

## 6. Remaining Boundary

B2 creates a governed metadata catalog entry, not a held or downloadable data
asset. The five products are not publishable or executable. A later phase must
separately revalidate upstream license/access terms, decide whether any source
may be materialized, and only then consider the existing product review and
publication state machine.
