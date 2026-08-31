# Phase 5.11.3B2 Eligible Candidate Ranking

## 1. Decision

The accepted B1 governance overlay currently contains **9** records with
`productization_eligible=true`. B2 selected five records for metadata-only
DataProduct drafts. The selection is based on the live Phase 5.11.3A state and
latest formal Reviews; it does not fetch any upstream page or dataset file.

Catalog version: `2026.07.27-12d5f08c`

All nine candidates have the same current governance score of `55`,
`license_review_status=permissive`, `access_review_status=open_download`, and
an approved productization Review. Therefore the score is only the first gate.
The ranking tie-breakers are:

1. breadth of the first metadata product set;
2. clarity and scope of the reviewed official source and license evidence;
3. avoidance of redundant disease/modality coverage or a large future
   materialization commitment;
4. preservation of the external-upstream boundary.

The catalog has null values for many size/sample fields. No claim that a
candidate is small or easy to materialize is inferred from a null field.

## 2. Ranking

| Rank | Dataset | Record ID | Modality / domain | License / access | Score | B2 decision |
| --- | --- | --- | --- | --- | ---: | --- |
| 1 | CPTAC-COAD | `377e1ee7-9647-437b-b1e1-79e770cb7234` | WSI / colon adenocarcinoma | CC BY 4.0 / public TCIA tooling | 55 | Selected |
| 2 | CAMELYON17 | `5c1b2324-fc02-4cfd-9256-b1c1c1542b9a` | WSI / breast cancer | CC0 stated on current Data page / open access | 55 | Selected |
| 3 | Hungarian-Colorectal-Screening | `51d66407-975c-4d8d-8761-ad2bfb0c4a40` | WSI / colorectal polyps | CC BY 4.0 / public TCIA tooling | 55 | Selected |
| 4 | HyperKvasir | `d6f56362-0d6a-4d5e-a603-8ceb1598e705` | Endoscopy / GI disease | CC BY 4.0 / open access | 55 | Selected |
| 5 | 4D-Lung | `0d34e89b-e4f5-4e90-8ed2-118e3e031934` | CT / lung cancer | CC BY 3.0 / public TCIA tooling | 55 | Selected |
| 6 | CPTAC-BRCA | `ef30ab88-3b4b-4657-b2d2-e3b5c8122ff6` | WSI / breast cancer | CC BY 4.0 / public TCIA tooling | 55 | Not selected; overlaps CAMELYON17 coverage |
| 7 | CPTAC-OV | `d9741bfd-2f0f-42be-bd92-f93baa86d397` | WSI / ovarian cancer | CC BY 4.0 / public TCIA tooling | 55 | Not selected; lower first-batch coverage value |
| 8 | CRC_FFPE-CODEX_CellNeighs | `2a38c775-bb89-4309-87e1-642faa01ce1d` | WSI / colorectal cancer | CC BY 4.0 / public TCIA tooling | 55 | Not selected; B1 evidence describes an approximately 2 TB package |
| 9 | TIL-WSI-TCGA | `c2b0de98-aa8f-4800-ab2d-2cff4a616025` | WSI-derived map / pan-cancer | CC BY 3.0 for derived maps / public | 55 | Not selected; license conclusion is limited to the derived result |

## 3. Why These Five

The five selected records provide two digital-pathology examples, two
colorectal examples at different collection scopes, one endoscopy example,
and one standardized CT/RTSTRUCT example. They are suitable for demonstrating
catalog-to-draft traceability without asserting that MedTrust Space owns or
hosts any upstream file.

The four unselected eligible records remain eligible in the governance layer.
They were not rejected and their immutable source/version records were not
changed. They can be reconsidered in a later batch after separate scope,
license, duplicate-coverage, or materialization review.

## 4. Boundary

This ranking does not publish a product, download data, create a model,
create an application, or create an execution job. The current `eligible_for_draft`
state means only that the formal B1 evidence permits the separate B2 draft
command to be attempted.
