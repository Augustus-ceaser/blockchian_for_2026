# Phase 5.13E-2C-R1 Acceptance

## Verdict

```text
Phase 5.13E-2C-R1 accepted = true
R2 started = false
R3 started = false
hard_isolation = false
```

Implementation commit: `568a8a9`.
Central migration: `20260729_0057`.
Connector migration: `phase5.13E_0010`.

## Formal Objects

```text
Status v2 = 520ed4a5-048b-479a-89dd-37e673d0392c / sequence 18
Readiness = 9f321447-9c6e-40b0-87c5-38bdbbd0f4cb
Policy = 10c33c60-2333-4895-bf91-d02e3adef63d
Order = bfda1466-ea6b-470a-8760-e07f56cfd8b2
Snapshot = 1545bd7b-21f4-4bc0-bd4f-3cc4024d261b
Task = 80a91641-c928-4343-96e3-3491ab53558c
Runtime = 2706dc89-93b3-4a2f-9497-1de64e887cc6
ReferenceExecution = dc79e6a7-f818-4e7f-8758-887720e3c4e2
Artifact = 03102c34-be57-4775-82b3-2b15bf381cb1
```

The old expired chain is unconsumed and created no execution object. The old
E-2B-1 execution remains unchanged and `evidence_eligible=false`; no
retroactive authorization ID was added.

## Acceptance Matrix

All 44 required conditions passed:

- fresh Status, Readiness, execution-authorized fixed Policy, Order, receipt,
  independent decision, and Snapshot were created before Task;
- Snapshot was atomically consumed once; local and central Order counts are 1;
- Task, Runtime, and ReferenceExecution were prebound;
- one fixed execution produced 20/19/0.95 and one quarantined Artifact;
- Worker restart and concurrent replay created no duplicate;
- Artifact scan/review, EvidenceBundle, central Job/Run/Artifact, package,
  grant, raw-data transfer, local-path transfer, patient identifier transfer,
  and model-weight transfer all have zero delta;
- all four viewports and central/local/executor audits passed;
- canonical PostgreSQL and MinIO are unchanged;
- tag and remote state are unchanged; R2/R3 are not started.

## Verification

```text
backend = 243 passed, 66 environment-gated skipped
Connector and Worker = 74 passed
frontend = 78 passed
typecheck = passed
production build = passed
Python compile = passed
OpenAPI = 199 paths / 207 unique operation IDs
fresh central migration = 20260729_0057 / 93 medtrust tables
upgraded central migration = 20260729_0057
Connector migration = phase5.13E_0010
Compose config = passed
PowerShell parse = 0 errors
secret/patient/path browser scans = 0 findings
central business audit = valid
central Connector audit = valid / 109 events
local audit = valid / 342 events at browser freeze / one disclosed fork
```

The independent environment increased only the parallel local authorized
execution tables by one each. Central counts remain Job/Run/Artifact `3/2/2`,
packages/grants `2/2`.

Canonical before/after normalized PostgreSQL SHA-256 is identical:
`083fa2a9b76e11a21d7b763c85bd53f8af013f9edf086821945e0c989029dfeb`.
Canonical MinIO remains 30 objects; isolated MinIO remains empty.

The R1-EXEC Compose project is stopped, all seven loopback ports are free, and
the PostgreSQL/MinIO named volumes, SQLite state, sandbox, Artifact, screenshots,
and reports are retained. Canonical PostgreSQL and MinIO remain running.
