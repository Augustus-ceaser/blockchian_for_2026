# Phase 5.13D Policy Control Acceptance

## Decision

```text
Phase 5.13D formally accepted = true
Phase 5.13E started = false
```

Implementation commit: `6a0b9d4`
Central migration: `20260729_0051 -> 20260729_0052`
Connector migration: `phase5.13C_0002 -> phase5.13D_0001`

## Terminal objects

```text
PolicySigningKey = 1 active Ed25519 test key
PolicyBundle = 3
Signed PolicyBundleVersion = 3
ExecutionOrder = 5
accepted = 1
manual rejected = 1
automatic validation_failed = 1
revoked = 1
signed receipts = 4
signed decisions = 3
```

The fifth order is the earlier control order for the pre-registration Connector
and remains `available_for_connector`; it was not executed.

Formal accepted order: `ORD-d8d04c521f01dc8d`.
Manual rejection: `ORD-5392bed042efd959`.
Automatic rejection: `ORD-9fc2f9e605141588`.
Revocation case: `ORD-b3a538210debe604`.

## Boundary evidence

Isolated baseline and final counts are Job/Run/Artifact `3/2/2`, so all deltas
are zero. Execution count is zero. Isolated MinIO is empty. No raw data, model
weights, local paths, patient identifiers, EvidenceBundle, result package, or
download grant was created by this phase.

Central control audit: 65 events, valid, head
`sha256:b79fe07edc6a0dc8e42eb29fe1119af1c6f672009a58c4c3f0f50cc8799f3ea9`.

Local audit: 118 events, valid, head
`sha256:8ec960f821ea1b6d3f5674642d92ccbfbfa1210159a883eb4edcd7695390bbb8`.

## Verification

- Connector tests: 9 passed.
- Backend tests: passed; PostgreSQL-only suites without dedicated test URLs were skipped.
- Frontend tests: 78 passed.
- Typecheck: passed.
- Production build: passed.
- Python compileall: passed.
- Four viewport pairs: passed with zero overflow, Console errors, unexpected
  failed requests, external requests, sensitive exposure, and execution buttons.

## Canonical protection

Canonical PostgreSQL and MinIO were never used for Phase 5.13D writes. Raw dump
hashes differ only in PostgreSQL's random `\restrict` token. A two-line diff
confirmed no schema or data changes; normalized before/after SHA-256 values are
identical.
