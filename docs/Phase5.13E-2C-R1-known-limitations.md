# Phase 5.13E-2C-R1 Known Limitations

- `hard_isolation=false`; this is a loopback engineering prototype, not a
  hospital production, clinical, certification, or arbitrary-workload claim.
- Only fixed `PATHMNIST_REFERENCE_V1` is supported. No user code, model,
  dataset, runtime package, network access, or dynamic download is allowed.
- Central PostgreSQL and Connector SQLite do not share a distributed
  transaction. A signed idempotent receipt is confirmed before Worker
  dispatch.
- The real immutable ReferenceExecution binding contains every required field,
  but its stored `schema_version` label was overwritten by the Task binding
  label. The serializer order is fixed for future records; the accepted
  historical record was not modified.
- Two simultaneous rejected replay requests created one hash-valid local audit
  branch. The audit writer is now serialized and the preserved graph verifies
  every contiguous event and digest; no event was removed or rewritten.
- The first start attempt was correctly rolled back because central Status
  event IDs and local attestation IDs are different namespaces. Matching now
  uses signed digest plus Executor binding. No Task or execution survived that
  rejection.
- Sixty-six backend PostgreSQL suites remain gated by dedicated destructive
  test database variables. All 243 runnable backend tests passed.
- The new Artifact is only `quarantined`. It has not been scanned, reviewed,
  approved, declared evidence eligible, bundled, registered centrally,
  released, or downloaded.
- R2 and R3 have not started. The release-candidate tag remains unchanged.

