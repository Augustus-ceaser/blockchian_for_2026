# Phase 5.13E-0 Network and Filesystem Isolation

## Network policy

The execution container default and first-scope policy is:

```text
network_mode=none
DNS=none
proxy_variables=absent
```

The runtime has no route to central services, Connector, Internet, DNS,
metadata endpoints, package registries, model hubs, PACS, HIS, LIS, local
databases, or arbitrary hospital services.

Control communication terminates at Executor Manager. Status is collected by
the manager through local process supervision or a constrained local channel;
the execution container does not call the Connector.

Any task that requires network access is outside the first execution scope and
requires a future architecture review. A silent fallback to host networking,
bridge networking, DNS, proxy, or service mesh is a launch failure.

## Workspace layout

The target ephemeral workspace is:

```text
input/    read-only approved projection
runtime/  read-only image/runtime content
scratch/  bounded ephemeral read-write space
output/   bounded write-only/limited-read task output
logs/     bounded structured local logs
```

The container root filesystem is read-only. Only `scratch/`, `output/`, and the
minimum log sink are writable. Input is mounted read-only and cannot be renamed,
deleted, modified, hard-linked, or used as an output target.

## Prohibited mounts and paths

The runtime must not see:

- `/`, host root, user homes, administrator profiles, or service homes;
- container runtime sockets or daemon APIs;
- Connector state, certificates, signing keys, databases, or audit stores;
- hospital object-store credentials or general data lake roots;
- central credentials or API tokens;
- arbitrary device nodes;
- unrelated local asset versions;
- an egress or release directory.

Mount sources are resolved from opaque approved IDs by a trusted local adapter.
The order and LaunchManifest never contain raw host paths.

## Path safety

Before launch and quarantine handoff:

- canonicalize beneath a fixed root;
- reject absolute, UNC, drive-qualified, parent traversal, alternate data
  stream, NUL, reserved-name, and mixed-separator paths;
- reject symlink, junction, mount-point, reparse-point, and hard-link escapes;
- use descriptor-relative or equivalent safe-open operations;
- enforce file-count, depth, name-length, and byte limits;
- never extract an archive without validating every member before writing.

## Input projection

ExecutionInputManifest identifies an approved projection by IDs and digests,
not by patient IDs or filenames. The projection builder runs in the hospital
domain before sandbox launch, verifies metadata/schema/quality digests, and
exposes only the minimum fixed task inputs.

The Executor must not enumerate the source system. It sees only the approved
projection. Input integrity is verified before and after the run where
technically feasible; a mismatch is a security failure.

## Cleanup

Normal completion, rejection, timeout, crash, host restart, and forced
termination all require cleanup reconciliation. The system must:

- stop and remove the sandbox;
- unmount projections;
- zero or securely remove ephemeral secrets if any future design permits them;
- destroy scratch data;
- move only declared output into quarantine;
- record cleanup outcome and residual paths by opaque reference;
- block new execution when unresolved residue exists.

Workspace destruction does not delete the immutable local audit record or the
quarantined Artifact needed for review.

## Required negative tests

Acceptance must cover external IP and DNS attempts, proxy variables, localhost
and metadata-service probes, host path probes, Docker socket access, symlinks,
junctions, hard links, traversal, archive escape, read-only input mutation,
output quota overflow, log overflow, and kill/restart cleanup.
