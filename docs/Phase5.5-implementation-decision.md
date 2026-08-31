# Phase 5.5 Implementation Decision

Canonical phase date: 2026-07-24.

## Scope boundary

Phase 5.5 ends after creation of audited, pre-dispatch `ComputeJob` records.
It does not create `ComputeRun`, dispatch outbox messages, start an executor,
run inference, process callbacks, or create Artifacts.

## Existing authoritative behavior

- `ContractReadinessConfirmation` already records immutable data, model, and
  platform confirmations for an active `ContractRevision`.
- `create_compute_job` creates a real `ComputeJob` and emits only
  `compute.job.created`; it does not create a Run or dispatch work.
- `validate_compute_job`, `prepare_compute_run`, and `reserve_compute_run`
  are separate later-stage operations and are excluded from this phase.
- The fixed model registry plus verified Connector capability bindings are the
  existing execution-asset authority. No new Executor model is needed.

## Storage decision

The existing `platform_ready` record is not a complete execution-eligibility
snapshot. It lacks an immutable full check matrix, direct bindings to all three
readiness records, and an auditable invalidation record.

Phase 5.5 therefore adds only the following durable facts:

1. An append-only readiness revocation record referencing an immutable
   readiness confirmation.
2. An immutable execution-eligibility snapshot referencing the active contract
   revision, locked data/model versions, the three readiness records, the
   current capability bindings, and a canonical check matrix.
3. An append-only eligibility invalidation record referencing a snapshot.
4. A nullable eligibility-snapshot reference and immutable pre-dispatch quota
   reservation evidence on `ComputeJob`.

All new records are created by backend domain services. Existing readiness
confirmation rows remain immutable.

## Validity and invalidation rule

A readiness record is usable only when it belongs to the active revision and
has no revocation record. An eligibility snapshot is usable only when it has no
invalidation record and a fresh server-side evaluation produces the same
canonical eligibility digest.

Revoking data or model readiness invalidates every still-valid snapshot that
references it. A later platform check can create a new snapshot only after
current data and model readiness are present again. Connector, capability,
contract-window, product/model version, policy, and compatibility changes are
detected by the same fresh evaluation before a job can be created; the
operator check records the resulting invalidation or replacement snapshot.

## Pre-dispatch quota rule

Current `run_count` consumption is represented by `ComputeRun.reservation_ordinal`
and is assigned only by `reserve_compute_run`. Phase 5.5 must not fake that
operation or create a Run.

Instead, creation of a Phase 5.5 job atomically reserves a **pre-dispatch job
slot** on `ComputeJob`. The slot uses the same contract/policy/requester/object
scope and a separate ordinal namespace. The allocation transaction locks the
governing `PolicyConstraint`, counts active pre-dispatch slots plus existing
Run reservations, and rejects creation when the configured `run_count` limit
is reached. This prevents two valid pending jobs when `run_count=1`.

The slot is neither a completed execution nor a `ComputeRun` reservation.
Phase 5.6 must explicitly define how a slot transfers to a Run so it is not
counted twice. Cancellation/release of pre-dispatch jobs is intentionally not
added here because the current domain has no matching job-cancellation command.

## API and authorization decision

- Data provider: confirm or revoke only its contracted data readiness.
- Model provider: confirm or revoke only its contracted model readiness.
- Space operator: run the eligibility check and create a snapshot, but cannot
  impersonate provider readiness.
- Data requester: may view its own contract readiness state and create a job
  only from a currently valid eligible snapshot.

All command endpoints require idempotency keys and append real `AuditEvent`
facts. Phase 5.5 adds readiness-revoked, eligibility-checked,
eligibility-passed, eligibility-blocked, eligibility-invalidated, and
pre-dispatch-slot-reserved vocabulary. These events publish only the normal
audit timeline target; they do not publish `compute.dispatch`.

## UI decision

The `/execution` route becomes the Phase 5.5 execution-readiness workspace,
using existing roadshow request helpers and role identity. It presents
server-authoritative status, locked versions, check results, readiness actions,
eligibility evidence, and pre-dispatch job details. It does not show an
execution progress bar, executor logs, fake HTTP details, or an Artifact view.

## Migration and verification

A new migration is required for the append-only facts, the `ComputeJob`
snapshot/slot fields, immutable-row guards, and the audit catalog additions.
It must support upgrade and downgrade on an empty database and incremental
upgrade from `20260724_0029`.

The Phase 5.4 full regression count was `147 passed / 2 skipped`. The earlier
`142 passed / 5 skipped` result had three additional environment-gated
destructive checks disabled: catalog concurrency, compute concurrency, and the
migration downgrade/upgrade cycle. No test was removed.
