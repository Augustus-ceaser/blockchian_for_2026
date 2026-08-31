# Tencent Guangzhou Administrator Bootstrap Failure Analysis

## Incident

The first Pre-ICP administrator initialization failed before commit because
`bootstrap_public_alpha.py` called the complete Phase 4 demo initializer. That
initializer created identity rows in the current transaction, then attempted
to read:

`/var/lib/medtrust/data/bootstrap/registered_assets/pathmnist_resnet18_v1/model_manifest.yaml`

The production server correctly had no pre-populated runtime bootstrap data,
so the read raised `FileNotFoundError`.

## Server Evidence

- Migration remained at `20260730_0058`.
- Application ORM inspection found zero rows across all mapped tables.
- `operator.demo` did not exist.
- No failed Compose run container remained.
- The MinIO volume contained only `.minio.sys` implementation metadata and no
  business object.
- PostgreSQL and MinIO named volumes were preserved.

The original tool wrapped Phase 4 initialization and credential creation in
one `session.begin()` transaction. Although identity rows had been flushed,
the exception rolled the transaction back before credentials were created.

## Root Cause

Account bootstrap, workspace bootstrap, and optional Phase 4 demo catalog
seeding were coupled in one command. The tracked manifest existed in the
developer checkout but was neither copied into the Backend image nor included
at the runtime data path expected by the initializer.

The manifest itself is static Public/Synthetic Non-clinical metadata. It does
not contain model weights, patient data, private keys, Artifact data,
EvidenceBundle data, Phase 5.13 paths, or local absolute paths.

## Fix

- `bootstrap_public_alpha_accounts` creates only invitation identities,
  credentials, organizations, memberships, and the minimal Public Alpha
  workspace.
- Existing complete account foundations are reported without changing
  password hashes. Incomplete foundations fail closed in one transaction.
- `create-admin.sh` checks status before prompting and never passes a password
  in process arguments or logs.
- The optional `seed-public-alpha-demo.sh` is separate and requires explicit
  confirmation.
- Safe PathMNIST metadata is packaged under immutable Backend application
  resources. It declares `non_clinical=true`,
  `synthetic_or_public=true`, and `contains_model_weights=false`.
- Manifest validation rejects model weights, patient fields, and local
  absolute paths.

No migration is required. The existing PostgreSQL and MinIO volumes remain
the authoritative Pre-ICP volumes.

## Additional Deployment Corrections

Live Pre-ICP deployment also identified three packaging defects:

- Compose build contexts require the deployment directory as project root.
- Docker 29 requires a non-internal `pre_icp_edge` network for a loopback host
  publication while the application network remains internal.
- The Caddy file capability must be removed when combining non-root execution,
  `cap_drop: ALL`, and `no-new-privileges`.

These corrections do not open a public port. The only Pre-ICP host binding
remains `127.0.0.1:18080`.
