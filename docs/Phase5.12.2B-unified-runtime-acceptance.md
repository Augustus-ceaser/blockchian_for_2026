# Phase 5.12.2B unified runtime acceptance

## Status

`complete=true`, `unified_runtime_accepted=true`.

The same canonical database now contains:

- 982 external dataset records and 982 versions;
- 982 governance profiles and 80 reviews;
- 5 active external product objects, 6 historical links;
- 3 published metadata-only products, 2 drafts and 1 archived test;
- 16 external model records and 16 versions;
- all 16 models `not_materialized`;
- 0 model weights downloaded and 0 execution-ready external models.

The model sync evidence is preserved in canonical:

- first sync: HTTP 200, received/inserted `16/16`;
- second sync: HTTP 304, changes `0`, unchanged `16`.

Historical state remains: 3 applications, 3 contracts, 3 jobs, 2 runs,
2 artifacts, 2 approved result packages, 2 download grants and one existing
historical ModelProduct. Phase 5.12 created no ModelProduct.

## Runtime and security

- Alembic current/head: `20260727_0042`, one head.
- Audit chain: valid, invalid sequence absent.
- MinIO: historical quarantine and approved-result objects remain present.
- Database merge: none.
- Manual business SQL writes: none.
- Volumes deleted: none.
- Data/model downloads: none.
- Browser: four real roles passed at 390x844; dataset total 982, model total 16,
  no page overflow, Console error, failed request or external request.
- Gateway acceptance binding: `127.0.0.1:8080`.
- Firewall, LAN exposure and Tunnel: unchanged/not enabled.

## Regression

- Backend: 137 passed, 66 environment-gated skipped.
- Frontend: 60 passed.
- Typecheck and production build: passed.
- Python compile: passed.
- OpenAPI: 136 paths, 140 operations, 140 unique IDs.
- Compose base/LAN config: passed.
- PowerShell: 25 scripts parsed.
- `git diff --check`: passed.
- `alembic check`: known pre-existing schema/FK comparison baseline still
  reports operations; current/head and migration execution are valid.
