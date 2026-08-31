# Phase 5.12.7 Final Browser Review

Review time: 2026-07-28 10:14:07 +08:00

## Decision

`final_browser_review_passed = true`

Recommendation: `maintain Engineering Roadshow RC`

This was a read-only visual and technical browser review. It did not create or
mutate business objects, download assets, execute inference, expose LAN ports,
or create a Git tag.

## Frozen Environment

- reviewed commit: `afcd8dd9fb381c1902b07c7616af98673e511245`
- implementation commit present: `c926eaf`
- documentation commit present: `afcd8dd`
- Alembic: `20260728_0049`
- entry URL: `http://127.0.0.1:5173/roadshow`
- PostgreSQL volume: `medtrust-space_postgres_data`
- MinIO volume: `medtrust-space_minio_data`
- PostgreSQL single writer: true
- application binding: loopback only
- `hard_isolation=false`
- tag created: no
- `v0.13` created: no

`ROADSHOW-NAVIGATION.md` and `ROADSHOW-KNOWN-LIMITATIONS.md`, named in the
review instruction, were not present in the accepted repository. Review
coverage was therefore derived from the committed acceptance/quick-start
documents, the actual router, live API projections, and visible navigation.
This is a documentation inventory gap, not a runtime failure.

## Session Isolation

Five independently created browser contexts authenticated successfully:

| Portal | Account | Login | Isolated session |
|---|---|---:|---:|
| Operator | `operator.demo` | 200 | yes |
| Hospital | `hospital.demo` | 200 | yes |
| Model provider | `model.demo` | 200 | yes |
| Requester | `requester.demo` | 200 | yes |
| Catalog curator | `catalog.curator.demo` | 200 | yes |

All five contexts held distinct session-cookie values. Passwords and cookie
values were not printed, captured, committed, or included in this report.

The hospital context was denied the operator evidence route. The requester
application options did not contain CONCH or UNI. No role crossover was found.

## Page Review

The requested 21-page coverage was satisfied through 23 routed page checks,
including two supplementary governance/workflow surfaces:

1. unified roadshow overview;
2. external dataset catalog;
3. CPTAC-COAD product detail;
4. CAMELYON17 product detail;
5. HyperKvasir product detail;
6. external model catalog;
7. CONCH product detail;
8. UNI product detail;
9. dataset-model evidence matrix;
10. materialization plans;
11. dataset governance;
12. model governance;
13. published data catalog;
14. published model catalog;
15. lifecycle review;
16. applications;
17. contracts;
18. historical execution;
19. result list;
20. reference Artifact/result detail;
21. audit chain;
22. governance boundary page;
23. historical roadshow workflow.

All routes loaded without a page error or page-level horizontal overflow.

Visible evidence confirmed:

- 4 static relations requiring transformation;
- 2 static incompatible relations;
- 1 verified PathMNIST/ResNet-18 reference relation;
- CONCH and UNI are metadata-only;
- model weights are not downloaded or materialized;
- external Executor registration is absent;
- CONCH and UNI are not executable or selectable for a new request;
- reference Artifact remains `quarantined`;
- result review is 3/3 approved;
- ReleasePackage contains exactly `aggregate_metrics.json`,
  `confusion_matrix.csv`, and `execution_summary.json`;
- one-time grant is exhausted at 1/1;
- audit chain is valid.

## Responsive Review

Seven focus pages were tested at four viewports, for 28 combinations:

- 390x844;
- 768x1024;
- 1366x768;
- 1920x1080.

Focus pages:

- unified overview;
- evidence matrix;
- CPTAC-COAD detail;
- CONCH detail;
- verified relation matrix;
- materialization plans;
- reference ReleasePackage/result detail.

Results:

- page-level horizontal overflow: 0;
- mojibake pages: 0;
- text/button overlap found: 0;
- badge overlap found: 0;
- broken navigation controls: 0;
- long IDs remained wrapped or locally contained;
- wide tables used local scrolling without enlarging the document width.

The evidence matrix, CPTAC-COAD, CONCH and reference result pages were also
visually inspected from captured desktop/mobile renders.

## Browser Technical Results

| Check | Result |
|---|---:|
| Actionable Console errors | 0 |
| JavaScript page errors | 0 |
| Unhandled Promise errors | 0 |
| React/hydration errors | 0 |
| Unexpected 404/500 responses | 0 |
| Failed requests | 0 |
| External application requests | 0 |
| Weight requests | 0 |
| Dataset-file requests | 0 |
| Inference requests | 0 |
| Non-login write requests | 0 |
| Sensitive information findings | 0 |
| Clinical exaggeration findings | 0 |

The development build emitted three unique existing Ant Design deprecation
warnings (`Alert.message`, `Drawer.width`, and `Space.direction`). They were
repeated during route navigation, but are not request failures, React errors,
unhandled exceptions, or product-state failures. No code was changed because
this review permits only an explicit display/request fix and these warnings do
not affect the sealed roadshow behavior.

## Wording Boundaries

No page claimed that:

- 982 datasets were downloaded;
- 16 models were deployed;
- CONCH or UNI had executed;
- static evidence was platform verification;
- accuracy 0.95 was clinical accuracy or full-PathMNIST performance;
- MedTrust owned upstream assets;
- the system was a hospital production deployment;
- hard isolation or official certification existed.

Required boundaries were visible: metadata-only cataloging, unmaterialized
weights, absent external Executor, non-executable external models, static
evidence not equal to execution, fixed 20-sample scope, locked
PathMNIST/ResNet-18 versions, non-clinical engineering use, and
`hard_isolation=false`.

## Before/After State

Before SHA-256:

`D5D0D086A6185EFFCCEACECBBD4DA99DE8CC73FC4EB0A5EDE36A5B597356289A`

After SHA-256:

`D5D0D086A6185EFFCCEACECBBD4DA99DE8CC73FC4EB0A5EDE36A5B597356289A`

The projections were byte-for-byte identical.

| Object | Before | After |
|---|---:|---:|
| ExternalDatasetRecord | 982 | 982 |
| ExternalModelRecord | 16 | 16 |
| DataProduct | 7 | 7 |
| ModelProduct | 4 | 4 |
| Dataset governance reviews | 80 | 80 |
| Model governance reviews | 96 | 96 |
| DatasetModelRelation | 7 | 7 |
| DatasetModelEvidence | 8 | 8 |
| MaterializationPlan | 0 | 0 |
| Application | 3 | 3 |
| Contract | 3 | 3 |
| ComputeJob | 3 | 3 |
| ComputeRun | 2 | 2 |
| Artifact | 2 | 2 |
| ReleasePackage | 2 | 2 |
| DownloadGrant | 2 | 2 |
| MinIO objects | 30 | 30 |
| Audit events | 353 | 353 |

Audit head and sequence did not change during login and page reads. Invalid
audit chains remained 0.

## Final Recommendation

No runtime or visual blocker was found. Keep the current release candidate:

`maintain Engineering Roadshow RC`

Do not create a release tag until separately authorized.
