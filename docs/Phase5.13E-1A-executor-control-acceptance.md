# Phase 5.13E-1A Executor Control Alpha Acceptance

## Decision

```text
Phase 5.13E-1A accepted = true
Executor control plane implemented = true
Executor task execution implemented = false
execution_enabled = false
hard_isolation = false
Phase 5.13E-1B ready = true
```

Implementation commit: `1ec541f`.

No tag was created or moved. The release tag remains
`v0.13-roadshow-evidence-rc -> c65d154`.

## Implemented scope

The Hospital Connector now controls inert Executor identities:

- local identity and CSR generation;
- Connector-local registration review;
- Connector-issued test certificate;
- immutable capability manifest;
- certificate-bound, sequenced heartbeat;
- pause, resume and irreversible revoke;
- central read-only status mirror;
- Connector-local administrator page and central hospital/operator view.

The central platform has no approve, pause, resume, revoke or execute endpoint
for an Executor. Requester and model-provider roles cannot read Executor status.

Central migrations are `20260729_0052 -> 0053 -> 0054`. Connector-local
migrations are `phase5.13D_0001 -> phase5.13E_0001 -> phase5.13E_0002`.
The second migrations correct sequence uniqueness to the Executor scope while
preserving existing records.

## Terminal evidence

The retained isolated acceptance environment contains:

```text
local Executor registrations = 7 certificate_issued
local Executors = 4 active, 3 revoked
central Executor mirrors = 3
central Executor status events = 4
latest active registration sync = HTTP 200
latest revoked chain sync = HTTP 200, HTTP 200
```

Earlier failed delivery attempts are retained in local sync history. They
document the two defects found during acceptance: a missing certificate
fingerprint request header and Connector-scoped status sequence uniqueness.
Both defects were fixed and the later terminal chains succeeded without
deleting the failed attempts.

## Security boundaries

The capability allowlist is fixed to an inert container declaration:

- `network_mode=none`;
- `filesystem_mode=readonly_input`;
- rootless and all Linux capabilities dropped;
- no runtime install or download;
- bounded CPU, memory, disk, processes and timeout;
- task type declaration limited to `PATHMNIST_REFERENCE`;
- `execution_enabled=false`;
- `hard_isolation=false`.

Tests reject wrong certificates, duplicate registration, heartbeat replay,
non-increasing sequences, revoked heartbeats, unknown capabilities, invalid
digests and non-administrator local actions. Executor registration cannot
self-activate.

No execution container, model, dataset, script, command, local path, raw data,
Job, Run, Artifact or EvidenceBundle is accepted by the status schema.

## Zero-delta evidence

The isolated baseline and terminal business counts are unchanged:

```text
ComputeJob = 3 -> 3
ComputeRun = 2 -> 2
Artifact = 2 -> 2
EvidenceBundle = not implemented -> not implemented
isolated MinIO objects = 0 -> 0
Executor execution count = 0
```

No model or dataset was loaded or downloaded. No task was dispatched.

## Verification

- Hospital Connector: 14 passed.
- Backend: full suite completed without failures; environment-gated PostgreSQL
  suites were skipped.
- Frontend: 78 passed, 0 failed.
- TypeScript typecheck: passed.
- Production build: passed.
- Python compileall: passed.
- OpenAPI: 204 operations, no missing or duplicate operation IDs.
- Alembic: one head at `20260729_0054`; real PostgreSQL upgrade passed.
- Four browser viewports (390, 768, 1366 and 1920): zero page overflow,
  failed requests, external requests and Execute buttons.
- The only browser Console entry is the expected anonymous `/auth/me` 401
  before each fresh login.
- Central control audit: 69 events, valid.
- Connector local audit: 181 events, valid.

`alembic check` still reports repository-wide reflection drift inherited from
earlier phases, including `asset_materialization_plans` metadata registration,
historical index definitions and constraint naming. This phase did not create
that drift and does not claim the check passed.

## Next gate

Phase 5.13E-1B may implement and attack-test the Executor Security Gate only:
image identity, runtime, network/filesystem and resource-policy decisions.
It must keep execution and all transfer/egress flags disabled. Phase 5.13E-2
is the earliest phase that may request authorization for a fixed task run.
