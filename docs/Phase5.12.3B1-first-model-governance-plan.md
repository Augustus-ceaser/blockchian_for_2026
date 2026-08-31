# Phase 5.12.3B1 First Model Governance Plan

## Scope

Review at most eight primary candidates from the existing 16-record catalog.
Four backup candidates may replace a primary candidate only when official
evidence is inaccessible or the model identity cannot be resolved. No more
than ten distinct records may receive formal reviews.

This ranking is only an evidence-governance work queue. It is not a model
quality, scientific performance, clinical value, or deployment score.

## Candidate Overview

| Candidate | Category | Existing evidence | Initial blockers |
| --- | --- | --- | --- |
| CONCH | pathology foundation / VLM | Nature Medicine, author GitHub, HF card | gated weights, noncommercial terms, revision |
| CTransPath | pathology foundation | journal, author GitHub | author request, license and technical gaps |
| CellViT | spatial cell | journal, official GitHub | custom terms, revision and weight metadata |
| DeepPT | pathology omics | PMC, project GitHub | no weights, license and runtime gaps |
| H-optimus-0 | pathology foundation | project page, HF card | gated, model-card conditions |
| HoVer-Net | spatial cell | journal, official GitHub | license, revision and weight metadata |
| HookNet | spatial cell | journal, official GitHub | no weights, license and technical gaps |
| MedSAM | medical imaging | Nature Communications, official GitHub | license, revision and weight metadata |
| PLIP | pathology foundation / VLM | Nature Medicine, GitHub, HF card | weight license and integrity |
| Phikon | pathology foundation | preprint, GitHub, HF card | noncommercial terms and weight metadata |
| PraNet | endoscopy | arXiv, official GitHub | license, revision and weight metadata |
| Prov-GigaPath | pathology foundation | Nature, GitHub, HF card | gated weights and separate model terms |
| ST-Net | pathology omics | Nature Biomedical Engineering, GitHub | no weights, license and revision |
| UNI | pathology foundation | Nature Medicine, GitHub, HF card | gated weights and noncommercial terms |
| Virchow | pathology foundation | Nature Medicine, HF card | license, gated weights, no code repository |
| iStar | pathology omics / spatial | Nature Biotechnology, GitHub | no weights, architecture/license/revision gaps |

## Primary Batch

1. CONCH: foundation and vision-language coverage with official paper,
   repository, and model card.
2. UNI: foundation model with a parallel official evidence set and explicit
   gated/noncommercial boundary.
3. Prov-GigaPath: whole-slide foundation model with official paper,
   repository, and model card.
4. ST-Net: pathology-to-spatial-transcriptomics coverage.
5. DeepPT: pathology-to-bulk-transcriptomics and treatment-response research
   coverage.
6. CellViT: nuclei instance segmentation and cell classification coverage.
7. HoVer-Net: established nuclei segmentation/classification baseline with an
   official repository.
8. PraNet: gastrointestinal endoscopy segmentation coverage.

## Backup Batch

1. Phikon: replace a pathology foundation candidate.
2. PLIP: replace a vision-language/foundation candidate.
3. iStar: replace a pathology-omics candidate.
4. MedSAM: replace the endoscopy/imaging candidate.

Backups receive no formal review unless activated and documented.

## Evidence Budget

- Maximum five official pages per primary candidate.
- Recommended total requests: at most 60; hard limit: 80.
- Maximum response: 10 MB.
- Total saved evidence: at most 500 MB.
- Allowed content: official paper metadata, repository metadata/README/LICENSE,
  model card metadata, small configuration files, release metadata, and file
  names/sizes/LFS pointers.
- Blocked content: model weights, archives, source ZIPs, release assets,
  inference endpoints, authenticated or gated access, and repository clones.

## Expected Decisions

Each reviewed candidate may receive source, paper, repository, model-card,
license, weights, revision, technical-contract, clinical-boundary, security,
and productization reviews. Unknown facts remain unknown. Code licenses and
weight/model-use terms must be recorded separately in evidence payloads.

Eligibility is not a target quota. A candidate becomes
`eligible_for_model_draft` only when every rule is supported by official
evidence. Eligibility permits only a metadata-only ModelProduct draft and does
not establish local weights, executability, compatibility, performance,
clinical use, or redistribution rights.
