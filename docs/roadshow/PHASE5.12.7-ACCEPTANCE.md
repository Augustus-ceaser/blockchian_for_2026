# Phase 5.12.7 Unified Roadshow Seal Acceptance

Date: 2026-07-28

## Decision

`Phase 5.12.7 accepted = true`

This seals the core public-data/model engineering roadshow. It does not seal
external model weight materialization, hospital-side execution, production
hard isolation, clinical validation, or regulatory certification.

## Frozen State

- source baseline: `874f9ab6ff33e9195720679effe3edb26147f068`
- implementation commit: `c926eaf`
- Alembic: `20260728_0049`
- PostgreSQL volume: `medtrust-space_postgres_data`
- MinIO volume: `medtrust-space_minio_data`
- manifest digest:
  `sha256:9ec948386ff69804baee50218292c130a289369b02d35b3649a91a4e307641d7`
- audit sequence: 353
- audit head:
  `sha256:75b9b969d8a3d81f65aab15c5fbfa8ab3b6c805581624cc0cac602332db7e866`
- audit chain valid: true

## Canonical Counts

| Item | Count |
|---|---:|
| External dataset records | 982 |
| External model records | 16 |
| Published metadata data products | 3 |
| Published metadata model products | 2 |
| Dataset-model relations | 7 |
| Static transformation relations | 4 |
| Static incompatible relations | 2 |
| Platform verified relations | 1 |
| Approved materialization plans | 0 |
| Compute runs | 2 |
| MinIO objects | 30 |

CONCH and UNI remain `external_metadata_only`: no weights were downloaded,
no Executor was registered, and neither product is executable.

The one verified relation is locked to the historical PathMNIST demo subset
and fixed ResNet-18 version. It records 19 correct predictions from 20 samples,
aggregate accuracy 0.95, real CPU execution, quarantined Artifact, 3/3 result
reviews, a three-file ReleasePackage, and exhausted one-time DownloadGrant.

## Read-Only Acceptance

Three complete start/status/page/stop cycles passed. The before and after state
files in every cycle had this identical SHA-256:

`D5D0D086A6185EFFCCEACECBBD4DA99DE8CC73FC4EB0A5EDE36A5B597356289A`

No Application, Contract, Job, Run, Artifact, package, grant, relation,
evidence, plan, audit event, or MinIO object was created by acceptance.

## Browser Acceptance

- five isolated authenticated accounts passed;
- 21 core routes loaded without page errors;
- 390x844, 768x1024, 1366x768 and 1920x1080 were tested;
- the 390px evidence-matrix overflow found during acceptance was fixed and
  rechecked at `scrollWidth=390`, `clientWidth=390`;
- no external network request was observed;
- no non-login write request was observed;
- no JavaScript page error was observed;
- Console output was limited to existing Ant Design deprecation warnings.

## Regression

- backend: 163 passed, 66 skipped;
- skipped backend suites require dedicated PostgreSQL or controlled-smoke
  environment variables and were not silently treated as passed;
- frontend: 71 passed, 0 failed;
- TypeScript typecheck: passed;
- production build: passed;
- Python compileall: passed;
- video full linear decode: passed.

## Media

- screenshots: `D:\MedTrustData\roadshow-media\phase5.12.7\screenshots`
- video: `D:\MedTrustData\roadshow-media\phase5.12.7\MedTrust-Space-Phase5.12.7-Roadshow-CN.mp4`
- subtitles: `D:\MedTrustData\roadshow-media\phase5.12.7\ROADSHOW-CN.srt`
- video: H.264, 1920x1080, yuv420p, 420.000 seconds;
- audio: AAC stereo, 420.021 seconds;
- video SHA-256:
  `8207A64B64AC9EA0E8A5666AA2A1D0E93C49C19C72518492B394369176FD5513`
- subtitle SHA-256:
  `F5D22EB267B765525EE9F1B59C0BDA77268A832CDDD9BA1438132394C48CA0EA`

## Final Boundaries

`loopback_ready=true`

`lan_manual_gate=true`

`remote_manual_gate=true`

`hard_isolation=false`

`v0.13=not_created`
