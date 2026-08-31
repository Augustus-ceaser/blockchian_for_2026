# Phase 5.13E-1B Executor Security Gate Alpha Acceptance

## Decision

```text
Phase 5.13E-1B accepted = true
Executor security admission gate = complete
Executor execution = false
execution_enabled = false
hard_isolation = false
Phase 5.13E-2 planning ready = true
Phase 5.13E-2 execution authorized = false
```

Implementation commit: `8885bdc`.

No tag was created or moved. The release tag remains
`v0.13-roadshow-evidence-rc -> c65d154`.

## Implemented scope

The Hospital Connector now records three separate local control objects:

- immutable `ExecutorSecurityProfile`;
- governed `ExecutionImageManifest`;
- append-only `ExecutorAdmissionCheck`.

The security profile freezes the approved Executor capability into explicit
network, filesystem, privilege, Docker socket, runtime-download and bounded
resource policies. Image manifests have candidate, approved, deprecated and
revoked states. Admission checks bind one active Executor, its latest security
profile and one digest-pinned image manifest.

An approved admission means only that the recorded preflight checks passed. It
does not create a task, enable execution, load an asset or start a container.

## Security policy

The accepted profile requires:

```text
network_mode = none
filesystem_mode = readonly_input
rootless = true
privileged = false
docker_socket_access = false
runtime_download = false
execution_enabled = false
hard_isolation = false
```

CPU, memory, disk, process and timeout limits must all be present and remain
inside the frozen ranges. The image must use a SHA-256 digest, have a verified
local-alpha signature, pass its security scan and be in `approved` state.
Mutable `latest` references are rejected.

The local-alpha image signature uses a Connector-local HMAC key stored in the
isolated D-drive Connector state. This proves control-flow integrity for the
prototype; it is not a production image-signing or key-custody claim.

## Negative tests

The test suite rejects:

- mutable `latest` image references;
- unknown or mismatched image digests;
- unsigned images;
- revoked images;
- network-enabled profiles;
- root execution;
- privileged containers;
- Docker socket access;
- runtime downloads;
- missing or invalid resource policy.

The earlier E-1A certificate, registration, replay, sequence and revoked
Executor rejection tests remain active.

## Runtime evidence

The retained isolated acceptance environment contains:

```text
valid security profiles = 1
image manifests = 1 revoked
admission checks = 1 approved + 1 rejected
admission execution_enabled total = 0
```

The browser first recorded an approved admission. It then revoked the exact
image manifest and recorded a rejected admission. Failed and successful
decisions remain visible; no records were deleted or rewritten.

## Zero-execution evidence

```text
ComputeJob = 3 -> 3
ComputeRun = 2 -> 2
Artifact = 2 -> 2
EvidenceBundle = not implemented -> not implemented
isolated MinIO objects = 0 -> 0
Executor task count = 0
```

No model, dataset, script or image was downloaded. No runtime image was pulled
or started. The approved manifest is metadata only.

## Verification

- Hospital Connector: 22 passed.
- E-1B security-gate tests: 13 passed.
- Backend: full suite exited successfully; 196 passed and 66 environment-gated
  tests skipped.
- Frontend: 78 passed, 0 failed.
- TypeScript typecheck: passed.
- Production build: passed.
- Python compileall: passed.
- OpenAPI: 204 operations with no missing or duplicate operation IDs.
- Central Alembic: one head at `20260729_0054`.
- Connector local migration: `phase5.13E_0003`.
- Four browser viewports (390, 768, 1366 and 1920): zero overflow, Console
  errors, failed requests, external requests and Execute buttons.
- Connector local audit: 192 events, valid.

Repository-wide `alembic check` reflection drift remains the known pre-existing
issue documented in E-1A. E-1B creates no central schema and does not alter that
drift.

## Next gate

Phase 5.13E-2 may now be planned for one fixed PathMNIST + fixed ResNet-18
demonstration task, but this acceptance does not authorize execution. E-2 needs
a separate explicit scope, baseline snapshot, execution-container design,
local Artifact quarantine and EvidenceBundle review gate before any run.
