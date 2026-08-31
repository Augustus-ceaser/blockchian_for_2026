# Phase 5.12.4 Model Publication Candidate Ranking

Date: 2026-07-27

This ranking uses only the frozen external catalog, immutable B2 links and 96
formal governance reviews. Paper performance is not a selection criterion.

## Ranking

| Rank | Candidate | Recommendation | Main reason |
|---:|---|---|---|
| 1 | CONCH | publish | Explicit noncommercial/no-redistribution terms; distinct vision-language catalog value |
| 2 | UNI | publish | Explicit noncommercial/no-redistribution terms; clear tile embedding contract |
| 3 | Prov-GigaPath | keep_draft | Code is Apache-2.0 but model-weight redistribution and derivative rights remain unknown |

## CONCH

- Product ID: `0d198645-7d1e-564e-95c3-270404bae09d`
- External ID: `lxltx_model_02465d9c4a750405`
- Categories: pathology foundation, vision-language
- Modalities/tasks: histopathology tiles and text; feature extraction,
  retrieval and zero-shot classification
- Upstream provider: Mahmood Lab
- Paper/repository/model card: official and confirmed
- Code/weight terms: governed together as CC BY-NC-ND 4.0 with project terms;
  commercial use false, redistribution false, derivatives false
- Weight access: gated; recorded file metadata about 802 MB; not downloaded
- Revision: pinned review value
  `f9ca9f877171a28ade80228fb195ac5d79003357`
- Source digest: `10e3fc10f07cf88645ba70135a615441af4f223624903084995c5c51ae334ba7`
- Governance digest:
  `sha256:60622b57e2c9567e344145f70ffb0c99cfd0d5dca97e76ef9570d43bd309850d`
- Reviews: 12/12
- Contract: image/text input, image/text embedding output, official transforms
  and tokenizer; score 92; evaluation references remain missing
- Clinical/security: research only; static metadata review cleared; sandbox
  validation not performed
- Materialization difficulty: gated access, nonredistributable terms and
  untested code/dependencies
- Roadshow value: demonstrates multimodal model discovery
- Risk: medium, bounded by explicit research-only metadata policy
- Recommendation: `publish`

## UNI

- Product ID: `6f62774e-fe8e-5109-a02a-99d14b7eab16`
- External ID: `lxltx_model_b98abcd818aebb61`
- Category: pathology foundation
- Modality/task: histopathology tiles; feature extraction
- Upstream provider: Mahmood Lab
- Paper/repository/model card: official and confirmed
- Code/weight terms: governed together as CC BY-NC-ND 4.0 with project terms;
  commercial use false, redistribution false, derivatives false
- Weight access: gated; recorded file metadata about 1.21 GB; not downloaded
- Revision: pinned review value
  `b55a5ec6cade1a39edfe6534189a9b8ca7a022f0`
- Source digest: `07cb24dac87b28ab154f23c5ecefdac7aa89e78e30782ed33e4dc6a54f07616e`
- Governance digest:
  `sha256:4a9ad78c8de06be2a0da7f1908cf1f02702bcc16838341e013a50577bbcb917f`
- Reviews: 12/12
- Contract: RGB tile input, tile embedding output and official model-card
  transforms; score 92; evaluation references remain missing
- Clinical/security: research only; static metadata review cleared; sandbox
  validation not performed
- Materialization difficulty: gated access, nonredistributable terms and
  untested runtime
- Roadshow value: clear foundation-model catalog example
- Risk: medium, bounded by explicit research-only metadata policy
- Recommendation: `publish`

## Prov-GigaPath

- Product ID: `6a2d9dcd-78fd-5d88-b546-0e6ba50c6697`
- External ID: `lxltx_model_fc17d110f31cfd74`
- Category: pathology foundation
- Modality/tasks: whole-slide histopathology; tile and slide embeddings
- Upstream provider: Providence and Microsoft Research
- Paper/repository/model card: official and confirmed
- Code license: Apache-2.0
- Weight/model terms: research and reproducibility; commercial/deployed use
  false; redistribution and derivatives unknown
- Weight access: gated; recorded file metadata about 4.89 GB; not downloaded
- Revision: pinned review value
  `eba85dd46097c3eedfcc2a3a9a930baecb6bcc19`
- Source digest: `50ed8cb1ce6b5ac088f90037e4fb5fcd4e886a81194d2ac3e0304d63ce3ace92`
- Governance digest:
  `sha256:fc3f0a4a40d0086f4bfedfef0b92315c035dd04b77798c85f9cd1af7faa2fcbf`
- Reviews: 12/12
- Contract: tiled WSI plus coordinates, tile/slide embeddings and official
  pipeline; score 92; evaluation references remain missing
- Clinical/security: research only; static metadata review cleared; sandbox
  validation not performed
- Materialization difficulty: largest candidate, multi-stage runtime, gated
  assets and unresolved weight rights
- Roadshow value: whole-slide architecture diversity
- Risk: high for publication because code and model-weight rights cannot be
  presented as one license
- Recommendation: `keep_draft`

## Selection

CONCH and UNI are selected for Phase 5.12.4 publication. Prov-GigaPath remains
an active draft. Selection does not authorize download, redistribution,
materialization, compatibility claims or execution.
