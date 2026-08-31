# Phase 5.11.3B1 First Batch Governance Dry Run

Date: 2026-07-27

## Gate Result

- Main candidates: 20
- Backup candidates: 5
- Blocked records: 0
- Missing/malformed catalog locator URLs: 0
- Existing `official_source_url`: 0 of 25
- Duplicate groups in the main batch: 0

The upstream catalog stores all 982 official URLs as null. The non-null
`catalog_source_url` is therefore only a discovery locator, not proof of an
official source. External review must find and verify an official project,
repository, DOI, or institutional portal before recording
`official_source_confirmed`. If that cannot be done, the source stays
`aggregator_only`, `source_disputed`, or unreviewed.

All candidates currently have `needs_license_review`. Their common missing
fields are official source, dataset version, license, access level, sample
count, patient count, approximate size, and data format unless noted.

## Main Batch

| # | record_id | external_id | Name | Modality | Disease / organ | Catalog locator | Link | Duplicate | Why selected | Verification and risk |
|---:|---|---|---|---|---|---|---|---|---|---|
| 1 | `377e1ee7-9647-437b-b1e1-79e770cb7234` | `lxltx_ds_6f3021d8d6f69fea` | CPTAC-COAD | Histopathology WSI | Colon adenocarcinoma / colon | `https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=70227852` | HTTPS valid | none | Pathology, colorectal, CPTAC | Verify current TCIA/CPTAC collection, usage terms and NBIA access; legacy wiki redirect risk |
| 2 | `2a38c775-bb89-4309-87e1-642faa01ce1d` | `lxltx_ds_5cf652c8a5db4b49` | CRC_FFPE-CODEX_CellNeighs | Histopathology WSI | Colorectal cancer | `https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=70227790` | HTTPS valid | none | Colorectal spatial pathology | Verify TCIA analysis result, terms, component licenses and access tooling |
| 3 | `ffda3efd-6ff8-4dcb-9b1e-c8028e56053b` | `lxltx_ds_294b43ed15ecc89e` | CoNIC2022 | Histopathology patch | Colon nuclei / colon | `https://conic-challenge.grand-challenge.org/` | HTTPS valid | none | High-value colorectal benchmark | Verify challenge owner, dataset terms, registration and redistribution |
| 4 | `0ba0ad85-e873-448f-be2a-1dc226ccf28c` | `lxltx_ds_f3957ffb0ee67b10` | CoNSeP | Histopathology patch | Colorectal nuclei / colon | `https://warwick.ac.uk/fac/sci/dcs/research/tia/data/hovernet/` | HTTPS valid | none | Institutional pathology benchmark | Verify Warwick source, license text and request/download conditions |
| 5 | `f8310c03-6d64-44a1-a0d4-cb24564bcd0c` | `lxltx_ds_95edb9c784044ed8` | Colorectal Histology MNIST | Histopathology patch | Colorectal tissue / colon | `https://zenodo.org/record/53169` | HTTPS valid | none | Small standardized colorectal entry | Verify Zenodo depositor, record license, citation and open-file conditions without downloading |
| 6 | `1719e15d-b714-4533-89d0-b29f46c08547` | `lxltx_ds_47f4f3292cb8a45b` | DigestPath19 | Histopathology WSI | Signet ring cell / colon | `https://digestpath2019.grand-challenge.org/Home/` | HTTPS valid | none | Colorectal WSI challenge | Verify official challenge terms, account requirement and continuing availability |
| 7 | `ff8764d5-c73f-43c9-b9e0-8de303a68435` | `lxltx_ds_79b5bb15329ad030` | GlaS | Histopathology patch | Colorectal adenocarcinoma | `https://warwick.ac.uk/fac/cross_fac/tia/data/glascontest` | HTTPS valid | none | Canonical gland benchmark | Verify Warwick source, permitted use, citation and access |
| 8 | `51d66407-975c-4d8d-8761-ad2bfb0c4a40` | `lxltx_ds_4d162cdd463c0bf1` | Hungarian-Colorectal-Screening | Histopathology WSI | Colorectal polyps | `https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=91357370` | HTTPS valid | none | Colorectal WSI and TCIA | Verify current TCIA collection and usage terms; old wiki locator |
| 9 | `112612b1-94d8-4c4b-9308-5b5968c85e7f` | `lxltx_ds_2872b655ff6a7943` | OCELOT2023 | Histopathology WSI | Colon cancer | `https://ocelot2023.grand-challenge.org/` | HTTPS valid | none | Recent colon WSI benchmark | Verify host/organizer, challenge terms, registration and continuing access |
| 10 | `b9301532-789e-4803-89e1-19318b1bd394` | `lxltx_ds_684d8edfa7e277cb` | PAIP2021 | Histopathology WSI | Colon and prostate cancer | `https://paip2021.grand-challenge.org/` | HTTPS valid | none | Multi-organ pathology challenge | Verify challenge data policy, account/application requirements and license |
| 11 | `ef30ab88-3b4b-4657-b2d2-e3b5c8122ff6` | `lxltx_ds_514a5d5d0cbf7444` | CPTAC-BRCA | Histopathology WSI | Breast cancer | `https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=70227748` | HTTPS valid | none | CPTAC pathology comparator | Verify current TCIA/CPTAC source, terms and access |
| 12 | `88c69508-f7d6-4ade-abc1-ae56a07c41ca` | `lxltx_ds_6704731517bbbbf0` | CPTAC-HNSCC | CT, MR, WSI | Head and neck cancer | `https://www.cancerimagingarchive.net/collection/cptac-hnscc/` | HTTPS valid | none | Pathology plus multimodal imaging | Verify collection license, NBIA access and modality-level restrictions |
| 13 | `d9741bfd-2f0f-42be-bd92-f93baa86d397` | `lxltx_ds_f7ffa4cc07013c5d` | CPTAC-OV | Histopathology WSI | Ovarian cancer | `https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=70227856` | HTTPS valid | none | CPTAC WSI breadth | Verify current TCIA/CPTAC page and terms; old wiki locator |
| 14 | `c2b0de98-aa8f-4800-ab2d-2cff4a616025` | `lxltx_ds_9a5c1d098cafc175` | TIL-WSI-TCGA | Histopathology WSI | Pan-cancer | `https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=33948919` | HTTPS valid | none | TCGA-derived WSI analysis | Separate source-image terms from derived-analysis terms |
| 15 | `5c1b2324-fc02-4cfd-9256-b1c1c1542b9a` | `lxltx_ds_655256f80d39e053` | CAMELYON17 | Histopathology WSI | Breast cancer | `https://camelyon17.grand-challenge.org/Data/` | HTTPS valid | none | Widely used WSI benchmark | Verify official challenge terms, registration and redistribution |
| 16 | `88464daf-47b6-4d86-8f0a-b2af1c3bfb85` | `lxltx_ds_554e15b9a1e614e1` | MedMNIST | Multi | Retina, breast, lung | `https://medmnist.com/v1` | HTTPS valid | none | Small standardized future materialization candidate | Verify official project/repository license, component-dataset caveats and access |
| 17 | `9bc78474-b4b2-4a53-8b59-642001b10ff1` | `lxltx_ds_e9eba4876cb0776c` | 3D-IRCADb | CT | Liver tumors | `https://www.ircad.fr/research/data-sets/liver-segmentation-3d-ircadb-01/` | HTTPS valid | none | Compact CT segmentation candidate | Verify IRCAD terms, account/request requirements and redistribution |
| 18 | `0d34e89b-e4f5-4e90-8ed2-118e3e031934` | `lxltx_ds_c1781b881437e799` | 4D-Lung | CT | Lung cancer | `https://www.cancerimagingarchive.net/collection/4d-lung/` | HTTPS valid | none | Stable TCIA CT collection | Verify TCIA Data Usage Policy and NBIA access |
| 19 | `a3e947a6-c4e4-4194-a8a9-e8abeb47e5fc` | `lxltx_ds_9255573328074c30` | AIDA-E_3 | Endoscopy | Metaplasia/dysplasia; stomach and colon | `https://aidasub-chromogastro.grand-challenge.org/home/` | HTTPS valid | none | Gastric/colorectal endoscopy | Verify challenge identity, terms, registration and availability |
| 20 | `d6f56362-0d6a-4d5e-a603-8ceb1598e705` | `lxltx_ds_d9cbc9abcc1b0134` | HyperKvasir | Endoscopy | GI disease; stomach and colon | `https://datasets.simula.no/hyper-kvasir/` | HTTPS valid | none | Stable GI dataset with institutional portal | Verify Simula source, explicit license, citation and open access |

