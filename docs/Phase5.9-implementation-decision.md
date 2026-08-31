# Phase 5.9 Implementation Decision

## Scope and boundary

Phase 5.9 adds product lifecycle governance and four independently authenticated
local demo portals. It does not change network binding, public deployment,
controlled execution, `run_count`, contract policy, Artifact isolation, result
package allowlists, or download grants. The application remains a local,
non-clinical engineering demonstration with `hard_isolation=false`.

## Existing state

- `DataProduct` and `ModelProduct` use product lifecycle states. Their versions
  use `draft`, `under_review`, `approved`, and `retired`.
- `DataProductPublication.published_at` and `ModelPublication.published_at`
  already record a version's formal publication time. A new publication is
  created for each later relist; historic publication timestamps are immutable.
- Version `approved_at` records the platform listing decision. Product creation
  and update timestamps already come from the server.
- Existing `ReviewTask` is bound to `Application` and `ApplicationSnapshot`.
  Reusing it for a product action would create a false aggregate relationship.
- Current demonstration identity is client-controlled through
  `X-Demo-Identity`; it is not an authentication boundary. Identity, user,
  organization, membership, and member role records already exist.

## Lifecycle model

One generic `ProductLifecycleRequest` is introduced for both data and model
products. It records target type and IDs, action (`unpublish`, `relist`,
`archive`), requester, reason and structured request details, server-generated
impact snapshot/digest, status, decision evidence, timestamps, and an
idempotency digest. A partial unique index allows only one open request for a
product, regardless of action.

Product state and request state remain separate. A pending unpublish leaves a
published product selectable until platform approval. Approval withdraws the
active publication and marks the product `unpublished`; a relist approval
creates a new active publication for the same immutable approved version.
Archive is logical only: it marks the product `archived`, writes `deleted_at`,
and never deletes referenced history. The primary PathMNIST data and ResNet-18
model demo products are protected from archive.

`unpublished_at`, `deleted_at`, and latest lifecycle timestamps are added to
each product. All timestamps are generated in UTC by the server. The UI
formats them in the browser time zone and identifies the time zone.

## Impact and approval

Lifecycle request creation and operator decisions are domain-service commands,
not frontend state changes. The server queries applications, contracts, jobs,
runs, artifacts, packages, grants, organizations, and audit-chain validity to
produce a stored impact snapshot. A decision is blocked for running work,
unresolved result safety work, invalid audit evidence, or an unsafe active
execution situation. Active contracts are never altered by product withdrawal:
new selection is prevented after withdrawal, while historical contracts and
their locked versions remain auditable.

An operator can approve, reject, return, or an owner can cancel an open request.
Every command emits a typed AuditEvent and uses a command/idempotency context.
The PostgreSQL audit-event guard and event check constraint are extended by a
new incremental migration; no historical migration changes.

## Authentication and portals

The four existing Phase 4 demo users and organizations are retained. A local
demo credential record stores a unique username and a password hash only; the
raw password is supplied through ignored local configuration or test
environment, never stored in source, the database, audit evidence, or logs.
An opaque random session secret is delivered in a SameSite=Lax HttpOnly cookie;
only its SHA-256 digest is persisted. Distinct browser profiles therefore have
independent sessions.

All API actor resolution derives the identity from the active server session.
The `X-Demo-Identity` header is accepted only when
`MEDTRUST_ENABLE_DEMO_ROLE_SWITCH=true`, and even then it may select only the
already authenticated account. Its default is false. The frontend uses
credentialed requests, calls `/auth/me`, and does not keep authority in
`sessionStorage`.

Each portal has a role-specific navigation surface and route guard. Backend
authorization remains authoritative: hiding a menu is not permission control.
The normal header shows the current account, organization, and portal. The
legacy role switch is explicitly local-debug-only and hidden by default.
`/roadshow` remains available but directs the presenter to the next
organization's browser window rather than switching identity.

## Migration and regression risk

The incremental migration adds credentials, sessions, lifecycle requests, and
minimal product timestamps/state support. It also extends audit vocabulary and
guard logic. Existing product-selection queries already require active
publications; submission will be revalidated against an active, non-archived
publication. Historic applications, contracts, jobs, runs, artifacts, packages,
grants, and audit events retain their identifiers and references.

Regression coverage must cover lifecycle idempotency and concurrency, direct
API authorization, password hashing and session isolation, product selection
after state change, audit-chain validity, all Phase 5.1--5.8 behavior, and four
isolated browser contexts. LAN, public, and cloud deployment are explicitly
out of scope.
