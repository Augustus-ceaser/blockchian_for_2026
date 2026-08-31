# Phase 5.12.6B-R Acceptance

## Result

`Phase 5.12.6B-R accepted = true`

The canonical database is at `20260728_0049`. One reference relation is
`verified` at `platform_verification`:

- Relation: `df7ec70c-f4cb-5df7-842d-bf2af6d66961`
- executed Evidence: `6acbdf33-5bc7-55d6-aa10-24c60c81475a`
- verified Evidence: `cca93ce3-5ab7-5bc5-9b51-3181897b2427`
- historical Run: `e8c997ae-90f7-44d7-955a-6fa169cc9a7b`
- Artifact: `c79aa0bb-b0cd-4fb2-a80f-06d7281a7c1c`, still quarantined
- ReleasePackage: `cbe04697-376b-4de8-b58a-18643037851e`
- DownloadGrant: `ffaab076-1d50-4562-b6e8-d8b0e865cbb7`, exhausted 1/1

The run processed 20 samples, failed 0, predicted 19 correctly, and recorded
aggregate accuracy `0.95` on CPU. This is non-clinical engineering evidence
for the fixed demonstration subset only.

## Count Boundary

| Object | Before | After |
|---|---:|---:|
| ComputeRun | 2 | 2 |
| Artifact | 2 | 2 |
| ReleasePackage | 2 | 2 |
| DownloadGrant | 2 | 2 |
| DatasetModelRelation | 6 | 7 |
| DatasetModelEvidence | 6 | 8 |
| MinIO objects | 30 | 30 |

The six external public relations remain static. CONCH and UNI remain
metadata-only, unmaterialized, and non-executable; their gated access blockers
and licenses were not expanded.

## Verification

- backfill replay: idempotent, no duplicate relation/evidence/audit events;
- negative clones: failed Run, Fake adapter, version mismatch, and unavailable
  package all rejected;
- backend: all runnable tests passed; 66 dedicated-database suites skipped by
  their existing environment gates;
- frontend: 69/69 passed; typecheck and production build passed;
- OpenAPI: 163 paths, 169 unique operation IDs;
- Alembic current/head: `20260728_0049`;
- Alembic check: known repository-wide schema/model baseline drift remains,
  including pre-existing schema qualification and materialization model import
  noise; it did not identify a runtime migration failure;
- Compose configuration and all PowerShell script parses passed;
- audit chain valid; MinIO object metadata confirmed without downloading ZIP;
- browser: four accounts authenticated; 390x844 operator matrix had no
  page-level overflow, Console errors, failed requests, or external requests.

No tag or `v0.13` was created.
