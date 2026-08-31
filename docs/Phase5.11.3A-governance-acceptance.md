# Phase 5.11.3A Governance Acceptance

Date: 2026-07-27

## Runtime State

| Object | Count |
|---|---:|
| ExternalDatasetRecord | 982 |
| ExternalDatasetVersion | 982 |
| ExternalDatasetGovernanceProfile | 982 |
| ExternalDatasetGovernanceReview | 0 |
| ExternalDatasetDuplicateResolution | 0 |
| DataProduct | 1 existing; 0 added |

Initial computed states:

- `needs_license_review`: 836
- `duplicate_pending`: 143
- `blocked`: 3
- `eligible_for_draft`: 0

There are 49 populated duplicate group IDs. Upstream quality flags represent 8
duplicate-name groups/17 records and 45 duplicate-URL groups/137 records.

## Evidence

- Existing database migrated from `20260727_0034` to single head
  `20260727_0038`.
- A dedicated empty PostgreSQL database migrated from base through
  `20260727_0038` and was removed after verification.
- Formal operator API initialized 982 profiles; idempotent replay returned the
  original `982/982` result and did not add an AuditEvent.
- Hospital, model provider, requester, and operator sessions all read the
  governance summary.
- A hospital write attempt returned HTTP 403.
- Formal directory Review count stayed zero; write behavior is tested without
  inventing real catalog conclusions.
- Audit chain verification returned valid with no invalid sequence.
- Backend full available pytest run passed; environment-gated PostgreSQL and
  controlled-execution suites remained skipped by their existing gates.
- Focused governance/catalog/audit tests: 15 passed.
- Frontend typecheck and production build passed.
- Real Chrome tested four accounts at 390x844, 768x1024, 1366x768, and
  1920x1080; all 16 checks had no page-level overflow.
- The first browser context requested a missing favicon once. React development
  mode aborted superseded `auth/me` and deployment-status requests; subsequent
  requests succeeded and governance content rendered.

## Boundaries

External URL requests: 0. Dataset/model downloads: 0. New DataProducts: 0.
Original records and versions were not mutated. LAN, firewall, tunnel, remote
preview, and release tags were not changed. `hard_isolation=false` remains the
authoritative prototype boundary.
