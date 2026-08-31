# Phase 5.11.4 Metadata-Only Product Publication Design

## Decision

Phase 5.11.4 publishes a small number of existing external metadata drafts
through the native `DataProductVersion` lifecycle:

`draft -> under_review -> approved -> published`

For these products, `published` means that reviewed catalog metadata is
discoverable. It does not mean that MedTrust owns, stores, redistributes, or
can compute over the upstream dataset.

The authoritative external-source invariants remain:

- `default_use_mode=external_metadata_catalog`
- `materialization_status=metadata_only`
- `data_holder_status=external_upstream`
- `execution_readiness=not_ready`
- no raw-data storage reference
- no model binding, Application, readiness, Contract, or ComputeJob eligibility

## Existing Lifecycle Audit

The existing data-product workflow has one authoritative version state
machine and active publication record. Provider users submit draft versions;
the platform operator returns or approves them; approval creates an active
`DataProductPublication` and a real `published_at`.

The ordinary hosted-data submit path requires `linkage_metadata.data_ready=true`.
That rule is correct for controlled-compute products but cannot describe an
external metadata-only publication. Phase 5.11.4 therefore adds a narrowly
scoped submit policy for a version with an immutable
`DataProductExternalSourceLink`. It does not add a second product state
machine.

The existing Application option query and draft service select every approved
active publication. Without a new backend exclusion, a published external
metadata product would be offered as computable. Readiness and ComputeJob
services also rely on approved publication/contract state and do not currently
inspect the external source link. Backend guards are required at all three
layers; hiding a frontend button is not sufficient.

## Actor Boundary

The B2 draft provenance is immutable and truthfully records that
`operator.demo` created the five original draft graphs under the existing
required provider field. Those rows and SourceLinks will not be rewritten.

Phase 5.11.4 introduces a separate local demonstration actor:

- organization: MedTrust Public Data Catalog Curator
- account: `catalog.curator.demo`
- role: `catalog_curator`
- responsibility: review the frozen draft content and submit it for platform
  review
- explicit limitation: the curator is not the upstream rights holder and does
  not approve its own submission

The platform reviewer remains `operator.demo`. Submission authorization for an
external metadata draft is based on the dedicated curator role and the
immutable source-link policy, not on a false claim that the curator owns the
upstream dataset. Approval rejects the same user or organization that submitted
the current review request.

The legacy `provider_organization_id` remains a schema ownership field for the
B2 graph. Public API and UI labels must call it the catalog steward for an
external metadata product, never the upstream rights holder.

## Publication Policy

Policy name: `External Public Metadata Product Policy v1`.

Submission is accepted only when all of the following remain true:

- the product and version are active drafts, not archived;
- exactly one immutable external source link exists;
- the linked record and current external version still match the stored IDs
  and source digest;
- the governance snapshot recomputes to the stored digest;
- source, license, access, and productization Reviews still match the stored
  Review IDs and record digest;
- source is confirmed, license is permissive, access is open, and
  productization is approved;
- the official source URL and reviewed license/access evidence are present;
- materialization and execution fields retain their fixed non-computable
  values;
- the actor is the independent catalog curator.

Unknown never means allowed. A missing source, unknown license/access result,
digest mismatch, archived product, or non-curator submission fails closed.

## Review And Audit

The curator submit command moves the native version to `under_review` and emits
the existing `data_product.version.submitted` event plus
`external_catalog.product.submitted`. The evidence records the curator,
operator organization, product, external record, source digest, governance
digest, policy version, and non-computable invariants.

The operator approval command uses the native approve/publish operation and
emits existing approved/published events plus one
`external_catalog.product.published` event. Rejected publication attempts emit
`external_catalog.product.publication.rejected` only when the request reaches a
formal command boundary; validation tests should normally roll back fixtures
instead of polluting the accepted business chain.

Idempotent replay must return the same publication and events. A changed
request under the same key is rejected.

## Public Catalog Contract

Published external metadata products are visible in the normal data catalog
and detail route with:

- external public catalog
- metadata only
- not materialized
- not executable
- upstream rights holder: external/unknown as evidenced
- catalog curator: the independent local curator
- official source and reviewed license/access terms
- explicit notice that MedTrust has not downloaded or hosted raw data
- `application_eligibility=false`

They are excluded from compute selectors and expose no Application or Job
action. A future materialization/access request may be shown as unavailable
roadmap text, but it is not implemented in this phase.

## Backend Denial Points

The same helper must reject external metadata-only products in:

1. Application option enumeration.
2. Application draft selection, before an Application row is created.
3. Legacy Application submission, for defense in depth.
4. Data readiness confirmation.
5. Contract execution authorization and ComputeJob creation.

The stable error code is `DATA_PRODUCT_NOT_MATERIALIZED`, surfaced through the
project's existing conflict response envelope.

## Lifecycle After Publication

Unpublish, relist, and logical archive continue to use the existing Phase 5.9
lifecycle request workflow. Publication does not create an alternative delete
or relist mechanism. The archived CPTAC-BRCA test graph remains archived and
cannot be submitted or republished.

