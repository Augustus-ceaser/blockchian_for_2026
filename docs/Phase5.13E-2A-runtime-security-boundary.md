# Phase 5.13E-2A Runtime Security Boundary

## Enforced boundary

Runtime preparation fails closed unless all of these facts are current and
valid:

- the local Executor is active;
- an approved admission belongs to that Executor;
- the referenced security profile is valid;
- the execution image is approved;
- the image digest exactly matches the approved digest;
- the resource policy is bounded;
- the sandbox root is on the D drive in the host environment.

The API accepts policy identifiers, not caller-controlled host paths. Stored
workspace references are relative and do not disclose an absolute local path.

## Defense in depth

- `started` is rejected by the application and database trigger.
- Destroyed sessions cannot return to an active lifecycle state.
- Runtime lifecycle events are immutable and cannot be deleted.
- C-drive and user-home sandbox roots are rejected.
- A revoked image or missing admission prevents preparation.
- Repeated preparation with the same digest returns the same session.

## Claims not made

This is an admission-bound Runtime skeleton, not a hardened execution sandbox.
No execution container was started and no operating-system isolation was
validated. Therefore:

```text
execution_enabled = false
hard_isolation = false
production isolation certified = false
clinical workload ready = false
```

Host compromise, production identity and key custody, incident response,
malicious image execution, runtime network enforcement, and raw-data protection
remain outside the evidence of this phase.
