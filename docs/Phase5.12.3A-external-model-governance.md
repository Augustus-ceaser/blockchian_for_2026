# Phase 5.12.3A External Model Governance

## Scope

Phase 5.12.3A adds a governance overlay to the 16 immutable external model
catalog records. The overlay is separate from `ExternalModelRecord`,
`ExternalModelVersion`, `ModelProduct`, and executable `ModelVersion`.

This phase did not visit papers, GitHub, Hugging Face, model cards, or other
external model URLs. It did not download weights, clone repositories, call an
inference API, build an execution image, or create a `ModelProduct`.

## Domain Objects

- `ExternalModelGovernanceProfile`: one computed active profile per catalog
  record.
- `ExternalModelGovernanceReview`: append-only operator evidence and decision
  history. A new conclusion supersedes an earlier review.
- `ExternalModelFamilyResolution`: explicit operator resolution for potential
  aliases, variants, forks, or same-paper candidates.

Migration `20260727_0043` creates these tables, indexes, foreign keys, status
checks, the review append-only trigger, and the model-governance audit
vocabulary.

## Deterministic Rules

The profile calculation uses only fields already stored in the catalog and
append-only human reviews. Catalog metadata never automatically becomes an
official-source, permissive-license, public-weight, pinned-revision, or
regulatory-clearance conclusion.

Technical contract completeness is a 0-100 metadata completeness percentage.
It is not a model quality, performance, clinical reliability, or trust score.

Primary status priority is:

`rejected`, `blocked`, `family_resolution_pending`,
`security_review_required`, `technical_contract_incomplete`,
`clinical_boundary_unclear`, license/source/weight/revision/model-card/
repository/paper queues, `eligible_for_model_draft`, `in_review`, `unreviewed`.

`eligible_for_model_draft` requires explicit source, traceability,
repository/model-card, license, weight, revision, clinical, security, family,
and productization conclusions plus input/output/preprocessing metadata. It
permits only a metadata-only draft. It does not mean weights are local, an
image exists, execution was validated, compatibility was proven, or
commercial use is allowed.

## API And UI

Four authenticated roles can read governance summary, record detail, review
timeline, and model-family candidates. Only the space operator can recalculate
profiles, append reviews, or resolve families. Write commands use strict
idempotency and emit audit events.

The read route is `/external-catalog/models/governance`. The operator route is
`/portal/operator/external-model-catalog/governance`.

The UI keeps the table and detail drawer locally scrollable on narrow screens
and continuously displays the non-materialized, non-executable boundary.

