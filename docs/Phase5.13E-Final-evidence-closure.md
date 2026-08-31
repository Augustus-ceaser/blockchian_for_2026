# Phase 5.13E-Final: Hospital Evidence Closure

## Acceptance

`Phase 5.13E-Final accepted = true`.

This closes the engineering Alpha loop for the retained, pre-authorized
hospital-side reference execution. It does not claim clinical validation,
production isolation, legal compliance, hospital production deployment, or
certification. `hard_isolation=false` remains explicit.

## Closed flow

```text
quarantined hospital Artifact
-> deterministic local scan
-> independent hospital review
-> complete causal-chain validation
-> signed aggregate EvidenceBundle
-> verified central summary registration
```

The flow was completed through the real local browser forms with separate
Connector administrator and hospital Artifact reviewer sessions. The central
read-only registry was verified through the real `operator.demo` portal.

## Retained result

- Artifact: `03102c34-be57-4775-82b3-2b15bf381cb1`
- Execution: `dc79e6a7-f818-4e7f-8758-887720e3c4e2`
- Authorization snapshot: `1545bd7b-21f4-4bc0-bd4f-3cc4024d261b`
- Local status: `quarantined`
- Result: 20 samples, 19 correct, accuracy `0.95`
- Scan: `passed`, zero findings
- Independent review: `APPROVE_FOR_EVIDENCE_CANDIDACY`
- Causal validation: `passed`, all 33 checks true

The three retained files stayed byte-identical:

| File | Bytes | SHA-256 |
|---|---:|---|
| `aggregate_metrics.json` | 417 | `34a5b5acc62889e8f492ecb886e4a75245a6e3f00353a9bf5079ddedc54f2605` |
| `confusion_matrix.csv` | 461 | `4f4b57502a4bc4fceba24b160cbb3652fa869ac44ac7a0dec900adfc360ddf3e` |
| `execution_summary.json` | 958 | `7ac8f3552838fb06762c895fa79b590a405df93c1a39c1dc38843cd606eb84f1` |

## EvidenceBundle

- Local bundle: `3244bdd9-35b6-458c-ac30-7f13897aab3f`
- Bundle digest:
  `sha256:86d5ecebfb74a55199e3e602a93b86cbeec3c33d9dbe08305017509a08adab92`
- Central receipt: `3a051edd-1e4c-41c5-bf7e-77dcd2b1bc6a`
- Central verification: `verified`

The central record contains signed causal identifiers, digests, the three-file
manifest, aggregate result values, boundary flags, limitations, and the local
audit head. It does not contain Artifact bytes, a hospital filesystem path,
raw data, patient-level data, or model weights.

## Non-regression evidence

The central business counts after closure are:

```text
ComputeJob = 3
ComputeRun = 2
central Artifact = 2
ApprovedResultPackage = 2
ResultDownloadGrant = 2
ExecutionOrderConsumptionReceipt = 1
HospitalEvidenceBundleReceipt = 1
```

No new ComputeJob, ComputeRun, central Artifact, result package, download
grant, execution, or MinIO business object was created by this phase.

Validation results:

- Hospital Connector: `65 passed`
- Backend: `249 passed`, `66 skipped` because optional dedicated PostgreSQL
  and controlled-smoke environment gates were not enabled
- Frontend: `78 passed`
- Frontend typecheck: passed
- Frontend production build: passed, with the existing non-blocking large
  bundle warning
- Alembic current/head: `20260730_0058`
- Local schema: `phase5.13E_0011`
- OpenAPI: 209 operations, zero duplicate operation IDs
- Central audit chain: valid
- Local audit graph: valid; one known historical concurrency fork remains
  represented rather than rewritten

## Browser evidence

Screenshots are retained outside Git at:

`D:\MedTrustData\phase5.13E-2C-R1-EXEC\browser-evidence-final`

The central registry was checked at `390x844`, `768x1024`, `1366x768`, and
`1920x1080`. Page-level horizontal overflow was zero at all four sizes. There
were no page errors or external requests. The only Console error was the
expected initial anonymous session probe returning HTTP 401 before login.

## Boundaries and next direction

This Alpha proves a fixed, non-clinical, hospital-local reference execution can
produce a signed and centrally verifiable aggregate evidence summary without
central access to the retained Artifact. It does not prove arbitrary model
execution safety, strong sandbox isolation, hospital network deployment,
patient-data compliance, or regulatory conformity.

Further Executor security micro-features are not part of the immediate product
roadmap. The next work should focus on roadshow material, hospital pilot
design, deployment guidance, governance quality profiles, and StudyProtocol /
RWD design. A real hospital pilot requires separate legal, ethics, privacy,
security, infrastructure, and clinical governance review.
