from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalDatasetRecord,
    ExternalDatasetVersion,
    ExternalModelRecord,
    ExternalModelVersion,
)
from app.modules.external_catalog.orthopedic_seed import (
    DATASET_SOURCE_CODE,
    MODEL_SOURCE_CODE,
    RESOURCE_ROOT,
    SOURCE_CATALOG,
)

LOCAL_MATERIALIZATION_RESOURCE = "orthopedic_local_materialization_v1.json"
WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ASSET_ROOT = WORKSPACE_ROOT / ".runtime" / "orthopedic-assets"
FRACATLAS_EXTERNAL_ID = "medtrust_orthopedic_dataset_fracatlas_v6"
FRACATLAS_MODEL_EXTERNAL_ID = "medtrust_model_fracatlas_mnv3_target_v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_DATASET_FIELDS = (
    "canonical_name",
    "display_name_cn",
    "display_name_en",
    "source_catalog",
    "official_source_name",
    "official_source_url",
    "catalog_source_url",
    "modalities",
    "disease_areas",
    "organs",
    "task_types",
    "species",
    "sample_count",
    "patient_count",
    "file_count",
    "approximate_size_bytes",
    "data_formats",
    "license_name",
    "license_url",
    "license_status",
    "access_level",
    "registration_required",
    "dataset_version",
    "link_status",
    "quality_flags",
)
_MODEL_FIELDS = (
    "canonical_name",
    "display_name_cn",
    "display_name_en",
    "source_catalog",
    "paper_title",
    "paper_doi",
    "paper_url",
    "code_repository_url",
    "model_card_url",
    "upstream_provider",
    "framework",
    "library_name",
    "architecture",
    "pipeline_tag",
    "input_schema",
    "output_schema",
    "preprocessing_summary",
    "training_dataset_references",
    "evaluation_dataset_references",
    "metrics_summary",
    "license_name",
    "license_url",
    "license_status",
    "access_status",
    "weights_status",
    "weights_files",
    "estimated_weights_size_bytes",
    "revision",
    "commit_sha",
    "release_tag",
    "gated",
    "clinical_use_status",
    "intended_use_summary",
    "limitations_summary",
    "execution_status",
    "quality_flags",
)


class OrthopedicMaterializationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedOrthopedicAssets:
    manifest_id: str
    manifest_version: str
    manifest_digest: str
    dataset: dict[str, Any]
    model: dict[str, Any]
    model_manifest: dict[str, Any]
    evaluation: dict[str, Any]


@dataclass(frozen=True)
class OrthopedicMaterializationResult:
    dataset_record_id: UUID
    dataset_version_id: UUID
    dataset_outcome: str
    model_record_id: UUID
    model_version_id: UUID
    model_outcome: str
    manifest_digest: str
    dataset_archive_sha256: str
    model_weights_sha256: str


def _canonical_digest(value: Any) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OrthopedicMaterializationError(f"{label} is unavailable") from exc
    except json.JSONDecodeError as exc:
        raise OrthopedicMaterializationError(f"{label} is invalid JSON") from exc
    if not isinstance(document, dict):
        raise OrthopedicMaterializationError(f"{label} must be a JSON object")
    return document


