# Phase 5.12.5 Static Schema Review Rules

## Disclaimer

静态Schema审查只评估元数据和接口条件，不代表模型已经下载、运行或通过性能验证。

Technical structure review also does not establish license permission,
clinical validity or production readiness.

## Fixed Outcomes

- `static_schema_compatible`: no substantive format or semantic transformation
  is identified from complete enough metadata.
- `static_schema_compatible_with_transformation`: the modalities and objects
  can be connected only after explicit deterministic transformations.
- `static_schema_incompatible`: a positive structural conflict exists.
- `insufficient_metadata`: required facts are missing; missing information is
  never treated as compatibility.

## Required Dimensions

Every operator review records modality, data object, format, dimensions,
resolution, preprocessing, task, output and license/access warning results.
Transformation requirements record type, parameters, known/unknown state,
lossiness, determinism, implementation availability and verification.

In this phase `implementation_verified` must always be false.

## Initial Evidence-Based Rules

Current frozen metadata states:

- CPTAC-COAD and CAMELYON17: `Histopathology (WSI)`, format/resolution unknown.
- HyperKvasir: `Endoscopy`.
- CONCH: histopathology image tiles and/or pathology text; official transforms.
- UNI: RGB histopathology image tiles; model-card transforms.

Therefore:

- WSI to CONCH/UNI requires at least tissue masking/patch extraction and the
  model's official transforms. Unknown patch size, magnification and format are
  warnings and unknown parameters. The defensible status is
  `static_schema_compatible_with_transformation`, not direct compatibility.
- HyperKvasir to CONCH/UNI has an explicit endoscopy-versus-H&E pathology input
  conflict. The defensible status is `static_schema_incompatible`.

These conclusions are computed from locked metadata fields and submitted as
structured operator reviews. Product names are not used as matching evidence.

## External Declarations

Formal declarations require exact stable dataset ID, official collection ID,
official source URL, DOI or project identifier. Fuzzy names, shared cancer
types, TCGA/CPTAC substrings and keyword overlap are candidate hints only.
Current CONCH/UNI frozen evidence does not establish an exact match to the
three published data products, so the initial formal declaration count is zero.
