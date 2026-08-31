# Phase 5.13B Hospital Connector Control Acceptance

Status: accepted on 2026-07-29.

## Runtime result

- one active Connector: `d8706db7-7a27-4fbb-8a37-f5ebda217959`;
- one revoked test Connector: `8f8dac69-5ade-4cd5-a1a6-1c2838c77b8a`;
- one-time enrollment tokens were stored only as digests and consumed;
- registration required Operator approval;
- client certificates were signed by the D-drive Local Test CA;
- mTLS manifest and heartbeat succeeded;
- pause produced `paused_read_only`; resume returned to active;
- rotation generated a new local private key, activated a new certificate and
  marked the previous certificate `superseded`;
- revoked control communication returned HTTP 403;
- Connector control audit chain and main audit chain are valid.

## Frozen business result

Applications, Contracts, Jobs, Runs, Artifacts, ReleasePackages,
DownloadGrants, DataProducts, ModelProducts and MinIO objects all had delta 0.
Main AuditEvent remained at sequence 353. `hard_isolation=false`.

## Validation

- backend full suite: passed; database-dependent suites skipped where their
  dedicated opt-in environment was absent;
- Phase 5.13B backend tests: 9 passed;
- frontend tests: 75 passed, 0 failed, 0 skipped;
- TypeScript typecheck, production build and Python compile: passed;
- OpenAPI: 177 paths, 184 unique operation IDs;
- existing DB and empty DB migrations: head `20260729_0050`;
- browser: central and local pages passed at 390px and desktop, with no page
  overflow or Console error observed.

The historical global `alembic check` schema-comparison drift remains and was
not introduced or hidden by this phase.

