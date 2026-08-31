# Phase 5.12.3A Model Governance Acceptance

## Result

Phase 5.12.3A is accepted for metadata governance.

| Check | Result |
| --- | --- |
| Alembic | `20260727_0042 -> 20260727_0043`, single head |
| Empty database migration | upgrade, downgrade, re-upgrade passed |
| ExternalModelRecord / Version | 16 / 16, unchanged |
| GovernanceProfile / Review | 16 / 0 |
| FamilyResolution | 0 |
| Execution status | 16 `not_materialized` |
| ModelProduct | 1 historical, 0 added by this phase |
| Weights / clone / inference | 0 / 0 / 0 |
| Audit chain | valid |

All initial profiles are `security_review_required` because the deterministic
risk projection identifies unresolved execution/security metadata. All 16 are
ineligible for draft creation. This is a queue classification, not a claim
that the upstream models are unsafe.

The raw record and version row digests were identical before and after
migration and profile initialization:

- records: `19f36a7152f1ce98c8b79078e418fb98`
- versions: `c312f7596dfcd5d5e8bad4c1c5706ee5`

## Runtime Evidence

- Four real accounts read the governance summary.
- Hospital, model provider, and requester writes returned HTTP 403.
- Operator initialization created exactly 16 profiles.
- Exact idempotency replay returns the original result without a new event.
- Chrome checked four accounts at 390x844, 768x1024, 1366x768, and 1920x1080.
- Page-level overflow, Console errors, failed requests, and external requests:
  zero.

One `governance.recalculated` audit event records the acceptance-time replay
that exposed and led to the strict replay fix. The event is retained as real
append-only evidence.

## Regression

- Backend: 142 passed, 66 environment-gated skipped.
- Frontend: 64 passed.
- TypeScript typecheck and production build: passed.
- Python compile: passed.
- OpenAPI: 143 paths, 147 operations, duplicate operation IDs 0.
- Base/LAN Compose: passed.
- PowerShell: 26 files, parse errors 0.
- `alembic check` retains the pre-existing global schema/FK comparison noise;
  no new 0043-specific drift was identified.

## Boundaries

The runtime remains `hard_isolation=false`. This acceptance is not production
or clinical validation. No LAN, firewall, tunnel, tag, or `v0.13` change is
part of this phase.