## Backup Candidates

| # | record_id | external_id | Name | Modality / focus | Catalog locator | Reason / risk |
|---:|---|---|---|---|---|---|
| B1 | `249a6513-488f-4c3a-8337-0c342b47c825` | `lxltx_ds_24a66de639286904` | CRAG | Histopathology patch / colorectal | `https://github.com/XiaoyuZHK/CRAG-Dataset_Aug_ToCOCO` | Useful colorectal patches; repository may be derivative rather than authoritative |
| B2 | `465ebccf-be8b-4d25-b2c9-207f51b1b4cf` | `lxltx_ds_ffe190642471a5a0` | CRC100K | Histopathology patch / colorectal | `https://opendatalab.org.cn/CRC100K/download` | High value but locator is an aggregator/download page; official Zenodo evidence required |
| B3 | `06e9092c-1d46-46d4-8c5f-34c8c9e70953` | `lxltx_ds_efad2eca824e6e52` | PAIP2020 | Histopathology WSI / colorectal-liver | `https://paip2020.grand-challenge.org/Home/` | Strong pathology candidate if a main challenge source is unavailable |
| B4 | `2f0963dc-5536-4c42-9c47-8d629c2f40b1` | `lxltx_ds_156ac8874539b3ce` | AMOS22 | CT/MR / abdomen | `https://amos22.grand-challenge.org/` | Multimodal benchmark; terms and Synapse access may be controlled |
| B5 | `33dbdd14-1f7e-4c30-a058-823ef57466a5` | `lxltx_ds_b220bd71ed46b715` | AOMIC-ID1000 | MR / brain | `https://openneuro.org/datasets/ds003097/versions/1.2.1` | Stable versioned official portal and likely explicit license |

## Composition

- Digital pathology: 15
- Colorectal-related: 10
- Gastric/stomach-related: 2
- CT/MR or multimodal imaging: 3 (CPTAC-HNSCC, 3D-IRCADb, 4D-Lung)
- Small standardized entry: 1 (MedMNIST)

Records can count in more than one category. The batch contains no duplicate
group members, so duplicate resolution will be performed only if external
evidence reveals a relationship or a selected backup introduces one.

## External Access Guard

- Maximum main/backup records: 25
- Maximum official pages: 3 per record
- Maximum total page requests: 75
- Maximum accepted response: 10 MiB
- Redirect limit: 5
- Downloads and data-like extensions: blocked
- Evidence root: `D:\MedTrustData\catalog-governance-evidence\phase5.11.3B1`
- Temporary/browser/log roots: `D:\MedTrustCache\catalog-governance\phase5.11.3B1`

No Review will be written until source, license, and access conclusions each
have traceable official evidence. Unknown remains unknown.
