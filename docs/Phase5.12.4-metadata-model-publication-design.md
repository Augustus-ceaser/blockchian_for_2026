# Phase 5.12.4 Metadata-only Model Publication Design

Date: 2026-07-27

## Goal

Publish at most two governed external model metadata products for catalog
discovery without creating weight possession, an execution registry binding,
application eligibility or compute eligibility.

## Meaning of Published

For an external metadata model, `published` means only that the fixed catalog
record and its governance conclusion may be discovered. It does not mean that
MedTrust owns, redistributes, downloads, deploys, validates or executes the
model.

The immutable boundary remains:

```text
source_kind = external_public_model
materialization_status = metadata_only
weight_holder_status = external_upstream
execution_readiness = not_ready
platform_validation = not_validated
entrypoint_id = external-metadata-only
runtime = external_metadata_only
application_eligibility = false
compute_eligibility = false
```

## Lifecycle Audit

### Native lifecycle

The existing native lifecycle is:

```text
ModelProduct draft
-> ModelVersion under_review
-> ModelVersion approved
-> active ModelPublication
-> ModelProduct active
```

Native submission and approval validate the version against the fixed
PathMNIST `ModelRegistry`. This is correct for the historical executable demo
model and must remain unchanged. It is intentionally unusable for
`external-metadata-only`.

### External adapter

The external adapter will reuse the native product/version/publication states
but replace executable registry validation with strict revalidation of:

- current external record and version;
- unchanged source record digest;
- unchanged immutable source link;
- unchanged governance snapshot digest;
- all 12 bound reviews still current and digest-matched;
- current profile remains `eligible_for_model_draft`;
- metadata-only policy and non-executable version fields;
- no active publication before approval.

A dedicated `ModelMetadataPublicationReviewTask` records pending and decided
product review. The generic Application `ReviewTask` cannot be reused because
its foreign keys require an Application snapshot.

## Actor Boundary

| Responsibility | Actor |
|---|---|
| Upstream model author/provider | External evidence, never a MedTrust actor |
| Code/weight rights holder | External license evidence |
| Catalog submission | `catalog_curator` |
| Independent review and publication | `space_operator` |
| Original B2 ingestion | `space_operator`, retained in immutable source link |

The required `ModelProduct.provider_organization_id` is an internal catalog
ownership field for these records. Public UI must label it as catalog steward,
not model provider, author, owner or license grantor. The source link's
`upstream_provider` remains the source of upstream attribution.

The submitter and reviewer must differ by both user and organization. Operator
submission is rejected, curator approval is rejected and self-approval is
rejected.

## Ten Audit Questions

1. **Who currently holds the drafts?** The operator organization created and
   internally owns the B2 rows. The immutable source link identifies upstream
   attribution separately.
2. **Could the UI misstate ownership?** Yes. Existing native UI calls the
   required organization field `provider`. External views must instead show
   `catalog_steward` and `upstream_provider`.
3. **Does publication enter the compute selector?** Currently yes in
   `/application-options`; Phase 5.12.4 must explicitly exclude external links.
4. **Does publication permit Application creation?** The high-level creation
   service rejects the external link with `MODEL_PRODUCT_NOT_MATERIALIZED`.
   The selector and low-level attachment service also require hardening.
5. **Does publication permit Readiness?** No. Both readiness orchestration and
   marketplace registry validation reject the metadata-only model.
6. **Does publication register an Executor?** No. Registry entries are static,
   code-owned and allowlisted. `external-metadata-only` is not registered.
7. **Does publication permit Job or Run creation?** No. Compute authorization
   rejects the external link before algorithm validation.
8. **Is submitter/reviewer separation supported?** Native roles are separate,
   but there is no model publication task. The external task adds explicit
   submitter and reviewer evidence and conflict checks.
9. **Does the historical model use different semantics?** Yes. It has a fixed
   built-in entrypoint, registry digest, local asset and executable readiness.
10. **How is executable-flow pollution prevented?** External source-link
    exclusion in selectors plus mandatory materialization checks in attachment,
    Application, Readiness and Compute paths.

## Publication Policy

`External Public Model Metadata Product Policy v1` allows catalog discovery
and governance revalidation only. It prohibits weight/code download, direct
Application selection, contract readiness, Executor registration and
execution. Unknown is never interpreted as allowed; `public_available` is not
interpreted as redistribution permission.

Before future materialization, license, revision, hashes, dynamic code,
dependencies, resource requirements and sandbox behavior must be reviewed
again. No model is represented as clinically approved or performance-verified.

## API

The external source graph remains under
`/external-model-catalog/models/{record_id}`:

- `POST .../model-product-publication/submit`
- `POST .../model-product-publication/return`
- `POST .../model-product-publication/approve`
- `GET .../model-product-publication`

These endpoints invoke domain services. They never write status directly and
never bypass the native state transition constraints.

## Database

Migration `20260727_0045` adds only the dedicated publication review task and
required audit vocabulary. The existing source link remains immutable. No
external record, version or governance review is copied or modified.

## Failure Conditions

The phase fails if any published external product becomes materialized,
registered, ready, selectable or executable; if source evidence changes; if
the submitter can approve; or if Applications, Contracts, Jobs, Runs, MinIO
model objects or external downloads increase.
