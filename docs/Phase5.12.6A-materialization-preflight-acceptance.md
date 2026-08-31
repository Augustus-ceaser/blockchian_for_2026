# Phase 5.12.6A Materialization Preflight Acceptance

Date: 2026-07-28

## Status

```text
Phase 5.12.6A accepted = true
terminal result = no_materialization_candidate_approved
selected candidate = 0
approved MaterializationPlan = 0
```

Implementation commit: `8bf5285`

This is a successful fail-closed preflight. It is not an asset
materialization or execution acceptance.

## Candidate result

All four published static transformation-compatible pairs were reviewed:

- CAMELYON17 + CONCH: blocked;
- CAMELYON17 + UNI: blocked;
- CPTAC-COAD + CONCH: blocked;
- CPTAC-COAD + UNI: blocked.

CONCH and UNI both require gated Hugging Face access, an institutional
identity, acceptance of individual terms and a private user token. No account
was registered, no access request was submitted and no private credential was
used. Dataset subset manifests and deterministic WSI transformations also
remain unfrozen.

## Implementation

- migration `20260727_0047 -> 20260728_0048`;
- immutable `AssetMaterializationPlan`;
- curator create and submit commands;
- independent operator approve, reject and cancel commands;
- public authenticated plan reads;
- digest, publication, license, access, resource and security approval gates;
- approved and rejected deletion protection;
- approved-plan supersession through a new plan;
- operator materialization-plan portal;
- no download, materialize or execute API.

No canonical plan row was created because no candidate passed preflight.

## Zero-download boundary

```text
data downloads                 0
model weight downloads         0
Git clone / Git LFS            0
Inference API                  0
data materialized              0
model materialized             0
Executor added                 0
Application added              0
Contract added                 0
ComputeJob added               0
ComputeRun                     2
Artifact added                 0
executed evidence              0
execution_failed evidence      0
verified evidence              0
MinIO objects                 30
```

The six relations and six evidence records were not modified.

## Verification

- temporary PostgreSQL full migration to 0047, upgrade, downgrade and
  re-upgrade: passed;
- canonical incremental migration and re-cycle while plan count was zero:
  passed;
- single Alembic head: `20260728_0048`;
- focused backend tests: 4 passed;
- full backend: 161 passed, 66 environment-gated skipped;
- frontend: 69 passed;
- TypeScript typecheck and production build: passed;
- Python compile: passed;
- OpenAPI operations: 169, duplicate operation IDs: 0;
- Compose config: passed;
- PowerShell parse failures: 0;
- audit chains invalid: 0;
- secret scan: no token or private-key match.

Environment-gated PostgreSQL and controlled-execution suites remain skipped
unless their dedicated destructive test URLs and execution assets are
configured. They are not reported as passes.

## Browser

Five isolated authenticated accounts read the plan API successfully:

- `operator.demo`;
- `catalog.curator.demo`;
- `hospital.demo`;
- `model.demo`;
- `requester.demo`.

Each returned HTTP 200 and zero plans. The operator page passed at 390x844,
768x1024, 1366x768 and 1920x1080 with no page-level horizontal overflow,
Console error or external request.

Two requests from the old overview route (`auth/me` and `health/deployment`)
were aborted when the acceptance script immediately navigated to the plan
route. They are navigation cancellation noise, not plan-page failures, and are
reported rather than hidden.

## Boundary

`approved` would not mean downloaded, materialized, ready, executed or
verified. In this canonical result there is not even an approved plan.
Phase 5.12.6B is blocked. The next action is to govern a smaller model with
public unauthenticated weights, a clear permissive weight license, immutable
file metadata and CPU-feasible operation.
