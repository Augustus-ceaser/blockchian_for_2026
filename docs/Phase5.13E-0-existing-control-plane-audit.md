# Phase 5.13E-0 Existing Control Plane Audit

## Scope and baseline

This is a read-only design audit at start commit `edaa870`. It introduces no
runtime, schema, API, migration, business object, MinIO object, or execution.
The release-candidate tag remains `v0.13-roadshow-evidence-rc` at `c65d154`.

Verified inherited state:

- repository/Phase 5.13D isolated Alembic head: `20260729_0052`;
- currently running canonical Alembic head: `20260729_0051`;
- Connector local migration: `phase5.13D_0001`;
- PolicyBundle: 3;
- signed PolicyBundleVersion: 3;
- ExecutionOrder: 5;
- ComputeJob / ComputeRun / Artifact: `3 / 2 / 2`;
- canonical MinIO objects: 30;
- Phase 5.13D execution count: 0;
- Phase 5.13D isolated MinIO objects: 0;
- `execution_enabled=false`;
- `data_transfer_enabled=false`;
- `model_transfer_enabled=false`;
- `artifact_egress_enabled=false`;
- `hard_isolation=false`.

The Phase 5.13D isolated services are stopped and their retained acceptance
volume contains the `0052` control objects above. Canonical PostgreSQL and
MinIO remain running at `0051` with `3 / 2 / 2` execution objects and 30 MinIO
objects, but this documentation phase does not write to either.

## Existing trustworthy controls

### Connector identity and transport

Phase 5.13B provides one-time enrollment, Operator-reviewed registration,
Local Test CA certificates, mTLS heartbeat, certificate rotation,
pause/resume, revocation, and a disabled-capability manifest. These controls
authenticate a Connector control endpoint. They do not authenticate an
Executor, authorize execution, or establish production PKI.

### Local asset metadata

Phase 5.13C provides local asset identity, immutable versions, quality
profiles, independent local reviews, and a metadata-only central mirror.
Central receives no raw file, patient identifier, local path, database
connection, or model weight. A metadata mirror is not an execution input.

### Signed control policy

Phase 5.13D provides deterministic signed PolicyBundle versions, revocation,
signed control-only ExecutionOrders, Connector validation, independent local
accept/reject, and signed receipts and decisions. The accepted action is
`VALIDATE_POLICY_ONLY`, with `execution_authorized=false`.

### Audit

Central and Connector maintain separate append-only hash chains. Phase 5.13D
accepted one control-only order, manually rejected one, automatically rejected
one, and revoked one after acceptance without creating a Job, Run, Artifact, or
EvidenceBundle.

## Gaps that block execution

The current control plane has no trusted implementation or formally frozen
contract for:

- an independently identified Executor Manager;
- a sandbox runtime or execution container;
- execution-specific local approval;
- immutable ExecutionImageManifest approval;
- ExecutionInputManifest generation and local binding;
- model materialization and verification inside the hospital boundary;
- network, filesystem, process, and resource enforcement;
- LocalRun state and recovery semantics;
- local Artifact quarantine and scanning;
- hospital output review;
- signed EvidenceBundle generation;
- execution revocation timing and deny-cache behavior;
- cleanup proof after normal completion, timeout, or process termination.

## Critical non-equivalences

The following implications are invalid and must remain blocked:

```text
Connector active != Executor registered
Policy valid != execution authorized
Control order accepted != task queued
Metadata synchronized != raw data available
Model cataloged != model materialized
Run succeeded != output releasable
Artifact approved locally != raw Artifact transferable
EvidenceBundle accepted centrally != clinical validity
```

## Required trust split

The target split is:

1. Central proposes a signed, bounded execution request.
2. Connector validates central control facts and local references.
3. A separate local authorization decision may admit a fixed task.
4. Executor Manager enforces an immutable launch specification.
5. Sandbox Runtime executes without central or Internet access.
6. Local Artifact Quarantine receives all output.
7. Hospital reviewers decide whether a minimal EvidenceBundle may leave.
8. Central verifies and registers evidence but cannot reverse a hospital
   rejection.

## Audit conclusion

Phase 5.13D is a sufficient control-plane prerequisite for architecture
planning, but not for implementation or execution. Phase 5.13E-0 may freeze
the design only. Phase 5.13E-1 must remain separately authorized and must not
inherit execution permission from any Phase 5.13D object.
