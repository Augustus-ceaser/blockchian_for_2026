# Phase 5.12.6A Materialization Preflight Design

Date: 2026-07-28

## Scope

This phase adds approval records for a future controlled asset materialization
step. It does not download, unpack, transform, register, execute or validate any
external asset. An approved plan would authorize only Phase 5.12.6B to follow
the frozen file and network allowlists.

## Existing lifecycle audit

1. There is no Materialization Request, download-preapproval object or
   equivalent state machine.
2. External data and model source links are deliberately fixed at
   `metadata_only` and `not_ready`.
3. Native PathMNIST assets have fixed manifests, SHA-256 digests, dependency
   locks, output allowlists and a built-in Executor registry.
4. The native asset path does not provide a general D-drive partial download,
   atomic promotion, external archive validation or materialization rollback
   workflow.
5. Data and model materialization do not yet have independent lifecycle
   services.
6. The current failure states belong to compute execution, not asset
   acquisition, and must not be reused.
7. PathMNIST digest, registry and allowlist patterns are reusable as validation
   principles, but its fixed built-in asset registration must not be widened to
   arbitrary external code or weights.
8. External assets must require a curator-created plan followed by independent
   operator approval. Publication, static relation evidence and provider
   readiness cannot substitute for that approval.
9. A static relation never changes a product, application, contract, readiness
   confirmation, job, run or Executor registry. Materialization plans are also
   excluded from every compute gate.
10. A new minimal `AssetMaterializationPlan` is required because no existing
    object can represent immutable source, license, access, file, resource and
    security approval without falsely asserting asset readiness.

## Domain object

`AssetMaterializationPlan` locks the exact relation, product versions, current
relation evidence and all version/source/governance digests. It stores:

- data, model and deterministic transformation plans;
- exact network and file allowlists;
- license and access snapshots;
- estimated data, model, derived and total bytes;
- hardware requirements and execution goal;
- security result and blocking reasons;
- creator, submitter and independent approver identities;
- canonical plan digest and idempotency digests;
- optional superseded-plan reference.

Statuses are limited to `draft`, `submitted`, `approved`, `rejected`, `expired`,
`superseded` and `cancelled`. Downloading, materialized, ready and executed are
not plan statuses.

## Invariants

- Only active, public, currently published
  `static_schema_compatible_with_transformation` relations can receive plans.
- Every graph digest is revalidated during create, submit and decision.
- Curators create and submit; operators approve, reject or cancel.
- The creator organization cannot approve its own plan.
- Approval requires explicit passing license, access, immutable revision,
  exact file sizes, deterministic transformation, resource budget and static
  security evidence.
- Approved plans cannot be updated or deleted. Rejected plans cannot be
  deleted. Any approved-plan change requires a new plan that supersedes it.
- API commands have no HTTP client, storage client, task queue, Executor or
  compute side effect.

## Approval is not readiness

Even when a future plan is approved, both external source links remain
`metadata_only/not_ready`, no asset is present, no Executor is registered, and
the pair cannot be selected by Application or compute services. Only a later
separately accepted materialization phase may create verified asset facts.

## Phase 5.12.6A expected terminal state

Current official CONCH and UNI access terms require a registered Hugging Face
account, institutional identity, gated approval and a private access token.
These conditions violate this phase's approval policy. The expected honest
result is therefore:

```text
selected candidate = 0
approved plans = 0
status = no_materialization_candidate_approved
```
