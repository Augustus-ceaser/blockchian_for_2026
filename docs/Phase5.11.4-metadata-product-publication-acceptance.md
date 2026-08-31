# Phase 5.11.4 Metadata Product Publication Acceptance

## Result

Phase 5.11.4 is accepted on 2026-07-27. Three governed external public
dataset records were published through the native DataProduct lifecycle as
discoverable metadata products. Publication does not mean that MedTrust holds,
downloaded, may redistribute, or may compute over the upstream datasets.

Published:

| Product | Version ID | Published at (UTC) |
| --- | --- | --- |
| CPTAC-COAD | `99b88ccf-d4f1-5ce3-b8d4-f4ebff0bf775` | `2026-07-27T09:57:57.462844Z` |
| CAMELYON17 | `a756c1fa-b318-52e9-a9c7-11c2c0483651` | `2026-07-27T09:57:57.872623Z` |
| HyperKvasir | `ab2d1015-20e0-55ce-aeef-1565707e4cc2` | `2026-07-27T09:57:58.225648Z` |

Remaining drafts are Hungarian-Colorectal-Screening and 4D-Lung. CPTAC-BRCA
remains archived with its immutable historical SourceLink.

## Boundaries

- Curator: MedTrust Public Data Catalog Curator / `catalog.curator.demo`.
- Reviewer: platform operator / `operator.demo`.
- The curator is not described as the upstream rights holder.
- `materialization_status=metadata_only`.
- `data_holder_status=external_upstream`.
- `execution_readiness=not_ready`.
- Application eligibility is false.
- Backend Application, readiness and ComputeJob paths reject these versions
  with `DATA_PRODUCT_NOT_MATERIALIZED`.
- No dataset, model, Application, Contract, ComputeJob, Artifact or MinIO data
  object was created by this phase.
- `hard_isolation=false` remains unchanged.

## Final Counts

| Item | Result |
| --- | ---: |
| External catalog records | 982 |
| Governed eligible candidates | 9 |
| Active external products | 5 |
| Published metadata-only products | 3 |
| Remaining drafts | 2 |
| Archived test products | 1 |
| Active SourceLinks | 5 |
| Historical SourceLinks | 6 |
| Materialized external products | 0 |
| Executable external products | 0 |
| Application increase | 0 |
| Contract increase | 0 |
| ComputeJob increase | 0 |
| Invalid audit chains | 0 |

## Verification

- Focused unit and PostgreSQL integration tests: 8 passed.
- Full backend suite: passed; PostgreSQL suites without dedicated variables
  retained their established skips.
- Frontend: 57 passed, 0 failed; typecheck and production build passed.
- Python compile, Compose config and all PowerShell parser checks passed.
- OpenAPI: 129 paths, 133 operation IDs, 0 duplicates.
- Alembic current/head: `20260727_0041`.
- `alembic check` retains the previously recorded schema/FK autogenerate noise;
  no new Phase 5.11.4 table drift was hidden.
- Five isolated browser sessions passed public discovery and authorization
  checks at 390x844 with no page overflow, Console errors, unexpected network
  failures or external requests.
- The API-only publication script replayed without creating duplicate events.
- Audit chain is valid.

No tag was created and `v0.13` was not created.
