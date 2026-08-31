# Phase 5.13E-0 Hospital Controlled Execution Architecture

## Decision

The future execution path is frozen as:

```text
Central Application / Contract / Readiness
  -> signed PolicyBundle
  -> signed execution-specific order
  -> Hospital Connector validation
  -> independent local execution authorization
  -> Executor Manager
  -> Sandbox Runtime
  -> fixed Execution Container
  -> Local Artifact Quarantine
  -> scanners
  -> Hospital Output Review
  -> signed EvidenceBundle
  -> Central Evidence Registry
```

No component in this chain is implemented by Phase 5.13E-0.

## Control domains

### Central platform

May:

- receive an Application and complete Contract and Readiness governance;
- compile and sign bounded policies and execution requests;
- view coarse status and signed decisions;
- verify and register an approved EvidenceBundle.

Must not:

- read hospital input files or discover hospital paths;
- connect to PACS, HIS, LIS, local databases, or object storage;
- invoke an Executor process or container directly;
- bypass Connector validation or local authorization;
- alter a hospital accept/reject/release decision;
- receive an unreviewed Local Artifact.

### Hospital Connector

The Connector is a policy enforcement and mediation service. It validates
identity, signature, binding, time, replay, sequence, local asset eligibility,
and local decision prerequisites. It may submit a validated launch request to
Executor Manager only after a new execution-specific local approval.

The Connector is not a shell, job runner, container daemon proxy, file browser,
package installer, model loader, or Artifact release service.

### Executor Manager

Executor Manager is a hospital-controlled local service. It accepts only a
canonical, locally approved LaunchManifest from the Connector. It performs
preflight checks, creates the sandbox, supervises resource limits, records
local run events, terminates the runtime, and transfers output only into local
quarantine.

It exposes no central endpoint and accepts no free-form command, path,
environment variable, image tag, package, URL, or user code.

### Sandbox Runtime

The runtime is ephemeral and task-specific. It contains a fixed image,
read-only approved input, bounded scratch space, bounded output, and structured
local logs. It has no Internet, DNS, host filesystem, Docker socket, elevated
capability, or secret-bearing environment.

### Local Artifact Quarantine

Quarantine is outside the execution container and inside the hospital control
domain. Every output enters `created -> quarantined`; no execution outcome may
write directly to an egress location.

### Hospital review and evidence

Independent hospital review determines whether a minimal derived evidence
package may leave the boundary. Rejection is final for that candidate bundle.
Central may reject a malformed bundle but may not promote a locally rejected
output.

## Required decision points

Four decisions are independent:

1. `control_validated`: signatures and policy references are valid.
2. `execution_approved_locally`: an authorized hospital actor approves one
   immutable launch request.
3. `run_completed`: the sandbox ended with a recorded outcome.
4. `egress_approved_locally`: hospital review approves a specific evidence
   manifest.

No decision implies the next.

## Failure semantics

All ambiguity fails closed. Unknown image, missing digest, stale order,
revocation uncertainty, clock failure, asset mismatch, unsupported output,
resource-policy failure, sandbox-policy failure, audit failure, or cleanup
failure rejects launch or evidence generation.

Central status is observational. Loss of central connectivity must not weaken
local enforcement. The default after restart is deny until local state,
revocations, sequence, audit head, and approved manifests are reconciled.

## Non-claims

This architecture is a design target, not deployed isolation. It does not
establish clinical validity, hospital production readiness, certified trusted
data-space status, or `hard_isolation=true`.
