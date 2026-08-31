# Phase 5.13E-0 Local Artifact Lifecycle

## Boundary

A Local Artifact is hospital-controlled output produced by a local sandbox. It
is not a central `Artifact`, ReleasePackage, or downloadable object. Phase
5.13E-0 defines this concept only and creates no record or file.

## State machine

```text
created
  -> quarantined
  -> scanning
  -> review_pending
  -> approved
  -> destroyed

scanning -> rejected
review_pending -> rejected
approved -> destroyed
rejected -> destroyed
```

Every execution output enters `quarantined`. No transition from `created` or
`quarantined` to central transfer exists.

## Immutable facts

The future local record must bind:

- LocalRun ID and immutable launch digest;
- PolicyBundle, ExecutionOrder, input, model, and image digests;
- creation timestamp and producing sandbox identity;
- canonical file manifest with relative names, types, sizes, and digests;
- scanner versions and results;
- structured output schema validation;
- review decisions and reviewer identities;
- local audit head;
- retention and destruction decisions.

Changes create a new Artifact version or derived candidate; they do not mutate
the original manifest.

## Quarantine controls

- storage is separate from input, runtime, Connector state, and egress;
- files are non-executable and unavailable to the central platform;
- preview and scanning use isolated readers with bounded resources;
- active content, archives, macros, scripts, executables, device files,
  symlinks, and unknown media types are rejected for the first scope;
- names and paths are normalized and do not reveal source paths;
- partial output after timeout or crash remains quarantined and cannot generate
  evidence automatically.

## Scanning

Required checks include:

- exact output schema and field allowlist;
- media type and magic-byte agreement;
- size, count, depth, and compression-ratio limits;
- malware and active-content checks;
- secrets, credentials, path, stack trace, and identifier leakage;
- patient and quasi-identifier patterns;
- small-cell and rare-category disclosure risk;
- policy-specific aggregation, suppression, and rounding;
- digest and manifest consistency.

Scanner success is necessary but not sufficient for release.

## Hospital Output Review

An independent hospital reviewer evaluates:

- task and policy binding;
- output schema and intended use;
- file type and size;
- scanner results and unresolved warnings;
- small-cell and privacy risk;
- quality limitations and protocol deviations;
- whether the proposed EvidenceBundle is the minimum necessary disclosure.

Available decisions are `approve_evidence`, `reject`, and `request_new_derived_output`.
Approval applies to one immutable proposed evidence manifest. It does not
approve the raw Local Artifact for transfer.

Self-review by the execution requester, Executor service identity, or central
operator is prohibited. Automatic failure cannot be overridden; remediation
requires a new run or a new derived candidate with fresh review.

## Egress rule

Only a signed, approved EvidenceBundle may cross the hospital boundary. Raw
Artifact bytes, unreviewed logs, local paths, patient-level records, and source
files remain local. Central cannot request a direct download or change a
hospital rejection.

## Retention and destruction

Retention is hospital policy. Destruction is explicit, audited, and verifies
that quarantine bytes and temporary derivatives are removed. Evidence
registries retain only permitted manifests, digests, decisions, and audit
references. Legal hold, incident hold, and failed destruction block automatic
cleanup claims.