def _relative_parts(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise OrthopedicMaterializationError(f"{label} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OrthopedicMaterializationError(f"{label} must be a safe relative path")
    return path.parts


def _resolve_asset(root: Path, relative: Any, label: str) -> Path:
    root = root.resolve(strict=True)
    candidate = root.joinpath(*_relative_parts(relative, label)).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise OrthopedicMaterializationError(f"{label} escapes the asset root") from exc
    return candidate


def _expected_digest(value: Any, pattern: re.Pattern[str], label: str) -> str:
    normalized = str(value or "").lower()
    if pattern.fullmatch(normalized) is None:
        raise OrthopedicMaterializationError(f"{label} is invalid")
    return normalized


def _verify_file(
    path: Path,
    *,
    expected_sha256: Any,
    label: str,
    expected_size: Any | None = None,
    expected_md5: Any | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise OrthopedicMaterializationError(f"{label} is not a file")
    expected_sha256 = _expected_digest(expected_sha256, _SHA256_RE, f"{label} sha256")
    actual_sha256 = _file_digest(path)
    if actual_sha256 != expected_sha256:
        raise OrthopedicMaterializationError(f"{label} sha256 mismatch")
    size_bytes = path.stat().st_size
    if expected_size is not None and size_bytes != expected_size:
        raise OrthopedicMaterializationError(f"{label} size mismatch")
    result: dict[str, Any] = {"sha256": actual_sha256, "size_bytes": size_bytes}
    if expected_md5 is not None:
        expected_md5 = _expected_digest(expected_md5, _MD5_RE, f"{label} md5")
        actual_md5 = _file_digest(path, "md5")
        if actual_md5 != expected_md5:
            raise OrthopedicMaterializationError(f"{label} md5 mismatch")
        result["md5"] = actual_md5
    return result


def load_local_materialization_manifest(
    *, resource_root: Path = RESOURCE_ROOT
) -> dict[str, Any]:
    document = _json_object(
        resource_root / LOCAL_MATERIALIZATION_RESOURCE,
        "orthopedic local materialization manifest",
    )
    if document.get("schema_version") != "medtrust.orthopedic-local-materialization/v1":
        raise OrthopedicMaterializationError("local materialization schema is unsupported")
    if document.get("dataset", {}).get("external_id") != FRACATLAS_EXTERNAL_ID:
        raise OrthopedicMaterializationError("local dataset scope is invalid")
    if document.get("model", {}).get("external_model_id") != FRACATLAS_MODEL_EXTERNAL_ID:
        raise OrthopedicMaterializationError("local model scope is invalid")
    if (
        document.get("model", {}).get("linked_dataset_external_id")
        != FRACATLAS_EXTERNAL_ID
    ):
        raise OrthopedicMaterializationError("local model dataset link is invalid")
    if not isinstance(document.get("manifest_id"), str) or not isinstance(
        document.get("manifest_version"), str
    ):
        raise OrthopedicMaterializationError("local materialization identity is invalid")

    dataset = document["dataset"]
    model = document["model"]
    archive = dataset.get("archive")
    extracted = dataset.get("extracted")
    model_ref = model.get("manifest")
    if not all(isinstance(item, dict) for item in (archive, extracted, model_ref)):
        raise OrthopedicMaterializationError("local materialization entries are invalid")
    _relative_parts(archive.get("relative_path"), "dataset archive path")
    _relative_parts(extracted.get("relative_path"), "dataset extracted path")
    _relative_parts(model_ref.get("relative_path"), "model manifest path")
    _expected_digest(archive.get("sha256"), _SHA256_RE, "dataset archive sha256")
    _expected_digest(archive.get("md5"), _MD5_RE, "dataset archive md5")
    _expected_digest(model_ref.get("sha256"), _SHA256_RE, "model manifest sha256")
    return deepcopy(document)


def verify_local_orthopedic_assets(
    *,
    asset_root: Path = DEFAULT_ASSET_ROOT,
    resource_root: Path = RESOURCE_ROOT,
) -> VerifiedOrthopedicAssets:
    overlay = load_local_materialization_manifest(resource_root=resource_root)
    dataset = overlay["dataset"]
    archive_spec = dataset["archive"]
    archive_path = _resolve_asset(
        asset_root, archive_spec["relative_path"], "dataset archive path"
    )
    archive = _verify_file(
        archive_path,
        expected_sha256=archive_spec["sha256"],
        expected_md5=archive_spec["md5"],
        expected_size=archive_spec.get("size_bytes"),
        label="FracAtlas archive",
    )

    extracted_spec = dataset["extracted"]
    extracted_root = _resolve_asset(
        asset_root, extracted_spec["relative_path"], "dataset extracted path"
    )
    if not extracted_root.is_dir():
        raise OrthopedicMaterializationError("FracAtlas extracted root is not a directory")
    index_spec = extracted_spec.get("dataset_index")
    if not isinstance(index_spec, dict):
        raise OrthopedicMaterializationError("FracAtlas dataset index entry is invalid")
    index_path = _resolve_asset(
        extracted_root, index_spec.get("relative_path"), "dataset index path"
    )
    index = _verify_file(
        index_path,
        expected_sha256=index_spec.get("sha256"),
        label="FracAtlas dataset index",
    )
    class_counts: dict[str, int] = {}
    class_directories = extracted_spec.get("class_directories")
    if not isinstance(class_directories, dict) or not class_directories:
        raise OrthopedicMaterializationError("FracAtlas class directory manifest is invalid")
    for class_name, entry in class_directories.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("file_count"), int):
            raise OrthopedicMaterializationError("FracAtlas class entry is invalid")
        class_path = _resolve_asset(
            extracted_root,
            entry.get("relative_path"),
            f"FracAtlas {class_name} class path",
        )
        if not class_path.is_dir():
            raise OrthopedicMaterializationError(
                f"FracAtlas {class_name} class path is not a directory"
            )
        actual_count = sum(1 for child in class_path.iterdir() if child.is_file())
        if actual_count != entry["file_count"]:
            raise OrthopedicMaterializationError(
                f"FracAtlas {class_name} file count mismatch"
            )
        class_counts[str(class_name)] = actual_count
    image_count = sum(class_counts.values())
    if image_count != extracted_spec.get("image_file_count"):
        raise OrthopedicMaterializationError("FracAtlas image file count mismatch")

    model = overlay["model"]
    model_ref = model["manifest"]
    manifest_path = _resolve_asset(
        asset_root, model_ref["relative_path"], "model manifest path"
    )
    manifest_file = _verify_file(
        manifest_path,
        expected_sha256=model_ref["sha256"],
        label="FracAtlas model manifest",
    )
    model_manifest = _json_object(manifest_path, "FracAtlas model manifest")
    if (
        model_manifest.get("schema_version") != "medtrust.fixed-model-manifest/v1"
        or model_manifest.get("model_id")
        != "medtrust-fracatlas-mobilenet-v3-small-v1"
        or model_manifest.get("task") != "fracture_presence_image_classification"
    ):
        raise OrthopedicMaterializationError("FracAtlas model manifest contract is invalid")
    readiness = model_manifest.get("readiness")
    if not isinstance(readiness, dict) or any(
        readiness.get(key) is not False
        for key in (
            "application_eligible",
            "clinical_use",
            "compute_eligible",
            "executor_registered",
            "hard_isolation",
        )
    ):
        raise OrthopedicMaterializationError("FracAtlas model readiness boundary is unsafe")

    weights_spec = model_manifest.get("weights")
    evidence_spec = model_manifest.get("evidence")
    if not isinstance(weights_spec, dict) or not isinstance(evidence_spec, dict):
        raise OrthopedicMaterializationError("FracAtlas model asset references are invalid")
    weights_path = _resolve_asset(
        manifest_path.parent, weights_spec.get("path"), "model weights path"
    )
    weights = _verify_file(
        weights_path,
        expected_sha256=weights_spec.get("sha256"),
        expected_size=weights_spec.get("bytes"),
        label="FracAtlas model weights",
    )
    evaluation_path = _resolve_asset(
        manifest_path.parent,
        evidence_spec.get("evaluation_path"),
        "model evaluation path",
    )
    evaluation_file = _verify_file(
        evaluation_path,
        expected_sha256=evidence_spec.get("evaluation_sha256"),
        label="FracAtlas model evaluation",
    )
    split_path = _resolve_asset(
        manifest_path.parent,
        evidence_spec.get("split_inventory_path"),
        "model split inventory path",
    )
    split_file = _verify_file(
        split_path,
        expected_sha256=evidence_spec.get("split_inventory_sha256"),
        label="FracAtlas split inventory",
    )
    evaluation = _json_object(evaluation_path, "FracAtlas model evaluation")
    split_inventory = _json_object(split_path, "FracAtlas split inventory")
    if (
        evaluation.get("schema_version") != "medtrust.model-evaluation/v1"
        or evaluation.get("model_id") != model_manifest["model_id"]
        or evaluation.get("evidence_scope") != "image_level_technical_validation"
        or not isinstance(evaluation.get("test"), dict)
        or evaluation["test"].get("metrics_valid") is not True
    ):
        raise OrthopedicMaterializationError("FracAtlas evaluation evidence is invalid")
    if (
        split_inventory.get("schema_version")
        != "medtrust.dataset-split-inventory/v1"
        or split_inventory.get("dataset_version") != "v6"
        or split_inventory.get("split_scope") != "image_level"
    ):
        raise OrthopedicMaterializationError("FracAtlas split evidence is invalid")
    splits = split_inventory.get("splits")
    if not isinstance(splits, dict) or sum(
        int(entry.get("count", -1))
        for entry in splits.values()
        if isinstance(entry, dict)
    ) != image_count:
        raise OrthopedicMaterializationError("FracAtlas split count is invalid")

    verified_dataset = {
        "asset_id": dataset["asset_id"],
        "archive": {**archive, "asset_id": archive_spec["asset_id"]},
        "dataset_index": {**index, "asset_id": index_spec["asset_id"]},
        "image_file_count": image_count,
        "class_counts": class_counts,
    }
    verified_model = {
        "asset_id": model["asset_id"],
        "manifest": {**manifest_file, "asset_id": model_ref["asset_id"]},
        "weights": {**weights, "asset_id": f"{model['asset_id']}.weights"},
        "evaluation": {
            **evaluation_file,
            "asset_id": f"{model['asset_id']}.evaluation",
        },
        "split_inventory": {
            **split_file,
            "asset_id": f"{model['asset_id']}.split-inventory",
        },
    }
    return VerifiedOrthopedicAssets(
        manifest_id=overlay["manifest_id"],
        manifest_version=overlay["manifest_version"],
        manifest_digest=_canonical_digest(overlay),
        dataset=verified_dataset,
        model=verified_model,
        model_manifest=model_manifest,
        evaluation=evaluation,
    )


def _append_flags(existing: Any, *flags: str) -> list[str]:
    values = [str(item) for item in existing] if isinstance(existing, list) else []
    for flag in flags:
        if flag not in values:
            values.append(flag)
    return values


def _manifest_evidence(verified: VerifiedOrthopedicAssets) -> dict[str, Any]:
    return {
        "manifest_id": verified.manifest_id,
        "manifest_version": verified.manifest_version,
        "manifest_sha256": verified.manifest_digest,
    }


def _dataset_payload(
    base: dict[str, Any], verified: VerifiedOrthopedicAssets
) -> dict[str, Any]:
    payload = deepcopy(base)
    profile = deepcopy(payload.get("medtrust_profile") or {})
    profile.update(
        catalog_stage="static_candidate",
        materialization_status="materialized",
        asset_residency="local_verified",
        asset_manifest_status="verified",
        execution_readiness="validation_ready",
        executor_registered=False,
        platform_validation="asset_integrity_verified",
        application_eligible=False,
        can_execute=False,
    )
    profile["local_asset_manifest"] = {
        **_manifest_evidence(verified),
        **verified.dataset,
    }
    payload["medtrust_profile"] = profile
    payload["file_count"] = verified.dataset["image_file_count"]
    payload["approximate_size_bytes"] = verified.dataset["archive"]["size_bytes"]
    payload["quality_flags"] = _append_flags(
        payload.get("quality_flags"),
        "local_asset_integrity_verified",
        "patient_level_split_unverifiable",
        "non_clinical_static_candidate",
    )
    return payload


def _metric_summary(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = evaluation["test"]
    return [
        {"split": "test", "name": name, "value": metrics[name]}
        for name in (
            "accuracy",
            "balanced_accuracy",
            "precision",
            "sensitivity",
            "specificity",
            "f1",
        )
        if isinstance(metrics.get(name), (int, float))
    ]


def _model_payload(verified: VerifiedOrthopedicAssets) -> dict[str, Any]:
    manifest = verified.model_manifest
    evaluation = verified.evaluation
    weights = verified.model["weights"]
    limitations = evaluation.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
    return {
        "external_model_id": FRACATLAS_MODEL_EXTERNAL_ID,
        "canonical_name": "MedTrust FracAtlas MobileNetV3-Small v1",
        "display_name_cn": "MedTrust FracAtlas 骨折识别 MobileNetV3-Small v1",
        "display_name_en": "MedTrust FracAtlas Fracture MobileNetV3-Small v1",
        "source_catalog": SOURCE_CATALOG,
        "model_category": ["target_task_weights", "binary_classifier"],
        "modalities": ["x_ray"],
        "task_types": ["image_classification"],
        "disease_areas": ["fracture"],
        "organs": ["musculoskeletal_system"],
        "species": ["human"],
        "paper_title": None,
        "paper_doi": None,
        "paper_url": None,
        "code_repository_url": None,
        "model_card_url": None,
        "upstream_provider": "MedTrust Space engineering validation",
        "framework": "pytorch",
        "library_name": "torchvision",
        "architecture": manifest["architecture"],
        "pipeline_tag": "image-classification",
        "input_schema": "FracAtlas v6 musculoskeletal plain radiograph converted to RGB 224x224",
        "output_schema": "binary non_fractured versus fractured logits",
        "preprocessing_summary": manifest["input"]["transform"],
        "training_dataset_references": ["FracAtlas v6 deterministic image-level train split"],
        "evaluation_dataset_references": [
            "FracAtlas v6 deterministic image-level validation and test splits"
        ],
        "metrics_summary": _metric_summary(evaluation),
        "license_name": "Mixed upstream terms documented in the fixed manifest",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "license_status": "custom_terms",
        "access_status": "local_fixed_asset",
        "weights_status": "local_generated",
        "weights_files": [
            {
                "asset_id": weights["asset_id"],
                "name": Path(manifest["weights"]["path"]).name,
                "size_bytes": weights["size_bytes"],
                "sha256": weights["sha256"],
                "local_status": "verified",
            }
        ],
        "estimated_weights_size_bytes": weights["size_bytes"],
        "revision": manifest["version"],
        "commit_sha": None,
        "release_tag": None,
        "gated": False,
        "clinical_use_status": "non_clinical",
        "intended_use_summary": (
            "Static target-task candidate for image-level FracAtlas fracture-presence "
            "technical validation."
        ),
        "limitations_summary": " ".join(str(item) for item in limitations),
        "execution_status": "not_materialized",
        "quality_flags": [
            "local_asset_integrity_verified",
            "image_level_split_only",
            "patient_level_leakage_cannot_be_excluded",
            "executor_not_registered",
            "application_ineligible",
            "non_clinical",
        ],
        "source_evidence": [
            {
                "type": "local_generated_fixed_manifest",
                "asset_id": verified.model["manifest"]["asset_id"],
                "sha256": verified.model["manifest"]["sha256"],
                "evidence_scope": "image_level_technical_validation",
            },
            {
                "type": "official_dataset",
                "source_url": "https://doi.org/10.6084/m9.figshare.22363012.v6",
            },
        ],
        "medtrust_profile": {
            "schema_version": "medtrust.orthopedic-profile/v1",
            "catalog_stage": "static_candidate",
            "asset_kind": "target_task_weights",
            "target_task_weights": True,
            "materialization_status": "materialized",
            "asset_residency": "local_verified",
            "asset_manifest_status": "verified",
            "execution_readiness": "validation_ready",
            "executor_registered": False,
            "platform_validation": "image_level_technical_validation",
            "application_eligible": False,
            "can_execute": False,
            "condition_codes": ["fracture"],
            "anatomical_sites": ["musculoskeletal_system"],
            "modalities": ["x_ray"],
            "view_protocol": ["mixed_plain_radiograph_views"],
            "task_type": "image_classification",
            "target_definition": "fracture_presence_per_image",
            "operation_modes": ["fixed_validation_candidate"],
            "input_schema": manifest["input"],
            "output_schema": {"classes": manifest["classes"]},
            "evidence_scope": evaluation["evidence_scope"],
            "local_asset_manifest": {
                **_manifest_evidence(verified),
                **verified.model,
            },
            "linked_dataset_external_id": FRACATLAS_EXTERNAL_ID,
            "license": manifest["license_notes"],
            "runtime_code_policy": "fixed_executor_registration_required",
            "non_clinical": True,
        },
    }


def _assert_db_safe(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    if any(token in serialized for token in ("C:\\\\", "D:\\\\", "/home/", "/Users/")):
        raise OrthopedicMaterializationError("normalized payload contains a local path")


async def _source(
    session: AsyncSession, *, space_id: UUID, source_code: str
) -> ExternalCatalogSource:
    source = await session.scalar(
        select(ExternalCatalogSource).where(
            ExternalCatalogSource.source_code == source_code
        )
    )
    if source is None or source.space_id != space_id:
        raise OrthopedicMaterializationError(
            f"orthopedic catalog source {source_code} must be seeded first"
        )
    return source


async def _upsert_dataset(
    session: AsyncSession,
    *,
    source: ExternalCatalogSource,
    payload: dict[str, Any],
    catalog_version: str,
    observed_at: datetime,
    manifest_ref: dict[str, Any],
) -> tuple[ExternalDatasetRecord, ExternalDatasetVersion, str]:
    row = await session.scalar(
        select(ExternalDatasetRecord).where(
            ExternalDatasetRecord.source_id == source.id,
            ExternalDatasetRecord.external_id == FRACATLAS_EXTERNAL_ID,
        )
    )
    if row is None or row.current_version_id is None:
        raise OrthopedicMaterializationError("FracAtlas catalog record must be seeded first")
    digest = _canonical_digest(payload)
    versions = list(
        (
            await session.scalars(
                select(ExternalDatasetVersion).where(
                    ExternalDatasetVersion.record_id == row.id
                )
            )
        ).all()
    )
    target = next((version for version in versions if version.record_digest == digest), None)
    if row.raw_record_digest == digest and target is not None and target.is_current:
        return row, target, "unchanged"
    for version in versions:
        version.is_current = False
    outcome = "updated"
    if target is None:
        target = ExternalDatasetVersion(
            record_id=row.id,
            catalog_version=catalog_version,
            record_digest=digest,
            normalized_payload=payload,
            source_payload={"local_materialization_manifest": manifest_ref},
            observed_at=observed_at,
            is_current=True,
        )
        session.add(target)
        await session.flush()
    else:
        target.is_current = True
    for field in _DATASET_FIELDS:
        setattr(row, field, payload.get(field))
    row.raw_record_digest = digest
    row.last_seen_at = observed_at
    row.status = "active"
    row.current_version_id = target.id
    return row, target, outcome


async def _upsert_model(
    session: AsyncSession,
    *,
    source: ExternalCatalogSource,
    payload: dict[str, Any],
    catalog_version: str,
    observed_at: datetime,
) -> tuple[ExternalModelRecord, ExternalModelVersion, str]:
    digest = _canonical_digest(payload)
    row = await session.scalar(
        select(ExternalModelRecord).where(
            ExternalModelRecord.source_id == source.id,
            ExternalModelRecord.external_model_id == FRACATLAS_MODEL_EXTERNAL_ID,
        )
    )
    if row is None:
        values = {field: payload.get(field) for field in _MODEL_FIELDS}
        row = ExternalModelRecord(
            source_id=source.id,
            external_model_id=FRACATLAS_MODEL_EXTERNAL_ID,
            model_categories=payload["model_category"],
            modalities=payload["modalities"],
            task_types=payload["task_types"],
            disease_areas=payload["disease_areas"],
            organs=payload["organs"],
            species=payload["species"],
            raw_record_digest=digest,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            status="active",
            **values,
        )
        session.add(row)
        await session.flush()
        version = ExternalModelVersion(
            record_id=row.id,
            catalog_version=catalog_version,
            record_digest=digest,
            normalized_payload=payload,
            source_evidence=payload["source_evidence"],
            observed_at=observed_at,
            is_current=True,
        )
        session.add(version)
        await session.flush()
        row.current_version_id = version.id
        return row, version, "inserted"

    versions = list(
        (
            await session.scalars(
                select(ExternalModelVersion).where(
                    ExternalModelVersion.record_id == row.id
                )
            )
        ).all()
    )
    target = next((version for version in versions if version.record_digest == digest), None)
    if row.raw_record_digest == digest and target is not None and target.is_current:
        return row, target, "unchanged"
    for version in versions:
        version.is_current = False
    if target is None:
        target = ExternalModelVersion(
            record_id=row.id,
            catalog_version=catalog_version,
            record_digest=digest,
            normalized_payload=payload,
            source_evidence=payload["source_evidence"],
            observed_at=observed_at,
            is_current=True,
        )
        session.add(target)
        await session.flush()
    else:
        target.is_current = True
    for field in _MODEL_FIELDS:
        setattr(row, field, payload.get(field))
    row.model_categories = payload["model_category"]
    row.modalities = payload["modalities"]
    row.task_types = payload["task_types"]
    row.disease_areas = payload["disease_areas"]
    row.organs = payload["organs"]
    row.species = payload["species"]
    row.raw_record_digest = digest
    row.last_seen_at = observed_at
    row.status = "active"
    row.current_version_id = target.id
    return row, target, "updated"


async def materialize_local_orthopedic_assets(
    session: AsyncSession,
    *,
    space_id: UUID,
    asset_root: Path = DEFAULT_ASSET_ROOT,
    resource_root: Path = RESOURCE_ROOT,
) -> OrthopedicMaterializationResult:
    verified = verify_local_orthopedic_assets(
        asset_root=asset_root,
        resource_root=resource_root,
    )
    data_source = await _source(
        session, space_id=space_id, source_code=DATASET_SOURCE_CODE
    )
    model_source = await _source(
        session, space_id=space_id, source_code=MODEL_SOURCE_CODE
    )
    dataset_row = await session.scalar(
        select(ExternalDatasetRecord).where(
            ExternalDatasetRecord.source_id == data_source.id,
            ExternalDatasetRecord.external_id == FRACATLAS_EXTERNAL_ID,
        )
    )
    if dataset_row is None or dataset_row.current_version_id is None:
        raise OrthopedicMaterializationError("FracAtlas catalog record must be seeded first")
    dataset_version = await session.get(
        ExternalDatasetVersion, dataset_row.current_version_id
    )
    if dataset_version is None:
        raise OrthopedicMaterializationError("FracAtlas current version is unavailable")

    dataset_payload = _dataset_payload(dataset_version.normalized_payload, verified)
    model_payload = _model_payload(verified)
    _assert_db_safe(dataset_payload)
    _assert_db_safe(model_payload)
    observed_at = datetime.now(timezone.utc)
    manifest_ref = _manifest_evidence(verified)
    dataset_row, dataset_version, dataset_outcome = await _upsert_dataset(
        session,
        source=data_source,
        payload=dataset_payload,
        catalog_version=verified.manifest_version,
        observed_at=observed_at,
        manifest_ref=manifest_ref,
    )
    model_row, model_version, model_outcome = await _upsert_model(
        session,
        source=model_source,
        payload=model_payload,
        catalog_version=verified.manifest_version,
        observed_at=observed_at,
    )
    return OrthopedicMaterializationResult(
        dataset_record_id=dataset_row.id,
        dataset_version_id=dataset_version.id,
        dataset_outcome=dataset_outcome,
        model_record_id=model_row.id,
        model_version_id=model_version.id,
        model_outcome=model_outcome,
        manifest_digest=verified.manifest_digest,
        dataset_archive_sha256=verified.dataset["archive"]["sha256"],
        model_weights_sha256=verified.model["weights"]["sha256"],
    )
