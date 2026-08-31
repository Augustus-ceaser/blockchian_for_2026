from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
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

RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "resources" / "public_alpha"
CATALOG_RESOURCE = "orthopedic_catalog_v1.json"

DATASET_SOURCE_CODE = "medtrust-orthopedic-curated-datasets-v1"
MODEL_SOURCE_CODE = "medtrust-orthopedic-curated-models-v1"
SOURCE_CATALOG = "medtrust-orthopedic-curated-candidates"

EXPECTED_DATASET_IDS = {
    "medtrust_orthopedic_dataset_fracatlas_v6",
    "medtrust_orthopedic_dataset_pxr150_v1",
    "medtrust_orthopedic_dataset_digital_knee_v1",
    "medtrust_orthopedic_dataset_tcia_osteosarcoma_v1",
}
EXPECTED_TARGET_WEIGHT_IDS = {
    "medtrust_model_oai_knee_localizer_resnet18",
    "medtrust_model_oai_kl_resnet34_baseline",
    "medtrust_model_oai_kl_resnet34_cbam",
}
EXPECTED_TEMPLATE_IDS = {
    "medtrust_model_torchvision_mobilenet_v3_small_template",
    "medtrust_model_torchvision_resnet18_template",
    "medtrust_model_torchvision_ssdlite320_mobilenet_v3_template",
    "medtrust_model_torchvision_lraspp_mobilenet_v3_template",
}

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


class OrthopedicCatalogSeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrthopedicCatalogSeedResult:
    data_source_id: UUID
    model_source_id: UUID
    datasets_inserted: int
    datasets_updated: int
    datasets_unchanged: int
    models_inserted: int
    models_updated: int
    models_unchanged: int
    fracatlas_materialization_status: str
    catalog_digest: str


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _validate_catalog(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise OrthopedicCatalogSeedError("orthopedic catalog seed must be a JSON object")
    if document.get("schema_version") != "medtrust.orthopedic-catalog-seed/v1":
        raise OrthopedicCatalogSeedError("orthopedic catalog seed schema is unsupported")
    if document.get("asset_payload_included") is not False:
        raise OrthopedicCatalogSeedError("orthopedic catalog seed must remain metadata-only")
    datasets = document.get("datasets")
    models = document.get("models")
    if not isinstance(datasets, list) or not isinstance(models, list):
        raise OrthopedicCatalogSeedError("orthopedic catalog seed lists are invalid")

    dataset_ids = {
        item.get("external_id") for item in datasets if isinstance(item, dict)
    }
    model_ids = {
        item.get("external_model_id") for item in models if isinstance(item, dict)
    }
    if dataset_ids != EXPECTED_DATASET_IDS or len(datasets) != len(dataset_ids):
        raise OrthopedicCatalogSeedError("orthopedic dataset seed scope is invalid")
    if (
        model_ids != EXPECTED_TARGET_WEIGHT_IDS | EXPECTED_TEMPLATE_IDS
        or len(models) != len(model_ids)
    ):
        raise OrthopedicCatalogSeedError("orthopedic model seed scope is invalid")

    serialized = json.dumps(document, ensure_ascii=True, sort_keys=True)
    if any(token in serialized for token in ("C:\\\\", "D:\\\\", "/home/", "/Users/")):
        raise OrthopedicCatalogSeedError("orthopedic catalog seed contains a local path")

    for item in datasets:
        profile = item.get("medtrust_profile")
        if item.get("source_catalog") != SOURCE_CATALOG or not isinstance(profile, dict):
            raise OrthopedicCatalogSeedError("orthopedic dataset metadata is incomplete")
        if (
            profile.get("materialization_status") != "not_materialized"
            or profile.get("execution_readiness") != "not_ready"
            or profile.get("executor_registered") is not False
            or profile.get("platform_validation") != "not_validated"
            or profile.get("application_eligible") is not False
            or profile.get("can_execute") is not False
        ):
            raise OrthopedicCatalogSeedError("orthopedic dataset boundary is unsafe")

    for item in models:
        profile = item.get("medtrust_profile")
        if (
            item.get("source_catalog") != SOURCE_CATALOG
            or item.get("execution_status") != "not_materialized"
            or not isinstance(item.get("source_evidence"), list)
            or not item["source_evidence"]
            or not isinstance(profile, dict)
            or profile.get("materialization_status") != "not_materialized"
            or profile.get("execution_readiness") != "not_ready"
            or profile.get("executor_registered") is not False
            or profile.get("platform_validation") != "not_validated"
            or profile.get("can_execute") is not False
        ):
            raise OrthopedicCatalogSeedError("orthopedic model boundary is unsafe")
        expected_kind = (
            "target_task_weights"
            if item["external_model_id"] in EXPECTED_TARGET_WEIGHT_IDS
            else "algorithm_template"
        )
        if (
            profile.get("asset_kind") != expected_kind
            or profile.get("target_task_weights")
            is not (expected_kind == "target_task_weights")
        ):
            raise OrthopedicCatalogSeedError("orthopedic model asset kind is invalid")
    return deepcopy(document)


def load_orthopedic_catalog_seed(
    *,
    resource_root: Path = RESOURCE_ROOT,
) -> dict[str, Any]:
    path = resource_root / CATALOG_RESOURCE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OrthopedicCatalogSeedError(
            f"orthopedic catalog seed is unavailable: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise OrthopedicCatalogSeedError("orthopedic catalog seed is invalid JSON") from exc
    return _validate_catalog(document)


async def _ensure_source(
    session: AsyncSession,
    *,
    space_id: UUID,
    source_code: str,
    display_name: str,
    resource_kind: str,
    catalog_version: str,
    catalog_digest: str,
) -> ExternalCatalogSource:
    source = await session.scalar(
        select(ExternalCatalogSource).where(
            ExternalCatalogSource.source_code == source_code
        )
    )
    if source is not None and source.space_id != space_id:
        raise OrthopedicCatalogSeedError(
            f"embedded catalog source {source_code} belongs to another space"
        )
    if source is None:
        source = ExternalCatalogSource(
            space_id=space_id,
            source_code=source_code,
            display_name=display_name,
            base_url=f"https://catalog.medtrust.invalid/{source_code}",
            source_type="embedded_static_catalog",
            resource_kind=resource_kind,
            auth_mode="none",
            enabled=True,
            expected_schema_version="1.0",
            status="ready",
        )
        session.add(source)
        await session.flush()
    source.display_name = display_name
    source.source_type = "embedded_static_catalog"
    source.resource_kind = resource_kind
    source.enabled = True
    source.last_successful_catalog_version = catalog_version
    source.last_successful_digest = catalog_digest
    source.last_synced_at = None
    source.status = "ready"
    return source


def _dataset_values(
    item: dict[str, Any], *, record_digest: str, observed_at: datetime
) -> dict[str, Any]:
    values = {field: item.get(field) for field in _DATASET_FIELDS}
    values.update(
        raw_record_digest=record_digest,
        last_seen_at=observed_at,
        status="active",
        upstream_updated_at=None,
    )
    return values


def _model_values(
    item: dict[str, Any], *, record_digest: str, observed_at: datetime
) -> dict[str, Any]:
    values = {field: item.get(field) for field in _MODEL_FIELDS}
    values.update(
        model_categories=item.get("model_category") or [],
        modalities=item.get("modalities") or [],
        task_types=item.get("task_types") or [],
        disease_areas=item.get("disease_areas") or [],
        organs=item.get("organs") or [],
        species=item.get("species") or [],
        raw_record_digest=record_digest,
        last_seen_at=observed_at,
        status="active",
    )
    return values


async def _upsert_dataset(
    session: AsyncSession,
    *,
    source: ExternalCatalogSource,
    catalog_version: str,
    item: dict[str, Any],
    observed_at: datetime,
) -> str:
    record_digest = _digest(item)
    row = await session.scalar(
        select(ExternalDatasetRecord).where(
            ExternalDatasetRecord.source_id == source.id,
            ExternalDatasetRecord.external_id == item["external_id"],
        )
    )
    if row is None:
        row = ExternalDatasetRecord(
            source_id=source.id,
            external_id=item["external_id"],
            first_seen_at=observed_at,
            **_dataset_values(item, record_digest=record_digest, observed_at=observed_at),
        )
        session.add(row)
        await session.flush()
        version = ExternalDatasetVersion(
            record_id=row.id,
            catalog_version=catalog_version,
            record_digest=record_digest,
            normalized_payload=item,
            source_payload={},
            observed_at=observed_at,
            is_current=True,
        )
        session.add(version)
        await session.flush()
        row.current_version_id = version.id
        return "inserted"
    if row.raw_record_digest == record_digest:
        row.status = "active"
        return "unchanged"

    versions = list(
        (
            await session.scalars(
                select(ExternalDatasetVersion).where(
                    ExternalDatasetVersion.record_id == row.id
                )
            )
        ).all()
    )
    target = next(
        (version for version in versions if version.record_digest == record_digest), None
    )
    for version in versions:
        version.is_current = False
    if target is None:
        target = ExternalDatasetVersion(
            record_id=row.id,
            catalog_version=catalog_version,
            record_digest=record_digest,
            normalized_payload=item,
            source_payload={},
            observed_at=observed_at,
            is_current=True,
        )
        session.add(target)
        await session.flush()
    else:
        target.is_current = True
    for key, value in _dataset_values(
        item, record_digest=record_digest, observed_at=observed_at
    ).items():
        setattr(row, key, value)
    row.current_version_id = target.id
    return "updated"


async def _upsert_model(
    session: AsyncSession,
    *,
    source: ExternalCatalogSource,
    catalog_version: str,
    item: dict[str, Any],
    observed_at: datetime,
) -> str:
    record_digest = _digest(item)
    row = await session.scalar(
        select(ExternalModelRecord).where(
            ExternalModelRecord.source_id == source.id,
            ExternalModelRecord.external_model_id == item["external_model_id"],
        )
    )
    if row is None:
        row = ExternalModelRecord(
            source_id=source.id,
            external_model_id=item["external_model_id"],
            first_seen_at=observed_at,
            **_model_values(item, record_digest=record_digest, observed_at=observed_at),
        )
        session.add(row)
        await session.flush()
        version = ExternalModelVersion(
            record_id=row.id,
            catalog_version=catalog_version,
            record_digest=record_digest,
            normalized_payload=item,
            source_evidence=item["source_evidence"],
            observed_at=observed_at,
            is_current=True,
        )
        session.add(version)
        await session.flush()
        row.current_version_id = version.id
        return "inserted"
    if row.raw_record_digest == record_digest:
        row.status = "active"
        return "unchanged"

    versions = list(
        (
            await session.scalars(
                select(ExternalModelVersion).where(
                    ExternalModelVersion.record_id == row.id
                )
            )
        ).all()
    )
    target = next(
        (version for version in versions if version.record_digest == record_digest), None
    )
    for version in versions:
        version.is_current = False
    if target is None:
        target = ExternalModelVersion(
            record_id=row.id,
            catalog_version=catalog_version,
            record_digest=record_digest,
            normalized_payload=item,
            source_evidence=item["source_evidence"],
            observed_at=observed_at,
            is_current=True,
        )
        session.add(target)
        await session.flush()
    else:
        target.is_current = True
    for key, value in _model_values(
        item, record_digest=record_digest, observed_at=observed_at
    ).items():
        setattr(row, key, value)
    row.current_version_id = target.id
    return "updated"


async def ensure_orthopedic_catalog_seed(
    session: AsyncSession,
    *,
    space_id: UUID,
    resource_root: Path = RESOURCE_ROOT,
) -> OrthopedicCatalogSeedResult:
    catalog = load_orthopedic_catalog_seed(resource_root=resource_root)
    observed_at = datetime.now(timezone.utc)
    catalog_version = str(catalog["catalog_version"])
    data_digest = _digest(catalog["datasets"])
    model_digest = _digest(catalog["models"])
    data_source = await _ensure_source(
        session,
        space_id=space_id,
        source_code=DATASET_SOURCE_CODE,
        display_name="MedTrust Orthopedic Curated Dataset Candidates v1",
        resource_kind="dataset",
        catalog_version=catalog_version,
        catalog_digest=data_digest,
    )
    model_source = await _ensure_source(
        session,
        space_id=space_id,
        source_code=MODEL_SOURCE_CODE,
        display_name="MedTrust Orthopedic Curated Model Candidates v1",
        resource_kind="model",
        catalog_version=catalog_version,
        catalog_digest=model_digest,
    )

    dataset_counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    for item in catalog["datasets"]:
        outcome = await _upsert_dataset(
            session,
            source=data_source,
            catalog_version=catalog_version,
            item=item,
            observed_at=observed_at,
        )
        dataset_counts[outcome] += 1
    model_counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    for item in catalog["models"]:
        outcome = await _upsert_model(
            session,
            source=model_source,
            catalog_version=catalog_version,
            item=item,
            observed_at=observed_at,
        )
        model_counts[outcome] += 1

    fracatlas = next(
        item
        for item in catalog["datasets"]
        if item["external_id"] == "medtrust_orthopedic_dataset_fracatlas_v6"
    )
    return OrthopedicCatalogSeedResult(
        data_source_id=data_source.id,
        model_source_id=model_source.id,
        datasets_inserted=dataset_counts["inserted"],
        datasets_updated=dataset_counts["updated"],
        datasets_unchanged=dataset_counts["unchanged"],
        models_inserted=model_counts["inserted"],
        models_updated=model_counts["updated"],
        models_unchanged=model_counts["unchanged"],
        fracatlas_materialization_status=fracatlas["medtrust_profile"][
            "materialization_status"
        ],
        catalog_digest=_digest(catalog),
    )
