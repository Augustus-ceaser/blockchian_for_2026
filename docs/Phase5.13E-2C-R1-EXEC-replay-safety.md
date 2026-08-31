# Phase 5.13E-2C-R1-EXEC Replay Safety

The formal chain contains exactly one authorized Task, Input Manifest, Runtime,
ReferenceExecution, consumption receipt, and Artifact.

Two concurrent browser replays after completion both returned HTTP 409
`EXECUTION_AUTHORIZATION_ALREADY_CONSUMED`. A Worker restart left all six
counts at one and did not recreate a request or rerun the 20 samples. Dedicated
tests also prove:

- two-connection Snapshot consumption has one winner;
- forced transaction failure leaves no Task;
- a different idempotency payload is rejected;
- signed central consumption is idempotent only for the exact digest;
- immutable Task, Runtime, ReferenceExecution, receipt, and Artifact bindings
  cannot be updated or deleted.

The concurrent rejected requests exposed a pre-existing audit-writer race:
both valid events referenced the same prior digest. No record was deleted or
rewritten. The writer now takes `BEGIN IMMEDIATE`; the verifier checks
contiguous sequence numbers, every event digest, and every reference to a
previously seen digest. The retained log is valid with one disclosed
concurrency fork.

