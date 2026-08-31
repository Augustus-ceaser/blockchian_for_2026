# Phase 5.13E-2B-1 Fixed Reference Execution Design

## Scope

E-2B-1 adds one hospital-local execution capability:

```text
PATHMNIST_REFERENCE_V1
fixed public PathMNIST test indices: 20
fixed ResNet-18 weights
fixed Linux CPU image digest
fixed aggregate-only outputs
```

General execution remains disabled. The browser cannot supply a task type,
model, dataset, path, script, archive, image tag, environment variable, or
command.

## Flow

```text
approved local Executor admission
-> prepared Runtime
-> immutable Task Manifest and Input Manifest
-> atomic request in D-drive sandbox
-> fixed no-network Worker
-> three allowlisted aggregate files
-> hospital-local Artifact in quarantine
```

The Connector never receives Docker access. A pre-started fixed Worker polls
only server-generated sandbox directories. The Worker is the existing pinned
CPU image at:

```text
sha256:3c26323fa51cc80da9459c1ef9e7f4fe1c7f9f36cab110d7388706e0d3060df1
```

Its root filesystem is read-only; it runs as UID/GID 10001, with no network,
no privilege escalation, all capabilities dropped, and bounded CPU, memory,
processes, and temporary storage. Dataset and model mounts are read-only.

## Manifests

`ExecutionTaskManifest` binds the exact task, image, model, dataset, schemas,
resources, network mode, rootless mode, and output allowlist.

`ExecutionInputManifest` binds only the registered asset reference, metadata
digest, sample count, schema digest, and fixed-index digest. It contains no
path, patient identifier, raw filename, or binary data.

## Outputs

Only these files are accepted:

```text
aggregate_metrics.json
confusion_matrix.csv
execution_summary.json
```

The Connector independently verifies names, sizes, SHA-256 digests, result
binding, and result digest before creating a local quarantined Artifact.
