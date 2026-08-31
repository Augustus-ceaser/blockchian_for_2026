# Phase 5.13E-0 Architecture Freeze Acceptance

## Decision

```text
Phase 5.13E-0 architecture freeze completed = true
Phase 5.13E-1 started = false
Executor implemented = false
Executor started = false
execution enabled = false
hard_isolation = false
```

## Baseline

- start HEAD: `edaa870`;
- release tag: `v0.13-roadshow-evidence-rc -> c65d154`;
- repository/Phase 5.13D isolated migration: `20260729_0052`;
- running canonical migration: `20260729_0051`;
- Connector local migration: `phase5.13D_0001`;
- Phase 5.13D PolicyBundle / signed versions / ExecutionOrder: `3 / 3 / 5`;
- ComputeJob / ComputeRun / Artifact: `3 / 2 / 2`;
- running canonical MinIO objects: 30;
- Phase 5.13D execution and isolated MinIO object counts: `0 / 0`;
- no Git remote;
- baseline worktree clean.

## Frozen decisions

- Central cannot access hospital files, paths, source systems, or Executor.
- Connector mediates policy and local decisions but is not an Executor.
- Executor Manager, sandbox runtime, and execution container are separate
  hospital-side responsibilities.
- Control validation, execution approval, run completion, and egress approval
  are separate decisions.
- Images are pre-staged, digest-pinned, signed, provenance/SBOM/scanner bound,
  and never pulled at runtime.
- The first model is fixed and allowlisted; dynamic code and downloads are
  prohibited.
- Execution has no network or DNS.
- Input and runtime are read-only; scratch/output/logs are bounded.
- Runtime is non-root, unprivileged, capability-free, and resource limited.
- Every output enters local quarantine.
- Independent hospital review controls evidence egress.
- EvidenceBundle contains approved minimal evidence and no raw input,
  patient-level data, local path, raw Artifact, or unreviewed log.
- The threat model and negative-test groups are frozen.
- The first eventual execution is fixed PathMNIST + fixed ResNet-18 on the
  public/non-clinical demonstration subset.

## Change boundary

This phase changes documentation only. It creates:

- no backend, frontend, Connector, or infrastructure code;
- no API, migration, table, or database record;
- no Executor, container, image, model, Job, Run, LocalRun, Artifact, or
  EvidenceBundle;
- no data/model download or transfer;
- no MinIO object;
- no capability flag change;
- no tag.

## Known risks

- The design has not been implemented or attack-tested.
- Container isolation cannot protect against a compromised host.
- Production key custody, IAM/MFA, monitoring, incident response, and
  independent assessment remain absent.
- Model and aggregate-output privacy risks need task-specific review.
- Existing repository-wide Alembic reflection drift remains known and
  unchanged.

## Phase 5.13E-1 readiness

`Phase 5.13E-1 ready = true` only for an inert Executor control skeleton after
this document set is reviewed. It is not authorization to run a model.
Phase 5.13E-1 must preserve all prohibitions in
`Phase5.13E-0-first-execution-scope.md`.
