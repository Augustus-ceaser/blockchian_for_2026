# Phase 5.11.3B2 Selected First External Products

## 1. Selection Result

The following five records were created through the authenticated operator
API as metadata-only external DataProduct drafts. The product and version IDs
are deterministic for the external record, so a same-request replay returns
the existing graph instead of creating another product.

| Dataset | External ID | Product code | DataProduct ID | Version ID |
| --- | --- | --- | --- | --- |
| CPTAC-COAD | `lxltx_ds_6f3021d8d6f69fea` | `EXT-DP-377E1EE796` | `35361559-dab4-50e8-bf46-4730a9100a2c` | `99b88ccf-d4f1-5ce3-b8d4-f4ebff0bf775` |
| CAMELYON17 | `lxltx_ds_655256f80d39e053` | `EXT-DP-5C1B2324FC` | `8d96475e-d31d-5e50-95f4-48d84573696d` | `a756c1fa-b318-52e9-a9c7-11c2c0483651` |
| Hungarian-Colorectal-Screening | `lxltx_ds_4d162cdd463c0bf1` | `EXT-DP-51D6640797` | `01f7fa55-7e97-5784-aea4-8ec356641444` | `f9841bb5-9fe0-55b4-96b4-aa5b468cfc46` |
| HyperKvasir | `lxltx_ds_d9cbc9abcc1b0134` | `EXT-DP-D6F563620D` | `ac5d9e5e-3152-558a-9415-b216ae922248` | `ab2d1015-20e0-55ce-aeef-1565707e4cc2` |
| 4D-Lung | `lxltx_ds_c1781b881437e799` | `EXT-DP-0D34E89BE4` | `c5fadea4-c280-59cd-a713-c7997e035405` | `8a02eb53-dc90-5eb2-865e-62aea1368f40` |

## 2. Immutable Linkage Evidence

Each selected version has one `DataProductExternalSourceLink` containing the
external record ID, current external version ID, source ID, four Review IDs,
the imported record digest, and a governance snapshot digest. The upstream
official URL is evidence for provenance; it is not a local storage location.

| Dataset | External version ID | Source record digest | Governance snapshot digest |
| --- | --- | --- | --- |
| CPTAC-COAD | `57f27bac-3fee-42f8-a814-48ab973e8927` | `92e1b98a0764d93aba2171766a60d79f286791dcf08f289a74aba010d971c847` | `sha256:0a3197758b8731e6706c1630f98327822071bc7cf36b00f13b68bd9b1256ffdd` |
| CAMELYON17 | `a93ef356-c4f6-4a06-9826-37f93171f479` | `062f0c45aa53d8199e014c03e3e27b2c18be05f6f83ab5ae5ce554caa29e8666` | `sha256:6631d48746b1063c00b59236b858a077064e82301184bfbad190739714bf8297` |
| Hungarian-Colorectal-Screening | `0c4a0275-72d6-4019-8655-b61c9633044f` | `21a3ac25fb60f24565ed9d95ed8b7b13cfcefdea9997b293590a119ab446202f` | `sha256:81250f796b944907cf9780a3d897b35f664a6270c50a2248b6b10bea1764e79a` |
| HyperKvasir | `1e753846-4335-4769-9c86-4a2ed133e7fc` | `d6b0d6d343662a3605978f3fab28da5061aab381799858c9172604ea16955768` | `sha256:a280e082e51da768934371f4ce5b18502377eaac52548447a0be018ee21eb73d` |
| 4D-Lung | `55241cfd-0306-43d3-b2dc-d3605abc4a1e` | `1726a23f24ec5eafcd2f7abf6d9c394dd1d0f16145b4c4378cc5d81fe87cfcb7` | `sha256:d4632f951b8ba82f322c9931ab04cf6137e8358ca56eec7dec4e81d1bcd344a5` |

## 3. Explicit Product Semantics

All five selected drafts have the same guarded semantics:

- `DataProduct.lifecycle_status=draft`;
- `DataProductVersion.status=draft`;
- `default_use_mode=external_metadata_catalog`;
- `materialization_status=metadata_only`;
- `data_holder_status=external_upstream`;
- `redistribution_status=allowed` as the current Review conclusion, not as a
  statement that MedTrust Space owns the source;
- `execution_readiness=not_ready`;
- no source payload is copied into the product resource;
- raw-data download, data hosting, model download, execution, and internet
  use are denied by the draft policy.

The local curator organization is recorded as the product provider required by
the existing product schema. The source link separately records that the
upstream holder remains external. The reviewed evidence did not identify a
separate rights-holder field for these five records, so that field remains
null rather than being guessed.

## 4. Unselected Test Artifact

One permission-boundary test accidentally created a draft for `CPTAC-BRCA`,
which was eligible at the time of the negative test. It was discarded through
the formal operator discard API, not by SQL or ORM manipulation:

- product code: `EXT-DP-EF30AB883B`;
- version ID: `b855efdd-6fd9-5041-a495-fc47cf4147a1`;
- final lifecycle: `archived`;
- source link and audit evidence retained;
- not part of the five selected active drafts.

This retained audit artifact means B2 has six source-link rows created during
the work, of which five are active drafts and one is an archived cleanup
record. The report does not mislabel the physical row count as exactly five.
