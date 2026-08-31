# Phase 5.13E-0 First Execution Scope

## Scope decision

The first future execution, after separate authorization and implementation
phases, is limited to:

```text
task: fixed PathMNIST inference
data: fixed public/non-clinical 20-sample demonstration subset
model: fixed approved ResNet-18
image: one approved immutable digest
runtime: CPU only
network: none
output: small aggregate metrics and declared evidence files
```

Phase 5.13E-0 does not run this task.

## Why this scope

The pair has an existing verified engineering reference and avoids gated
external weights, real hospital data, arbitrary models, training, GPU/CUDA,
dynamic code, and new clinical claims. It is a control validation target, not
a performance benchmark or clinical validation study.

## Frozen prohibitions

- no real hospital or patient data;
- no PACS/HIS/LIS/database integration;
- no CONCH, UNI, Prov-GigaPath, or external gated weight download;
- no user-uploaded model, script, notebook, archive, or Dockerfile;
- no training, fine-tuning, federated learning, or arbitrary inference;
- no GPU, CUDA, ROCm, device passthrough, or privileged container;
- no runtime network, DNS, package install, model pull, or dependency download;
- no raw prediction rows, images, or unreviewed logs leaving the hospital
  boundary;
- no automatic Artifact release;
- no `hard_isolation=true` or clinical claim.

## Required immutable bindings

Before a first run can be approved, its execution-specific order and local
approval must bind:

- exact PathMNIST subset/version and input-manifest digest;
- exact fixed ResNet-18 model/version/weight digest;
- exact image manifest and OCI digest;
- exact fixed task schema and entrypoint ID;
- expected input count and schema;
- expected output schema and file allowlist;
- CPU, memory, process, disk, output, log, and time limits;
- no-network and filesystem mount policies;
- hospital reviewer roles and evidence schema.

## Stage gates

### Phase 5.13E-1

May implement only the Executor control skeleton, identities, inert state
machine, manifest validators, and negative-test harness. It must not read input
or invoke a model.

### Later sandbox gate

May prove sandbox controls using a harmless fixed fixture. It must pass
network, filesystem, privilege, resource, image, replay, and cleanup negative
tests before model loading.

### Fixed-task gate

May execute the exact public/synthetic reference task once all prior controls,
local approval, quarantine, review, evidence, and audit paths are ready.

## Success criteria

The first execution is acceptable only when:

- the central platform cannot read input or invoke the sandbox directly;
- Connector and Executor identities and responsibilities are separate;
- every manifest and approval digest matches;
- network is unavailable and input is read-only;
- runtime is non-root and resource bounded;
- all output begins in local quarantine;
- hospital review is independent;
- only an approved signed EvidenceBundle reaches central;
- both audit chains validate;
- cleanup completes;
- `hard_isolation=false` remains visible.
