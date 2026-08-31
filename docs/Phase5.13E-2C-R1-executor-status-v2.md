# Phase 5.13E-2C-R1 Executor Status v2

Date: 2026-07-29

## Decision

The signed Executor fixed-reference readiness attestation milestone is
implemented and verified.

This is an intermediate R1 checkpoint. It is not final R1 acceptance.

```text
Phase 5.13E-2C-R1 Executor Status v2 completed = true
Phase 5.13E-2C-R1 accepted = false
```

## Trust Boundary

The source of truth for the execution image manifest, security profile,
resource policy, capability manifest and admission check remains the Hospital
Connector SQLite database.

The Connector reads those formal objects in one consistent transaction,
creates a strict `hospital_executor_status_v2` payload, signs it with the
existing Connector key and appends it locally. Central receives it through the
existing mTLS ingress and verifies the certificate, signature, digest,
sequence, nonce, freshness and internal proof bindings.

Central stores the signed event and derived mirror indexes. It does not create
duplicate central image, security, resource or admission tables, and it does
not claim to have independently inspected the local objects.

## Protocol

```text
schema_version = hospital_executor_status_v2
event_type = EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION
ready result = READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION
supported task = PATHMNIST_REFERENCE_V1
hard_isolation = false
```

The payload schema forbids additional properties and excludes paths, raw data,
model weights, credentials, connection strings, tokens and Artifact content.
Callers cannot supply or override local object digests.

Only a currently verified v2 event can be used by the central readiness
service. A v1 event, expired event, superseded event, inactive Executor or
unsupported task fails closed.

## Runtime Evidence

The isolated loopback environment produced and synchronized a real v2 event:

```text
local Executor ID = 9d5ffa08-f05c-4df9-a288-67144cef8f0e
central event ID = 4dc8d007-77aa-4a2b-a7fc-524ebc2b9d1b
event sequence = 6
signature verification = verified
internal proof bindings = verified
readiness source recomputation = passed
first receipt created = true
identical replay created = false, same event ID
execution_started = false
hard_isolation = false
```

The local page shows the attestation version, sequence, result, expiry,
delivery state, digest and the `signed / not executed / hard_isolation=false`
boundary. The central page shows fixed-reference policy compilation only and
does not present a general execution-ready claim.

## Integrity Evidence

```text
central audit chain = valid (74 events)
local audit chain = valid (257 events)
central status event UPDATE = rejected by database trigger
local attestation UPDATE = rejected by database trigger
central migration head = 20260729_0055
local migration head = phase5.13E_0008
fresh central migration = passed
20260729_0054 -> 20260729_0055 migration = passed
OpenAPI operation IDs = unique
```

The migration tests used isolated temporary databases, which were removed
after verification.

## Regression Evidence

```text
Connector focused tests = 46 passed
Connector full tests = 55 passed
Central focused tests = 52 passed
Central full tests = 237 passed, 66 environment-gated skips
Frontend tests = 78 passed
Frontend typecheck = passed
Frontend production build = passed
Python compile = passed
```

PostgreSQL integration suites in the broad backend test command remain
environment-gated and were skipped when their dedicated test database
variables were absent. The Status v2 runtime acceptance, fresh migration and
incremental migration checks did not skip.

## No-Execution Evidence

The isolated central counts remained:

```text
ComputeJob = 3
ComputeRun = 2
Artifact = 2
execution-authorized PolicyBundleVersion = 0
execution-authorized ExecutionOrder = 0
execution-authorized ControlReadinessSnapshot = 0
```

The local authorization counts remained:

```text
authorized ReferenceExecution = 0
authorized Artifact = 0
```

No old execution record or identifier was changed. No retroactive binding was
performed.

## Remaining R1 Work

The next stage may consume this verified source when completing the formal
Policy and Order service. It must still create and validate the formal
authorization Snapshot before any execution can be considered.

At this checkpoint:

```text
execution Policy compiled = false
fixed ExecutionOrder created = false
formal Snapshot created = false
formal execution started = false
new Artifact = 0
EvidenceBundle = 0
R2/R3 started = false
R1 accepted = false
```
