# Phase 5.13E-2B-1 Execution Security Boundary

## Enforced controls

- exact `PATHMNIST_REFERENCE_V1` task only;
- immutable image digest, model digest, dataset digest, schemas, 20 indices,
  resources, and output names;
- no runtime network namespace;
- fixed non-root user, read-only root filesystem, no privileges, all
  capabilities dropped, and no Docker socket;
- no runtime pull, package installation, user script, upload, dynamic model,
  external weight, WSI, DICOM, patient data, or clinical prediction;
- read-only dataset and model mounts;
- D-drive server-generated sandbox;
- atomic request/result files and replay-safe completion;
- all output remains hospital-local and starts in quarantine.

Worker negative tests reject task, image, model, network, rootless, resource,
output, sample-count, asset, schema, request-digest, and sandbox-binding
tampering.

## Honest limitations

```text
hard_isolation = false
production isolation certified = false
clinical use = false
automatic artifact egress = false
```

Compose-level controls were verified, but this is not an independent container
escape assessment or protection against a compromised host. Production PKI,
key custody, host hardening, monitoring, incident response, malicious-image
analysis, and clinical/privacy validation remain outside this phase.

## Central versus local objects

E-2B-1 intentionally creates a hospital-local reference execution and local
quarantined Artifact. It does not create a central `ComputeRun` or central
`Artifact`, because hospital review has not occurred and no Artifact metadata
or output is allowed to leave the hospital boundary yet.

Treating the local object as a central Run or Artifact before E-2B-2 review
would violate the architecture. Central evidence synchronization belongs to a
later, separately authorized stage.
