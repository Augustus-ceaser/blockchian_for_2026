# Phase 5.13E-0 Executor Security Boundary

## Components

```text
Connector
  -> Executor Manager control API
  -> sandbox policy adapter
  -> sandbox runtime
  -> fixed execution container
```

The local control API is authenticated, loopback or private IPC only, and
unreachable from the central platform. Connector and Executor Manager use
separate identities and least-privilege service accounts.

## Allowed Executor responsibilities

- verify the immutable LaunchManifest and local approval reference;
- verify an approved image digest and signature;
- mount one approved input projection read-only;
- launch one fixed entrypoint with fixed arguments;
- enforce CPU, memory, process, disk, output-size, and wall-clock limits;
- emit structured status and bounded local logs;
- move output to quarantine using an atomic local handoff;
- terminate and prove cleanup.

## Prohibited responsibilities

- accepting shell commands, notebooks, scripts, plugins, or Python source;
- selecting arbitrary images, tags, registries, models, paths, or URLs;
- downloading packages, dependencies, weights, or data at runtime;
- using `eval`, `exec`, dynamic imports, `trust_remote_code`, or user hooks;
- changing PolicyBundle, order, local approval, audit, or output review;
- reading outside the approved input projection;
- writing to input, host paths, or an egress destination;
- connecting to Internet, central APIs, PACS, HIS, LIS, or arbitrary local
  services;
- returning raw output to Connector or central.

## LaunchManifest

The future canonical manifest must bind:

- local approval ID and digest;
- ExecutionOrder and PolicyBundle IDs and digests;
- Connector and Executor identities;
- approved LocalAssetVersion and ExecutionInputManifest digests;
- approved local model or model package digest;
- approved ExecutionImageManifest ID and image digest;
- fixed entrypoint identifier, not a command string;
- output schema and limits;
- CPU, memory, process, disk, and time limits;
- network policy `none`;
- filesystem mount plan;
- expiration, nonce, sequence, and idempotency key.

Any field mismatch invalidates the complete manifest. No local default may
silently broaden policy.

## Privilege baseline

The target container must:

- run as a fixed non-root UID/GID;
- set `no-new-privileges`;
- use a read-only root filesystem;
- drop all Linux capabilities;
- never be privileged;
- never mount the container runtime socket;
- use a bounded process count;
- disable setuid/setgid binaries where technically possible;
- use an explicit seccomp/AppArmor/SELinux policy in hardened environments;
- receive no host credentials or control-plane private keys.

`CAP_SYS_ADMIN`, `CAP_NET_ADMIN`, and `CAP_DAC_OVERRIDE` are always forbidden.

## Resource policy

Every approved task must define hard limits. Missing limits are a rejection,
not an unlimited default:

- CPU quota and core ceiling;
- memory and swap ceiling;
- process/thread ceiling;
- input projection size;
- scratch and output quota;
- maximum file count and single-file size;
- wall-clock timeout and termination grace period;
- log byte and event-rate limits.

Limit breach terminates the run, quarantines any partial output, records a
security outcome, and prevents EvidenceBundle generation until review.

## Execution state separation

A future LocalRun must be local and append-only in its significant facts. It
must never be represented by reusing central ComputeRun state. Central may
receive a signed status summary only. Automatic retries require a new local
decision or a policy-defined bounded retry authorization; they cannot reuse a
completed or rejected launch nonce.

## First implementation gate

Phase 5.13E-1 may build only an inert control skeleton. No model invocation or
input read is allowed until separate negative tests prove that command,
network, filesystem, privilege, resource, replay, and image checks fail closed.
