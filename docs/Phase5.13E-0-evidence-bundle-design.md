# Phase 5.13E-0 EvidenceBundle Design

## Purpose

An EvidenceBundle is the minimum hospital-approved, signed account of a
controlled execution. It is not the raw Local Artifact, a clinical report, or
proof that the platform provides production isolation.

## Generation flow

```text
LocalRun terminal
  -> Local Artifact quarantined
  -> scans complete
  -> proposed evidence manifest
  -> independent Hospital Output Review
  -> canonical serialization
  -> digest
  -> hospital/Connector signature
  -> egress transmission
  -> central digest/signature/schema verification
  -> append-only Central Evidence Registry
```

Generation is impossible before local review approval. Central receipt creates
an evidence-registry fact only; it does not alter the local review decision.

## Required content

- bundle ID, schema version, immutable version, and supersedes reference;
- hospital organization, Connector, and future Executor identities;
- PolicyBundle and execution-specific order IDs and digests;
- local execution approval ID and digest;
- LocalAssetVersion, model version, ExecutionInputManifest,
  ExecutionImageManifest, and LaunchManifest IDs and digests;
- fixed task schema and runtime version;
- start/end timestamps and terminal status;
- bounded aggregate resource usage;
- allowlisted output summary and output schema digest;
- quality limitations, protocol deviations, and security events;
- proposed evidence file manifest with media type, size, and digest;
- Hospital Output Review decision, reviewer role, and timestamp;
- local audit-chain head and relevant event range;
- signing key ID, algorithm, bundle digest, and signature.

## Prohibited content

- raw patient data, images, WSI, DICOM, or row-level records;
- patient IDs, accession numbers, filenames, local paths, or database handles;
- model weights or hospital credentials;
- raw Local Artifact or unreviewed intermediate output;
- unrestricted stdout/stderr, stack traces, environment variables, or secrets;
- reversible identifiers or direct links to hospital storage;
- fields not declared by the approved evidence schema.

## Canonical and cryptographic rules

- exact schema with unknown fields rejected;
- deterministic UTF-8 canonical serialization;
- SHA-256 or stronger approved digest;
- signature by a dedicated hospital evidence key, separate from central policy
  signing;
- current certificate/key status and Connector binding verified centrally;
- immutable accepted version; correction creates a new version and
  `supersedes` reference;
- replay, duplicate digest, expired review, revoked signer, and audit-head
  mismatch fail closed.

## Files and summaries

The first scope should prefer a manifest-only bundle with small allowlisted
aggregate JSON/CSV evidence. Every file must be independently scanned,
reviewed, digested, and bound into the signed manifest. A file absent from the
reviewed manifest is not transmitted.

## Rejection and revocation

Hospital rejection prevents generation and transmission. A policy, image,
model, or key revocation after execution does not rewrite history; it records
a new revocation/incident fact and may block acceptance or downstream use.
Central rejection of a malformed bundle is recorded and requires a newly
generated version after local review.

## Central registry boundary

Central stores the verified signed bundle, verification outcome, receipt time,
and audit linkage. It cannot browse quarantine, request raw files, reconstruct
patient-level data, or change local decisions.
