# Phase 5.13E-2C-R1-EXEC Prebinding Proof

## Formal Chain

| Object | ID | Created before Worker dispatch | Terminal state |
|---|---|---:|---|
| Status v2 | `520ed4a5-048b-479a-89dd-37e673d0392c` | yes | verified |
| Readiness | `9f321447-9c6e-40b0-87c5-38bdbbd0f4cb` | yes | passed |
| Policy | `10c33c60-2333-4895-bf91-d02e3adef63d` | yes | active |
| Policy version | `2b128650-cf4a-41f2-8857-bc40ba3e6a75` | yes | signed immutable |
| Order | `bfda1466-ea6b-470a-8760-e07f56cfd8b2` | yes | consumed 1/1 |
| Connector receipt | `2de5f247-db74-4d2d-be8f-62d5ba098111` | yes | delivered |
| Local decision | `c83020e5-ece1-4bbf-ba3f-84e3d8cfd4d3` | yes | accepted |
| Snapshot | `1545bd7b-21f4-4bc0-bd4f-3cc4024d261b` | yes | consumed |
| Task Manifest | `80a91641-c928-4343-96e3-3491ab53558c` | yes | immutable |
| Input Manifest | `6434685e-f2f3-47e3-9f05-7a80b3793902` | yes | immutable |
| Runtime | `2706dc89-93b3-4a2f-9497-1de64e887cc6` | yes | completed |
| ReferenceExecution | `dc79e6a7-f818-4e7f-8758-887720e3c4e2` | yes | completed |

The SQLite `BEGIN IMMEDIATE` transaction locks and validates the Snapshot,
Order mirror, Policy, local attestation, Admission, image, security profile,
and Executor. It then creates Task, Input, Runtime, ReferenceExecution, and a
signed consumption receipt while changing Snapshot to `consumed` and local
Order to `consumed_count=1`.

The Connector next sends the signed receipt to central PostgreSQL. Central
verifies mTLS identity, signature, payload digest, Order digest, accepted
state, time window, and one-use count. Only after central records the immutable
receipt and `consumed_count=1` does the Connector write the Worker request.
This is a causal two-database protocol, not a claimed distributed transaction.

The Task binding freezes Snapshot, Policy/version, Readiness, Order, Status v2,
receipt, decision, Admission, Connector, Executor, asset, metadata, quality,
model, image, security, resource, task, input, and output-schema digests.
Runtime and ReferenceExecution bind those facts before `started_at`.

