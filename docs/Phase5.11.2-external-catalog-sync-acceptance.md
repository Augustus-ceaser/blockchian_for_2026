# Phase 5.11.2A Real Catalog Sync Acceptance

Status: **accepted**

## Docker and migration

- Docker Desktop 4.83.0 restored from `D:\Apps\DockerDesktop`.
- Engine 29.6.2, Linux/amd64.
- Existing PostgreSQL/MinIO volumes and Coordinator image remained intact.
- Existing `medtrust_phase4_demo` migrated from `20260725_0032` through
  `20260727_0033` and audit completion migration `20260727_0034`.
- A separate empty database migrated through the complete chain and was then
  removed.
- No reset, volume deletion, prune, firewall, LAN, Tunnel, or public deployment.

## Source and first sync

- Source: `lxltx-public-medical-datasets`
- Type: `versioned_rest_catalog`
- Local URL: `http://127.0.0.1:3000/api/v1`
- Catalog version: `2026.07.27-12d5f08c`
- Records expected/received: 982/982
- HTTP: 200
- Inserted/updated/unchanged/stale: 982/0/0/0
- Records/versions: 982/982
- Missing current versions/orphan versions: 0/0

## ETag and failure behavior

- Second sync returned HTTP 304 and `not_modified`.
- Inserted/updated/stale: 0/0/0; unchanged: 982.
- Record/version counts remained 982/982.
- Digest and successful catalog version remained unchanged.
- `last_synced_at` updated and Source returned to `ready`.
- With the catalog stopped, a new run ended `failed` with code
  `upstream_unreachable` and the fixed redacted summary
  `Configured catalog source is unreachable.`
- Existing records, versions, and snapshots remained intact.

## D-drive evidence

- Manifest, datasets, and provenance files exist under `D:\MedTrustData`.
- Dataset SHA-256:
  `12d5f08c12da5092c429a54477420c084ca2198c975591d484c68170da923c3b`
- Record count: 982.
- Partial files: 0.
- Provenance token/cookie/password/user-path findings: 0.

## Quality and boundaries

- Duplicate names: 8 groups / 17 records.
- Duplicate URLs: 45 groups / 137 records.
- Missing URLs: 2.
- Malformed links: 1.
- Legacy HTTP links: 60.
- Unknown licenses: 982.
- Data/model downloads: 0.
- DataProduct/ModelProduct created: 0.
- Existing business counts remained data/model/application/contract/job/run/artifact
  = `1/1/3/3/3/2/2`.

## Audit and browser

- Audit events include source-created, sync-started, succeeded, not-modified,
  and failed summaries.
- Audit chain verification returned valid.
- Hospital, model, requester, and operator sessions independently read 982
  records.
- Non-operator direct sync calls returned 403; operator returned 200.
- No browser request reached port 3000, `host.docker.internal`, MinIO, or an
  external source URL.
- No page errors or unexpected network failures.
- Expected Console messages came only from the deliberate anonymous 401 and
  non-operator 403 probes.
- 390x844, 768x1024, 1366x768, and 1920x1080 had no page-level overflow.

## Regression

- Backend available suite passed; PostgreSQL suites without dedicated configured
  test databases retained their existing skips.
- External connector focused tests: 9 passed.
- Frontend: 52 passed, 0 failed.
- Typecheck, production API build, Python compile, OpenAPI operation-ID
  uniqueness, Compose config, Alembic single head, and `git diff --check`
  passed.

No `v0.13` tag was created.
