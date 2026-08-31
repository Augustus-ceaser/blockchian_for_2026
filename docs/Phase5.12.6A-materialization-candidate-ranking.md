# Phase 5.12.6A Candidate Ranking

Date: 2026-07-28

This is an engineering materialization ranking, not a scientific performance
ranking. No data, weights, repositories or asset bodies were downloaded.

## Deterministic result

| Rank | Relation ID | Pair | Static evidence | Data access | Model access | Transformation | Hardware | Outcome |
|---|---|---|---|---|---|---|---|---|
| 1 | `f35cdda8-be20-52da-87e3-d35afeefbf49` | CAMELYON17 + CONCH | transformation required | public WSI, subset manifest not frozen | gated account, institutional approval and private token | tissue mask, fixed patch extraction and official transform not implemented | no discrete supported GPU detected; CPU feasibility unverified | blocked |
| 2 | `f630cbf2-db1f-5fd9-ba50-b8dc9a136165` | CAMELYON17 + UNI | transformation required | public WSI, subset manifest not frozen | gated account, institutional approval and private token | tissue mask, 224 px extraction and ImageNet normalization not implemented | ViT-L CPU budget and latency unverified | blocked |
| 3 | `908f9b00-ccc8-5685-bc0d-87a1a078f014` | CPTAC-COAD + CONCH | transformation required | TCIA usage policy applies; exact pathology subset and byte manifest not frozen | gated account, institutional approval and private token | WSI selection, tissue mask, patching and transform not implemented | no discrete supported GPU detected; CPU feasibility unverified | blocked |
| 4 | `163099a3-2589-598e-8f7e-d06e93c50ac3` | CPTAC-COAD + UNI | transformation required | TCIA usage policy applies; exact pathology subset and byte manifest not frozen | gated account, institutional approval and private token | WSI selection, tissue mask, 224 px extraction and normalization not implemented | ViT-L CPU budget and latency unverified | blocked |

Locked versions:

- CAMELYON17 DataProductVersion:
  `a756c1fa-b318-52e9-a9c7-11c2c0483651`
- CPTAC-COAD DataProductVersion:
  `99b88ccf-d4f1-5ce3-b8d4-f4ebff0bf775`
- CONCH ModelVersion: `87bf1cd5-3be2-5393-b7cd-34f1998305b2`
- UNI ModelVersion: `5ad11992-a1db-525b-ab83-03f26a373e7d`

## Common model blockers

The official model cards for both products state that downloading requires:

- prior registration on Hugging Face;
- an institutional primary email;
- an individual access request and acceptance of extra terms;
- authenticated access using a private user token.

Official sources:

- `https://huggingface.co/MahmoodLab/CONCH`
- `https://huggingface.co/MahmoodLab/UNI`

The terms are non-commercial research-only and prohibit redistribution of
model copies. MedTrust has no organizational grant, user authorization or
credential approved for this phase. Weight files and exact authenticated byte
manifests were therefore not requested.

## Dataset evidence

CAMELYON17's official site describes public H&E WSI data and TIFF/XML formats,
but the complete challenge is hundreds of gigabytes and the current platform
has no frozen patient/file subset with exact bytes and SHA-256 values:

- `https://camelyon17.grand-challenge.org/Data/`
- `https://camelyon17.grand-challenge.org/Download/`

CPTAC-COAD is governed by TCIA's data usage policy. The platform has not yet
frozen an exact pathology-only subset, per-file sizes and hashes:

- `https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=70229330`

## Recommendation

No pair is an approved or conditional candidate because gated model access is
a blocking condition, not a missing implementation detail. Phase 5.12.6B must
not start from these four pairs. The next catalog search should prioritize a
smaller pathology encoder with public unauthenticated weights, a permissive
weight license, immutable revision metadata and CPU-feasible inference.
