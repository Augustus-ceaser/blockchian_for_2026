# Phase 5.12.7-H2 Readiness Responsive Acceptance

## Decision

```text
Phase 5.12.7-H2 accepted = true
loopback release candidate ready = true
v0.13 created = false
Phase 5.13 started = false
```

H2 restores the narrow-screen acceptance boundary. It does not authorize a
release tag by itself; tag creation remains a separate explicit action.

## Commits

```text
32ca680 fix: associate readiness labels with form controls
4109e46 docs: accept readiness accessibility hotfix
13eb282 fix: prevent readiness details overflow on narrow screens
documentation commit: this report's commit
```

No tag was created and nothing was pushed to a remote.

## Responsive Browser Acceptance

The accepted readiness detail was checked in real headless Chrome through the
loopback gateway at `127.0.0.1:18080`.

| Viewport | Client width | Scroll width | Overflowing elements | Table layout |
|---:|---:|---:|---:|---|
| 320x568 | 320 | 320 | 0 | fixed |
| 360x800 | 360 | 360 | 0 | fixed |
| 375x812 | 375 | 375 | 0 | fixed |
| 390x844 | 390 | 390 | 0 | fixed |
| 412x915 | 412 | 412 | 0 | fixed |
| 768x1024 | 768 | 768 | 0 | auto |
| 1366x768 | 1366 | 1366 | 0 | auto |
| 1920x1080 | 1920 | 1920 | 0 | auto |

At every viewport:

- no page-level horizontal overflow was present;
- no element extended outside the document;
- `hard_isolation=false` remained visible;
- desktop table behavior remained unchanged.

## Role And Route Regression

Five isolated authenticated contexts were used:

```text
hospital.demo
model.demo
requester.demo
operator.demo
catalog.curator.demo
```

The following existing records were opened without submitting a business
command:

```text
/execution
/execution/7635e4f1-04a8-5510-a510-af5982c6b125
/applications/5a131140-7619-5e52-962e-c874e0906944
/contracts/7635e4f1-04a8-5510-a510-af5982c6b125
/results/cb6f10f0-bb27-462c-89e7-99afb443a0cf
```

Hospital, model provider, requester, and operator sessions loaded all five
routes without page errors, mojibake, overflow, failed requests, or external
network requests.

The catalog curator loaded the shared page shells. Result data remained denied
by the existing authorization policy, producing the expected HTTP 403 and
visible access-denied state. Those Console resource errors are expected
authorization evidence, not an H2 regression.

## H1 Accessibility Check

All accepted Z2E contracts already have completed provider readiness. The H1
confirmation form is therefore absent from frozen runtime records. H2 did not
revoke readiness or create a replacement chain to force it to render.

The H1 association remains verified by the source regression, TypeScript
check, and production build:

```text
Form.Item htmlFor
textarea id
label id
aria-labelledby
description id
aria-describedby
```

## Automated Regression

```text
targeted readiness tests: 6 passed
frontend tests: 72 passed, 0 failed
pnpm typecheck: passed
container pnpm build: passed
backend pytest: 163 passed, 66 skipped
Python compileall: passed
OpenAPI: 164 paths, 170 operations, 170 unique operation IDs, 0 missing
Compose base config: passed
Compose Z2E config: passed
PowerShell script parse: passed
git diff --check: passed
changed-file secret scan: no findings
```

The skipped backend tests require dedicated destructive or phase-specific test
database settings and controlled PathMNIST smoke settings. They were not
silently enabled against canonical data.

Alembic reports one head and the running database is at that head:

```text
current: 20260728_0049 (head)
heads:   20260728_0049 (head)
```

`alembic check` still reports pre-existing autogenerate drift involving
schema-qualified foreign keys and reflected metadata. H2 has no migration or
backend change. This warning is retained as baseline debt rather than being
altered inside a CSS hotfix.

## Canonical Protection

Official read-only roadshow snapshots taken before and after H2 are
byte-identical:

```text
SHA-256:
d5d0d086a6185effcceacecbbd4da99de8cc73fc4eb0a5ede36a5b597356289a
```

Counts remained:

```text
Applications 3
Contracts 3
Jobs 3
Runs 2
Artifacts 2
Packages 2
Grants 2
Relations 7
Evidences 8
Plans 0
DataProducts 7
ModelProducts 4
MinIO objects 30
AuditEvents 353
Audit chain valid true
Alembic 20260728_0049
```

No canonical business write, reset, new chain, model download, or dataset
download was performed.

## Boundaries

- `hard_isolation=false` remains unchanged.
- This is an engineering roadshow prototype, not clinical production
  isolation or certification.
- No hospital Connector or distributed hospital-side Executor was added.
- No database, lifecycle, contract, run-count, quarantine, allowlist, or
  one-time-download behavior changed.
- No release tag was created.
- Phase 5.13 was not started.

