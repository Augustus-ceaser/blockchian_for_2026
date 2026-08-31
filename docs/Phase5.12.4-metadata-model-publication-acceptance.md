# Phase 5.12.4 Metadata-only Model Publication Acceptance

Date: 2026-07-27

## Decision

`Phase 5.12.4 accepted = true`

CONCH and UNI passed the independent catalog publication workflow and are now
discoverable as external public model products. Prov-GigaPath remains a draft.

| Model | Product code | Product/version | Publication | Runtime boundary |
|---|---|---|---|---|
| CONCH | `EXT-MP-AC887C58A0` | active / approved | active | metadata only |
| UNI | `EXT-MP-AB7E9193C7` | active / approved | active | metadata only |
| Prov-GigaPath | `EXT-MP-EF77C69C86` | draft / draft | none | metadata only |

## Publication Meaning

Publication means only that an authenticated user may discover the governed
metadata entry in the public model catalog. It does not mean that MedTrust:

- owns, hosts or may redistribute the upstream weights;
- downloaded or loaded any weight or repository;
- registered an Executor or built an execution image;
- confirmed schema compatibility with a dataset;
- executed or validated the model;
- approved clinical, commercial or production use.

All three external product graphs retain:

```text
materialization_status = metadata_only
weight_holder_status = external_upstream
execution_readiness = not_ready
platform_validation = not_validated
application_eligibility = false
compute_eligibility = false
```

## Independent Workflow

- `catalog.curator.demo` submits an eligible immutable draft.
- `operator.demo` independently approves or returns it.
- self-review and non-curator submission are denied.
- the review task binds the source digest, governance snapshot digest, all
  twelve current review IDs, submitter and reviewer.
- publication reuses the native model version and publication state while the
  external adapter preserves the metadata-only boundary.

The API-only script
`scripts/apply_phase5124_model_publications.py` is idempotent. Its second run
returned the original publication IDs and added no duplicate review or
publication.

## Runtime Evidence

The workflow was first exercised on an isolated clone of the canonical
PostgreSQL database and then applied through the same authenticated API to the
canonical database.

```text
external model products = 3
published metadata-only products = 2
remaining external drafts = 1
publication review tasks = 2 approved
external submit audit events = 2
external publish audit events = 2
audit chains valid = true
```

Existing business state was unchanged:

```text
Application = 3
Contract = 3
ComputeJob = 3
ComputeRun = 2
MinIO objects = 30
```

No MinIO object was added by this phase.

## Negative Acceptance

```text
hospital submit = HTTP 403
operator submit = HTTP 403
curator approve/self-review = HTTP 403
external models in Application options = 0
source-link UPDATE = rejected
source-link DELETE = rejected
Prov-GigaPath submit then return = draft
```

CONCH and UNI are visible in the public catalog but absent from Application
selection. The low-level demand attachment service also invokes the
materialization gate, so bypassing the UI does not make them selectable.

## Validation

```text
Alembic current/head = 20260727_0046, one head
OpenAPI = 148 paths / 153 unique operations / 0 duplicates
backend pytest = exit 0; 66 environment-gated PostgreSQL tests skipped
focused publication tests = 13 passed
frontend tests = 64 passed
frontend typecheck/build = passed
Python compileall = passed
secret scan = 0
```

Real Chrome acceptance used five independent authenticated accounts. CONCH and
UNI were discoverable by every role; curator and operator governance views
were correct; 390x844, 768x1024, 1366x768 and 1920x1080 had no page overflow.
Console errors, failed requests and external browser requests were all zero.

## Environment And Release Boundary

- `hard_isolation=false`
- PostgreSQL and MinIO remain loopback-only.
- no LAN, firewall, tunnel or remote-preview change was made.
- no tag was created; `v0.13` was not created.
- this remains a non-clinical engineering demonstration.

The next separately reviewed phase is Phase 5.12.5: explicit bidirectional
dataset-model evidence. It must distinguish declared, schema-compatible,
executed, verified, incompatible and execution-failed facts. It must not infer
compatibility from names or publication status.
