# Phase 5.12.5 Dataset-Model Evidence Acceptance

Date: 2026-07-27

## Status

Phase 5.12.5 is accepted as a static, metadata-only evidence graph.

- Implementation commit: `1f8419e`
- Migration: `20260727_0046 -> 20260727_0047`
- Release tag: none
- `v0.13`: not created
- `hard_isolation`: `false`

This phase does not download data or weights, register an external Executor,
run inference, or create runtime or verification evidence.

## Canonical result

```text
DatasetModelRelation                         6
DatasetModelEvidence                         6
public relations                             6
private/draft relations                      0
external declaration evidence                0
static compatible                            0
static compatible with transformation        4
static incompatible                          2
insufficient metadata                        0
executed                                     0
execution_failed                             0
verified                                     0
```

The absence of external declarations is intentional. The frozen sources did
not provide an exact stable dataset identifier, official collection ID, DOI,
or official URL match that justified a formal declaration.

## Pair matrix

| Data | Model | Static result | Public |
|---|---|---|---|
| CAMELYON17 | CONCH | compatible with transformation | yes |
| CAMELYON17 | UNI | compatible with transformation | yes |
| CPTAC-COAD | CONCH | compatible with transformation | yes |
| CPTAC-COAD | UNI | compatible with transformation | yes |
| HyperKvasir | CONCH | incompatible | yes |
| HyperKvasir | UNI | incompatible | yes |

The four pathology pairs require tissue masking, patch extraction and the
official model transform. Parameters and implementations remain unverified.
The HyperKvasir pairs are incompatible because endoscopy images do not satisfy
the H&E pathology image-tile input contract.

## Evidence integrity

Each relation locks:

- exact DataProductVersion and ModelVersion IDs;
- version snapshot digests;
- external source record digests;
- governance snapshot digests;
- source-link IDs.

`DatasetModelEvidence` is append-only. PostgreSQL rejects `UPDATE` and
`DELETE`. Relation version/source/governance lock fields are also immutable.
New conclusions append a record with `supersedes_evidence_id`; they do not
overwrite history.

Public reads re-check both products, versions and active publications. A later
unpublish/archive therefore hides the relation from ordinary roles while
retaining its evidence.

## API and permissions

Read APIs:

- `GET /api/v1/dataset-model-relations`
- `GET /api/v1/dataset-model-relations/{id}`
- `GET /api/v1/data-products/{id}/model-evidence`
- `GET /api/v1/model-products/{id}/dataset-evidence`

Operator APIs:

- `POST /api/v1/dataset-model-relations/static-review`
- `POST /api/v1/dataset-model-relations/{id}/evidence`
- `POST /api/v1/dataset-model-relations/{id}/recalculate`
- `POST /api/v1/dataset-model-relations/{id}/publish`

The six canonical reviews were created through `operator.demo` and the formal
API. Replaying the same six idempotency keys returned `created=false`.
Requester writes returned `403`. An operator attempt to create `executed`
evidence returned `409`.

## UI

The operator portal includes `/portal/operator/dataset-model-evidence` with:

- a 3 by 2 matrix;
- data, model and status filters;
- structured review fields for modality, data object, format, dimensions,
  resolution, preprocessing, task, output and license/access;
- transformation, blocker and warning capture;
- explicit unexecuted and unverified badges.

DataProduct and ModelProduct details expose the same evidence in both
directions. All related views state:

> Static schema review evaluates metadata and interface conditions only. It
> does not mean the model was downloaded, executed or performance-verified.

No recommendation ranking, run control or weight-download control was added.
Prov-GigaPath remains a draft and is absent from the public matrix.

## Verification

- temporary PostgreSQL upgrade/downgrade/re-upgrade: passed;
- empty PostgreSQL full migration chain: passed;
- canonical incremental migration: passed;
- single Alembic head: `20260727_0047`;
- Evidence update negative test: rejected by PostgreSQL trigger;
- idempotent API replay: six of six returned `created=false`;
- canonical audit chain: valid;
- backend: `157 passed, 66 skipped`;
- frontend: `67 passed`;
- TypeScript typecheck: passed;
- production build: passed;
- Python compile: passed;
- browser matrix: 4 transformed-compatible and 2 incompatible cells;
- browser bidirectional details: passed;
- browser widths 390, 768, 1366 and 1920: no page overflow;
- post-load matrix Console errors: 0;
- post-load failed requests: 0;
- external, weight and inference requests: 0.

The skipped backend suites require their dedicated destructive PostgreSQL or
controlled execution environment variables. They are not reported as passes.
`alembic check` still emits the repository's pre-existing schema-reflection
foreign-key noise; the migration cycle and explicit new constraint/trigger
inspection passed.

## Preserved boundary

```text
ExternalDatasetRecord  982
ExternalModelRecord     16
Application              3
Contract                 3
ComputeJob               3
ComputeRun               2
MinIO objects            30
draft external model      1
```

No source record, governance review, source link or frozen digest was changed.
No new Application, Contract, readiness object, Job, Run, Artifact or MinIO
object was created.
