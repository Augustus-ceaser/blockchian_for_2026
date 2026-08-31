# Phase 5.12.5 Dataset-Model Evidence Design

Date: 2026-07-27

## Boundary

Evidence is locked to one `DataProductVersion` and one `ModelVersion`. Product
pages aggregate only relations for their current active published versions.
Publication, shared disease names and shared image terminology never establish
compatibility.

```text
author declaration != static schema review
static schema review != runtime execution
runtime execution != platform verification
```

This phase permits only `external_declaration` and `platform_static_review`.
`runtime_execution` and `platform_verification` are schema-reserved and rejected
by all operator APIs.

## Existing Version Locks

- Data versions have `snapshot_digest`.
- Model versions have `snapshot_digest` and `model_digest`.
- Both external source links have immutable `source_record_digest` and
  `governance_snapshot_digest`.
- Relations copy those six digests and both source-link IDs.
- A new product version creates a new relation. Existing evidence never moves.
- Unpublished, archived or draft products are private and cannot be published.

## Objects

`DatasetModelRelation` is the current projection for a version pair. It stores
the strongest level, current evidence, status and publication state. Its
version/source/governance lock fields are database-immutable.

`DatasetModelEvidence` is append-only. A new conclusion references
`supersedes_evidence_id`; update and delete are rejected by PostgreSQL. The
relation is recalculated by the domain service in the same transaction.

Permitted current statuses in this phase:

- `external_declaration_only`
- `static_schema_compatible`
- `static_schema_compatible_with_transformation`
- `static_schema_incompatible`
- `insufficient_metadata`

Reserved statuses `executed`, `execution_failed` and `verified` cannot be
created by the operator API.

## Actors And Visibility

- All authenticated project roles may read public relations.
- The operator may read the full matrix, including unassessed candidates.
- Only `space_operator` may append static evidence or publish a relation.
- A relation is public only when both exact versions have active publications,
  both products are active, evidence is current operator evidence and all
  locked digests still match.
- Prov-GigaPath remains a draft and is never part of the public matrix.

## Compute Separation

Evidence is descriptive and cannot unlock Application, Contract, readiness,
ComputeJob or ComputeRun. Existing materialization gates remain authoritative.
The new services do not import or invoke execution services.

## Runtime Evidence Reservation

Future internal services must bind runtime evidence to a terminal real
`ComputeRun` with matching data/model versions. Verification must additionally
bind a successful run, Artifact and approved release/review fact. These paths
are intentionally absent from the public/operator router in this phase.

## Audit

The phase records relation creation, evidence append, supersession, status
change and publication change. Evidence snapshots contain identifiers,
digests, levels, outcomes and state transitions only. Cookies, tokens, local
paths, full payloads and signed URLs are excluded.

## Initial Matrix

Only the three active published external data products and two active published
external model products are eligible, for six pairs maximum. Candidate
generation is deterministic and read-only. Formal evidence is created only by
authenticated operator commands.
