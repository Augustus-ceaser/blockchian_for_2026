# Phase 5.13E-2A Hospital Local Executor Runtime Skeleton

## Decision

```text
runtime skeleton implemented = true
runtime prepared = true
task executed = false
execution_enabled = false
hard_isolation = false
```

This phase adds a local, inert Runtime lifecycle to the Hospital Connector. It
proves that an approved Executor can be bound to a fixed security admission and
an empty D-drive sandbox. It does not load a model, read data, create an
Artifact, or start a task.

## Local objects

- `local_executor_runtime_sessions` stores immutable admission bindings and a
  terminal-for-this-phase lifecycle state.
- `local_sandbox_workspaces` stores generated sandbox identity and relative
  location only.
- `local_runtime_lifecycle_events` records append-only lifecycle evidence.
- Connector migration `phase5.13E_0005` permits separate Executors to use the
  same approved image digest without weakening per-Executor admission checks.

## Lifecycle

```text
created -> admitted -> prepared -> destroyed
```

`started` is deliberately unreachable. Both the service layer and a SQLite
trigger reject it. A prepared Runtime is displayed as `Not Executed`.

Preparation is idempotent for the same immutable request digest. Destruction is
terminal and idempotent; a destroyed session cannot be resurrected.

## Interfaces

- Local administrators may prepare and destroy a Runtime.
- The local Runtime page exposes status and immutable binding evidence.
- The central portal is status-only and states that execution remains disabled.
- No Start, Run, or Execute action exists in either interface.

## Explicit exclusions

No PathMNIST or ResNet-18 invocation, model or input loading, container launch,
network call, package installation, dynamic code, ComputeRun, Artifact,
EvidenceBundle, ReleasePackage, DownloadGrant, or MinIO write is part of E-2A.
