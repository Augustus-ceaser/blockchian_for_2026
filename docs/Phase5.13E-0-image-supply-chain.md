# Phase 5.13E-0 Execution Image Supply Chain

## Principle

Runtime image selection is an approval decision, not an ExecutionOrder string.
Images are acquired, scanned, signed, approved, and staged before any order is
accepted. Runtime pulls and mutable tags are forbidden.

## ExecutionImageManifest

The future immutable manifest must include at least:

```text
image_id
manifest_version
image_digest
source_registry
source_reference
signature_algorithm
signature_key_id
signature
build_provenance_digest
source_revision
builder_identity
build_timestamp
dependency_manifest_digest
sbom_digest
runtime_version
entrypoint_id
supported_task_schema_ids
supported_model_digests
security_scan_tool/version/time
security_scan_status
exception_approvals
created_at
```

Allowed lifecycle:

```text
candidate -> approved -> deprecated
                     \-> revoked
```

Only `approved` may be used for a new launch. `deprecated` blocks new launches
but preserves historical evidence. `revoked` blocks queued and not-yet-started
launches immediately and requires local incident handling for active or
completed runs.

## Required controls

- pin by OCI digest, never `latest` or another mutable tag;
- verify image signature against an approved local trust root;
- verify provenance and source revision;
- generate and retain an SBOM and dependency lock digest;
- scan OS and language dependencies before approval;
- store approval and exceptions as explicit, expiring decisions;
- import into a hospital-controlled registry or content store before launch;
- verify staged bytes again immediately before launch;
- bind entrypoint and supported task schema in the manifest;
- maintain an offline deny list for revoked image and signing-key digests.

## Build boundary

Production candidate images must be built by a controlled pipeline from pinned
sources and dependencies. The runtime host must not build an image from an
ExecutionOrder, Git URL, uploaded Dockerfile, notebook, or user archive.

Dependency installation belongs to the controlled build pipeline. `pip`,
`conda`, `apt`, `npm`, `curl`, `wget`, and model-hub downloads are prohibited
inside the execution runtime.

## Model supply levels

- Level 0: metadata only; not loadable.
- Level 1: a hospital-owned, previously approved local model with immutable
  digest.
- Level 2: a separately approved materialization/import process stages a model
  into the hospital environment before execution.

The first execution may use only a fixed allowlisted model. User-uploaded
models, arbitrary Hugging Face downloads, `trust_remote_code`, dynamic Python,
and runtime model retrieval are prohibited.

## Revocation and offline behavior

If current revocation status cannot be established within the permitted local
cache window, launch fails closed. Offline operation may use only a
policy-defined, signed, unexpired deny/allow snapshot. Reconnection must
reconcile revocations before another launch.

## Acceptance evidence

Before execution implementation is accepted, negative tests must prove
rejection of mutable tags, unknown signer, altered manifest, altered layer,
missing SBOM, unsupported task schema, expired exception, revoked image,
runtime pull, dynamic dependency install, and model digest mismatch.
