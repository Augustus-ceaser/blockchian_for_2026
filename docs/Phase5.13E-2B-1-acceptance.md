# Phase 5.13E-2B-1 Fixed Reference Execution Alpha Acceptance

## Verdict

```text
Phase 5.13E-2B-1 accepted = true
fixed reference execution = completed
local ReferenceExecution delta = +1
local quarantined Artifact delta = +1
central ComputeRun delta = 0
central Artifact delta = 0
EvidenceBundle delta = 0
MinIO delta = 0
raw data transfer = 0
model transfer = 0
hard_isolation = false
```

The central deltas correct an inconsistency in the initial instruction. An
unreviewed hospital-local Artifact must not be represented as a central
`ComputeRun` or `Artifact`.

## Execution evidence

- Runtime: `completed`.
- Task: `PATHMNIST_REFERENCE_V1`.
- Samples: 20 fixed public PathMNIST test samples.
- Model: fixed official small-image ResNet-18.
- Result: 19/20 correct, aggregate accuracy `0.95`.
- Dataset digest before/after:
  `sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72`.
- Model digest:
  `sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0`.
- Input files copied to sandbox: 0.
- Output files: exactly 3.
- Local Artifact status: `quarantined`.
- Worker restart/replay: still 1 execution and 1 Artifact.

Output digests:

```text
aggregate_metrics.json
sha256:34a5b5acc62889e8f492ecb886e4a75245a6e3f00353a9bf5079ddedc54f2605

confusion_matrix.csv
sha256:4f4b57502a4bc4fceba24b160cbb3652fa869ac44ac7a0dec900adfc360ddf3e

execution_summary.json
sha256:bf5cb9a6c3a9a5f0f77d33a2607f88520b1ef9aca37d32635e9d739f46fe5017
```

## Business protection

Central terminal counts remained:

```text
ComputeJob = 3
ComputeRun = 2
Artifact = 2
ReleasePackage = 2
DownloadGrant = 2
MinIO objects = 0
```

No EvidenceBundle, release package, download grant, raw-data transfer, model
transfer, or automatic egress was created.

## Verification

- Connector: 32 passed.
- fixed Worker security tests: 12 passed.
- frontend: 78 passed.
- backend: all runnable tests passed; 66 existing environment-gated tests
  skipped.
- frontend typecheck and build passed.
- Python compile validation passed.
- OpenAPI: 204 operations, 0 missing IDs, 0 duplicate IDs.
- central Alembic: one head, `20260729_0054`.
- Connector local migration: `phase5.13E_0006`.
- local audit: valid, 221 events.
- browser: 390x844, 768x1024, 1366x768, and 1920x1080 passed with
  zero overflow, Console errors, failed requests, external requests, upload
  inputs, or arbitrary Execute/Run/Start buttons.
- Worker runtime inspection confirmed no network, non-root, read-only root,
  non-privileged, all capabilities dropped, and bounded resources.

## Next gate

Phase 5.13E-2B-2 may add hospital Artifact scanning and independent hospital
review only after explicit authorization. The current Artifact must remain
local and quarantined. EvidenceBundle and central egress remain prohibited.

## Later Causal Remediation

This E-2B-1 execution remains real but ineligible for evidence because it had
no pre-execution Policy/Order/Snapshot binding. Phase 5.13E-2C-R1 did not
modify or relabel it. Instead, R1 created a separate fresh authorization chain
and one new fixed execution. Its new Artifact is independently quarantined and
must pass future R2 and R3 gates before any evidence or egress claim.
