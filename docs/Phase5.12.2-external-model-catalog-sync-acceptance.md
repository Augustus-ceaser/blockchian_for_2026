# Phase 5.12.2 External Model Catalog Sync Acceptance

## Runtime

| Check | Result |
| --- | --- |
| First sync | HTTP 200, received 16, inserted 16 |
| Second sync | HTTP 304, inserted 0, updated 0, unchanged 16 |
| Records | 16 |
| Versions | 16 |
| Non-materialized | 16 |
| Weight files downloaded | 0 |
| Model repositories cloned | 0 |
| Model inference calls | 0 |
| New executable ModelVersion | 0 |
| Audit chain | valid |

Snapshots were written under the three `D:\MedTrustData\model-catalog-*`
roots. A real Docker run exposed and fixed cross-volume atomic rename handling:
the validated cache file is copied to a same-volume `.partial`, digest checked,
then atomically renamed.

Four isolated browser sessions (`hospital.demo`, `model.demo`,
`requester.demo`, `operator.demo`) displayed all 16 records through the
`127.0.0.1:8080` gateway. The 390x844 viewport had no page-level overflow and
no browser request reached the catalog website, GitHub, Hugging Face, a weight
file or an inference API. Non-operators received HTTP 403 from sync.

## Regression

- Backend: 137 passed, 66 environment-dependent skipped
- Frontend: 60 passed
- TypeScript and production build: passed
- OpenAPI: 136 paths, 140 unique operation IDs
- Python compile and migration to `20260727_0042`: passed

The currently mounted Docker database volume had zero
`ExternalDatasetRecord` rows before/after this model sync and therefore is not
the earlier Phase 5.11 acceptance data volume. This report does not claim that
the 982 dataset catalog is present in this runtime volume.
