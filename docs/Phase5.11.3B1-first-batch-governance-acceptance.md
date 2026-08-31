# Phase 5.11.3B1 First-Batch Governance Acceptance

Date: 2026-07-27

## Result

Phase 5.11.3B1 is accepted for the first evidence-led batch.

- Main candidates reviewed: 20
- Backup candidates reviewed: 0
- Formal GovernanceReview rows: 80
- Unique reviewed records: 20
- Source confirmed: 20
- Explicit license conclusions: 13
- License left unverified: 7
- Explicit access conclusions: 17
- Access left unknown: 3
- Duplicate resolutions: 0
- Productization approvals: 9
- `eligible_for_draft`: 9
- Dataset files downloaded: 0
- DataProduct created: 0
- ModelProduct created: 0
- Application created: 0
- ComputeJob created: 0

The database still contains the pre-existing one DataProduct, one ModelProduct,
three Applications, and three ComputeJobs. This phase did not add to those
counts.

## Governed Candidates

| Candidate | Source | License | Access | Productization |
|---|---|---|---|---|
| CPTAC-COAD | confirmed | CC BY 4.0 | open download | approved |
| CRC_FFPE-CODEX_CellNeighs | confirmed | CC BY 4.0 | open download | approved |
| CoNIC2022 | confirmed | unverified | registration required | unreviewed |
| CoNSeP | confirmed | unverified | unknown | unreviewed |
| Colorectal Histology MNIST | confirmed | unverified | open download | unreviewed |
| DigestPath19 | confirmed | unverified | unknown | unreviewed |
| GlaS | confirmed | unverified | unavailable | unreviewed / blocked |
| Hungarian-Colorectal-Screening | confirmed | CC BY 4.0 | open download | approved |
| OCELOT2023 | confirmed | unverified | registration required | unreviewed |
| PAIP2021 | confirmed | CC BY-NC 4.0 plus DUA | registration required | unreviewed |
| CPTAC-BRCA | confirmed | CC BY 4.0 | open download | approved |
| CPTAC-HNSCC | confirmed | component-specific controlled terms | controlled access | unreviewed |
| CPTAC-OV | confirmed | CC BY 4.0 | open download | approved |
| TIL-WSI-TCGA | confirmed | CC BY 3.0 for derived maps | open download | approved |
| CAMELYON17 | confirmed | CC0 on current Data page | open download | approved |
| MedMNIST | confirmed | component-specific custom terms | open download | unreviewed |
| 3D-IRCADb | confirmed | CC BY-NC-ND 4.0 | open download | unreviewed |
| 4D-Lung | confirmed | CC BY 3.0 | open download | approved |
| AIDA-E_3 | confirmed | unverified | unknown | unreviewed |
| HyperKvasir | confirmed | CC BY 4.0 | open download | approved |

`open_download` describes the official access condition. No download endpoint
or dataset file was requested during this phase.

## Evidence Boundary

Evidence and browser artifacts are outside Git:

```text
D:\MedTrustData\catalog-governance-evidence\phase5.11.3B1
D:\MedTrustCache\catalog-governance\phase5.11.3B1
```

The capture tool enforces:

- HTTPS only;
- globally routable, credential-free hosts;
- at most 3 accepted pages per record and 75 accepted pages in total;
- at most 10 MiB per response and 5 redirects;
- HTML, plain text, JSON, or JSON-LD only;
- rejection of attachments and dataset/archive extensions;
- no persisted response body.

Eighteen official metadata pages were accepted by the local capture tool.
Three attempted official sites failed DNS or TLS validation and were not
counted as successful local captures. They were not retried with disabled TLS
verification. Official indexed pages or publisher records were used for the
governance conclusion, and uncertain fields remained unverified.

## Implementation Corrections

Two governance defects were fixed:

1. Eligibility previously required the immutable imported
   `ExternalDatasetRecord.official_source_url`. All 982 imported records have
   that field empty, so no evidence could ever make a record eligible. The
   calculator now consumes the latest append-only source Review as an overlay.
2. `metadata_incomplete` previously hid `eligible_for_draft` even when the
   eligibility boolean was true. Eligibility now determines the primary status
   after all hard blockers and required review gates; noncritical missing
   metadata remains a warning.

The operator UI was rebuilt from corrupted Chinese source text and now captures
structured source, license, permission, access, and evidence fields. No API or
state-machine value changed.

## Runtime Evidence

Database:

```text
GovernanceReview = 80
reviewed records = 20
DuplicateResolution = 0
eligible_for_draft = 9
audit chain valid = true
```

Chrome at `390x844`:

```text
page scrollWidth = 390
page clientWidth = 390
Console errors = 0
external requests = 0
summary shows 982 profiles, 80 Reviews, 9 eligible
CPTAC-COAD shows source, license, access, productization Reviews
structured license fields render correctly
```

Regression:

```text
backend available test suite: passed
focused governance/evidence tests: 13 passed
frontend tests: 56 passed
frontend typecheck: passed
frontend production build: passed
Python compile: passed
base Compose config: passed
LAN Compose config with ignored local environment: passed
git diff --check: passed
```

PostgreSQL-backed test modules that require dedicated destructive test database
variables remained skipped by the repository's existing gates. The live
database evidence above was collected through formal APIs and read-only checks.

## Preserved Boundaries

- Original external records and version payloads were not rewritten.
- Reviews remain append-only and idempotent.
- No dataset, model, archive, DICOM, WSI, NIfTI, HDF5, FASTQ, or matrix file was
  downloaded.
- No direct SQL or ORM business-state write was used.
- No registration or access application was submitted.
- No LAN port, firewall rule, WLAN category, tunnel, or remote preview changed.
- `hard_isolation=false` remains unchanged.
- No release tag was created.

Final runtime state:

```text
backend/frontend/workers/coordinator = stopped
ports 8000/5173/8080 = free
PostgreSQL = running, healthy, loopback only
MinIO = running, loopback only
```
