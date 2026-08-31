# Phase 5.12.7-H1 Readiness Accessibility Acceptance

## Decision

```text
Phase 5.12.7-H1 accepted = true
v0.13-roadshow-evidence-rc created = false
Phase 5.13 started = false
```

This hotfix is limited to the provider confirmation note on the execution
readiness page. It does not change an API, database schema, state machine,
authorization rule, readiness command, audit event, or frozen business record.

## Change

The visible `确认意见` label now has a complete programmatic association:

- `Form.Item.htmlFor` targets the textarea ID;
- the textarea has a stable `id`;
- `aria-labelledby` references the visible label text;
- `aria-describedby` references the explanatory text.

The existing controlled value, minimum-length submit guard, maximum length and
command behavior are unchanged.

## Automated Verification

```text
frontend tests: 72 passed, 0 failed
pnpm typecheck: passed
container pnpm build: passed
git diff --check: passed
```

The normal host build reached Vite output preparation but Windows denied
removal of the pre-existing `frontend/dist/favicon.svg`. The file was not
read-only and the failure was outside TypeScript compilation. A clean
alternative output build passed, and the rebuilt Z2E gateway ran the exact
container `pnpm build` successfully. The only build warning remains the
pre-existing large JavaScript chunk warning.

The readiness test now asserts all six parts of the association:

```text
htmlFor
textarea id
label id
aria-labelledby
description id
aria-describedby
```

## Browser Regression

The existing isolated Z2E environment was rebuilt with the H1 frontend and
started at `127.0.0.1:18080`. A real hospital demo session was used for
read-only checks.

Verified:

- execution list loads from the real API;
- the accepted Z2E readiness detail loads;
- no page error is shown;
- no browser Console warning or error is emitted;
- `hard_isolation=false` remains visible;
- desktop page-level width is contained.

All existing Z2E contracts have already completed provider readiness. The
conditional confirmation form therefore does not render on those frozen
records. H1 did not revoke readiness, create a replacement contract, submit a
command or alter business state merely to make the form reappear. The rendered
association is instead covered by the source regression, TypeScript check and
the production container build.

## Canonical Protection

Canonical application, PostgreSQL and MinIO containers remained stopped while
the isolated `medtrust-z2e` project was tested. The two previously frozen
canonical state snapshots remain byte-identical:

```text
SHA-256:
2EAB2E1C44FEC8E0806ED995F23FA84B04A21570710BBA2BC0E54DC61F1750B7
```

No canonical service was started and no canonical business write was made.

## Residual Observation

At the in-app browser's narrow viewport override, the already-completed
readiness detail has a pre-existing page-level horizontal overflow caused by
the Ant Design `Descriptions` table:

```text
clientWidth = 375
scrollWidth = 402
```

The H1 confirmation form is not rendered in that state and cannot cause this
overflow. Fixing the descriptions layout would be a separate responsive hotfix
and is intentionally outside H1.

## Boundaries

- `hard_isolation=false` remains unchanged.
- No real hospital Connector exists.
- No clinical, production-isolation or regulatory claim is added.
- No release tag is created.
- Phase 5.13 work is not started.
