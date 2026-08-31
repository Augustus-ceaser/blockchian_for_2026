# Phase 5.11.4 Publication Candidate Ranking

## Method

This ranking uses only the five active B2 drafts and their frozen B1 Review
evidence. No upstream page or dataset file was opened. All five have governance
score 55, confirmed source Reviews, permissive license Reviews, open-access
Reviews, approved productization Reviews, and immutable source/governance
digests. Missing imported catalog fields remain unknown.

The tie-breakers are evidence clarity, first-catalog domain coverage, medical
AI demonstration value, and future materialization scope. A famous dataset is
not promoted merely because it is well known.

## Ranking

| Rank | Product / external ID | Modality and disease | Reviewed source, license, access | Metadata and scale | Demonstration value / risk | Decision |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | CPTAC-COAD / `lxltx_ds_6f3021d8d6f69fea` | WSI; colon adenocarcinoma | TCIA collection; CC BY 4.0; public TCIA retrieval with citation | completeness 55; counts, size and formats unknown | Strong colorectal digital-pathology example; clear current evidence; future WSI materialization may be large | `publish` |
| 2 | CAMELYON17 / `lxltx_ds_655256f80d39e053` | WSI; breast cancer | official challenge Data page; CC0 stated on reviewed page; open access | completeness 55; counts, size and formats unknown | Recognizable pathology benchmark with distinct disease coverage; source page must be revalidated before any materialization | `publish` |
| 3 | HyperKvasir / `lxltx_ds_d9cbc9abcc1b0134` | endoscopy; gastrointestinal disease | Scientific Data descriptor; CC BY 4.0; open access | completeness 55; counts, size and formats unknown | Adds non-WSI imaging and GI coverage; publication remains metadata-only | `publish` |
| 4 | Hungarian-Colorectal-Screening / `lxltx_ds_4d162cdd463c0bf1` | WSI; colorectal polyps | TCIA collection; CC BY 4.0; public tooling and citation | completeness 55; counts, size and formats unknown | Relevant to colorectal work, but overlaps the first CPTAC-COAD product and may require substantial WSI transfer | `keep_draft` |
| 5 | 4D-Lung / `lxltx_ds_c1781b881437e799` | CT; lung cancer | TCIA collection; CC BY 3.0; public tooling and attribution | completeness 55; counts, size and formats unknown | Useful multimodal expansion, but RT/4D imaging materialization and compatibility need a separate design | `keep_draft` |

## Selected Products

The recommended first publication batch is:

1. CPTAC-COAD
2. CAMELYON17
3. HyperKvasir

They give colorectal WSI, breast WSI, and gastrointestinal endoscopy coverage
without claiming that any dataset is locally held. The two remaining active
drafts are neither rejected nor downgraded; they stay `draft`.

## Required Content Review

For every selected product, the public detail must show:

- English source name and current product name;
- official reviewed source URL from the frozen source Review;
- external ID and catalog version;
- source-record and governance snapshot digests;
- upstream rights holder as unknown/external unless evidenced;
- curator organization as the metadata steward, not rights holder;
- reviewed license name/URL and use/redistribution conclusions;
- reviewed access URL and conditions;
- modality, disease and organs;
- unknown counts, size and formats as unknown;
- `metadata_only`, `external_upstream`, `not_materialized`, and `not_ready`.

