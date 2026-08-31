# Phase 5.13E-0 Hard Isolation Maturity

## Levels

| Level | Meaning | Minimum evidence | Permitted claim |
|---|---|---|---|
| L0 | metadata only; no local asset execution | immutable catalog and governance | metadata catalog |
| L1 | fixed-task controlled engineering execution | fixed public/synthetic task and audited local run | controlled engineering execution |
| L2 | hospital Connector local execution candidate | independent Connector, local asset, local authorization, local quarantine, reviewed evidence | hospital-controlled engineering candidate |
| L3 | hardened isolation candidate | deny-by-default network/filesystem/privilege/resource controls plus systematic negative tests and operations drills | hardened isolation candidate |
| L4 | independently assessed production hard isolation | production IAM/PKI/key custody, tenancy, monitoring, recovery, independent security assessment and remediation | only claims within assessed scope |

## Current assessment

The current platform is:

```text
implemented engineering baseline: L1
Phase 5.13E target under design: L2/L3 controls
hard_isolation=false
```

Phase 5.13B-D establish identity, metadata, and signed control prerequisites.
They do not create a local execution implementation. Phase 5.13E-0 freezes a
design only and does not advance the measured maturity level.

## Promotion gates

### L1 to L2

- separate local Executor identity and manager;
- execution-specific hospital approval;
- immutable input/model/image/launch manifests;
- real local fixed-task run using public/synthetic data only;
- local Artifact quarantine and hospital review;
- signed EvidenceBundle and dual audit verification;
- proven absence of central raw-data access.

### L2 to L3

- default no-network sandbox;
- read-only root and input, explicit bounded writable mounts;
- non-root, no-new-privileges, all capabilities dropped;
- image signature/provenance/SBOM/vulnerability policy;
- resource and output limits;
- comprehensive attack tests and crash/recovery drills;
- operations procedures, patch/revocation objectives, and monitoring;
- residual-risk review.

### L3 to L4

- deployment-specific independent assessment;
- production IAM/MFA, PKI/HSM/KMS, key rotation and incident handling;
- hospital infrastructure integration and separation of duties;
- tenant and workload isolation assessment;
- backup/recovery and business-continuity validation;
- continuous vulnerability, configuration, audit, and egress monitoring;
- remediation closure and periodic reassessment.

## Claim guardrails

- A Docker setting, passing test, signed manifest, or version tag cannot
  self-assign a maturity level.
- Windows Docker Desktop is not L4 evidence.
- `network_mode=none` alone is not hard isolation.
- Hospital deployment is not implied until deployment-specific controls are
  assessed.
- No phase before an independent L4 assessment may set
  `hard_isolation=true`.
