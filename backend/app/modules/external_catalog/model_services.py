from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.demo.phase4 import DemoActor
from app.demo.phase4 import command_for
from app.modules.audit.services import append_audit_event_with_outbox
from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalCatalogSyncRun,
    ExternalModelRecord,
    ExternalModelVersion,
)
from app.modules.external_catalog.services import (
    ExternalCatalogError,
    _canonical_record,
    _fetch,
    _normalized_base_url,
    _parse_json,
    _safe_version,
    _sha256,
    _validate_url,
)

SOURCE_CODE = "lxltx-public-medical-models"
SOURCE_NAME = "LXLTX Public Medical AI Model Candidate Catalog"
EXPECTED_SOURCE_CATALOG = "lxltx-medical-model-catalog"
MODEL_FIELDS = (
    "canonical_name", "display_name_cn", "display_name_en", "source_catalog",
    "paper_title", "paper_doi", "paper_url", "code_repository_url",
    "model_card_url", "upstream_provider", "framework", "library_name",
    "architecture", "pipeline_tag", "input_schema", "output_schema",
    "preprocessing_summary", "license_name", "license_url", "license_status",
    "access_status", "weights_status", "estimated_weights_size_bytes",
    "revision", "commit_sha", "release_tag", "gated", "clinical_use_status",
    "intended_use_summary", "limitations_summary", "execution_status",
)
ARRAY_FIELDS = (
    "model_category", "modalities", "task_types", "disease_areas", "organs",
    "species", "training_dataset_references", "evaluation_dataset_references",
    "metrics_summary", "weights_files", "quality_flags",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_configured_model_source(
    session: AsyncSession, *, settings: Settings, space_id: UUID
) -> ExternalCatalogSource:
    if not settings.external_model_catalog_base_url:
        raise ExternalCatalogError("source_not_configured", "External model catalog is not configured.")
    base_url = _normalized_base_url(settings.external_model_catalog_base_url)
    _validate_url(settings, base_url)
    source = await session.scalar(select(ExternalCatalogSource).where(
        ExternalCatalogSource.space_id == space_id,
        ExternalCatalogSource.source_code == SOURCE_CODE,
    ))
    if source is None:
        source = ExternalCatalogSource(
            space_id=space_id, source_code=SOURCE_CODE, display_name=SOURCE_NAME,
            base_url=base_url, source_type="versioned_rest_model_catalog",
            resource_kind="model", auth_mode="none", enabled=True,
            expected_schema_version="1.0", status="ready",
        )
        session.add(source)
        await session.flush()
    else:
        source.base_url = base_url
        source.resource_kind = "model"
        source.source_type = "versioned_rest_model_catalog"
    return source


def _models_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/api/v1"):
        raise ExternalCatalogError("invalid_source_url", "Catalog base URL must end with /api/v1.")
    root = path[:-len("/api/v1")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{root}/catalog/v1/models.json", "", ""))


def _validate_manifest(source: ExternalCatalogSource, value: Any) -> dict[str, Any]:
    required = {
        "schema_version", "catalog_version", "record_count", "models_sha256",
        "source_catalog", "weight_assets_included",
    }
    if not isinstance(value, dict) or not required.issubset(value):
        raise ExternalCatalogError("invalid_manifest", "Model manifest is missing required fields.")
    if value["schema_version"] != source.expected_schema_version:
        raise ExternalCatalogError("unsupported_schema", "Model catalog schema is unsupported.")
    if value["source_catalog"] != EXPECTED_SOURCE_CATALOG:
        raise ExternalCatalogError("unexpected_catalog", "Model catalog identity does not match.")
    if value["weight_assets_included"] is not False:
        raise ExternalCatalogError("assets_included", "Model catalog unexpectedly includes weight assets.")
    if not isinstance(value["record_count"], int) or value["record_count"] < 0:
        raise ExternalCatalogError("invalid_manifest", "Model record_count is invalid.")
    if not isinstance(value["models_sha256"], str) or len(value["models_sha256"]) != 64:
        raise ExternalCatalogError("invalid_manifest", "Model digest is invalid.")
    return value


def _validate_models(value: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != manifest["record_count"]:
        raise ExternalCatalogError("record_count_mismatch", "Model count differs from manifest.")
    seen: set[str] = set()
    for item in value:
        external_id = item.get("external_model_id") if isinstance(item, dict) else None
        if not isinstance(external_id, str) or not external_id.startswith("lxltx_model_"):
            raise ExternalCatalogError("invalid_model_record", "Model record is invalid.")
        if external_id in seen:
            raise ExternalCatalogError("duplicate_external_id", "Duplicate external_model_id detected.")
        seen.add(external_id)
        if item.get("execution_status") != "not_materialized":
            raise ExternalCatalogError("invalid_execution_status", "Model must remain not_materialized.")
        if not isinstance(item.get("source_evidence"), list) or not item["source_evidence"]:
            raise ExternalCatalogError("invalid_model_record", "Model evidence is required.")
        if len(_canonical_record(item)) > 1024 * 1024:
            raise ExternalCatalogError("record_too_large", "Model record exceeds 1 MB.")
    return value


def _record_values(item: dict[str, Any], digest: str, observed_at: datetime) -> dict[str, Any]:
    values = {field: item.get(field) for field in MODEL_FIELDS}
    values["canonical_name"] = str(values["canonical_name"] or item["external_model_id"])
    values["source_catalog"] = str(values["source_catalog"] or EXPECTED_SOURCE_CATALOG)
    values["execution_status"] = "not_materialized"
    values["model_categories"] = item.get("model_category") or []
    for field in ARRAY_FIELDS:
        if field != "model_category":
            values[field] = item.get(field) or []
    values.update(raw_record_digest=digest, last_seen_at=observed_at, status="active")
    return values


async def _apply_models(session: AsyncSession, source: ExternalCatalogSource, run: ExternalCatalogSyncRun, models: list[dict[str, Any]]) -> None:
    observed_at = _now()
    existing = {row.external_model_id: row for row in (await session.scalars(
        select(ExternalModelRecord).where(ExternalModelRecord.source_id == source.id)
    )).all()}
    seen: set[str] = set()
    for item in models:
        external_id = item["external_model_id"]
        seen.add(external_id)
        digest = _sha256(_canonical_record(item))
        row = existing.get(external_id)
        if row is None:
            row = ExternalModelRecord(
                source_id=source.id, external_model_id=external_id,
                first_seen_at=observed_at, **_record_values(item, digest, observed_at)
            )
            session.add(row)
            await session.flush()
            version = ExternalModelVersion(
                record_id=row.id, catalog_version=run.catalog_version or "",
                record_digest=digest, normalized_payload=item,
                source_evidence=item["source_evidence"], observed_at=observed_at,
                is_current=True,
            )
            session.add(version)
            await session.flush()
            row.current_version_id = version.id
            run.inserted_count += 1
        elif row.raw_record_digest != digest:
            if row.current_version_id:
                current = await session.get(ExternalModelVersion, row.current_version_id)
                if current:
                    current.is_current = False
            for key, value in _record_values(item, digest, observed_at).items():
                setattr(row, key, value)
            version = ExternalModelVersion(
                record_id=row.id, catalog_version=run.catalog_version or "",
                record_digest=digest, normalized_payload=item,
                source_evidence=item["source_evidence"], observed_at=observed_at,
                is_current=True,
            )
            session.add(version)
            await session.flush()
            row.current_version_id = version.id
            run.updated_count += 1
        else:
            row.last_seen_at = observed_at
            row.status = "active"
            run.unchanged_count += 1
    for external_id, row in existing.items():
        if external_id not in seen and row.status != "stale":
            row.status = "stale"
            run.stale_count += 1


def _write_model_snapshot(settings: Settings, source: ExternalCatalogSource, run: ExternalCatalogSyncRun, manifest_raw: bytes, models_raw: bytes, manifest: dict[str, Any]) -> None:
    version = _safe_version(manifest["catalog_version"])
    stage = settings.cache_root / "partial" / f"model-catalog-{run.id}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        (stage / "manifest.json").write_bytes(manifest_raw)
        (stage / "models.json").write_bytes(models_raw)
        provenance = {
            "source_code": source.source_code, "resource_kind": "model",
            "catalog_version": version, "schema_version": manifest["schema_version"],
            "record_count": manifest["record_count"], "http_etag": run.response_etag,
            "manifest_digest": run.manifest_digest, "models_digest": run.models_digest,
            "sync_run_id": str(run.id), "fetched_at": _now().isoformat(),
        }
        (stage / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        destinations = {
            "manifest.json": settings.storage_root / "model-catalog-manifests" / source.source_code / version / "manifest.json",
            "models.json": settings.storage_root / "model-catalog-snapshots" / source.source_code / version / "models.json",
            "provenance.json": settings.storage_root / "model-catalog-provenance" / source.source_code / version / "provenance.json",
        }
        for filename, destination in destinations.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = stage / filename
            if destination.exists():
                if filename != "provenance.json" and _sha256(destination.read_bytes()) != _sha256(staged.read_bytes()):
                    raise ExternalCatalogError("snapshot_digest_conflict", "Existing model snapshot conflicts.")
            else:
                same_volume_partial = destination.with_name(
                    f".{destination.name}.{run.id}.partial"
                )
                shutil.copyfile(staged, same_volume_partial)
                if _sha256(same_volume_partial.read_bytes()) != _sha256(staged.read_bytes()):
                    same_volume_partial.unlink(missing_ok=True)
                    raise ExternalCatalogError(
                        "snapshot_copy_mismatch", "Model snapshot copy verification failed."
                    )
                os.replace(same_volume_partial, destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


async def synchronize_model_catalog(session: AsyncSession, *, settings: Settings, source: ExternalCatalogSource, actor: DemoActor, raw_key: str) -> ExternalCatalogSyncRun:
    run = ExternalCatalogSyncRun(
        source_id=source.id, resource_kind="model", status="fetching_manifest",
        request_etag=source.last_successful_etag,
    )
    session.add(run)
    await session.flush()
    command = command_for(actor, f"external-model-catalog-sync-start:{run.id}", raw_key)
    await append_audit_event_with_outbox(
        session, space_id=source.space_id,
        event_type="external_catalog.sync.started",
        subject_type="external_catalog_sync_run", subject_id=run.id,
        result="success", evidence_snapshot={
            "schema_version": "phase5.12.2/model-catalog-sync-start/v1",
            "source_code": source.source_code, "resource_kind": "model",
            "request_etag_present": bool(run.request_etag),
        }, **command.append_kwargs(),
    )
    try:
        status, headers, manifest_raw = await _fetch(
            settings, f"{source.base_url}/model-catalog/manifest",
            etag=source.last_successful_etag,
        )
        run.http_status = status
        run.response_etag = headers.get("etag")
        if status == 304:
            run.status = "not_modified"
            run.unchanged_count = int(await session.scalar(select(func.count(ExternalModelRecord.id)).where(ExternalModelRecord.source_id == source.id)) or 0)
        else:
            run.status = "validating"
            manifest = _validate_manifest(source, _parse_json(manifest_raw, "Model manifest"))
            run.schema_version = manifest["schema_version"]
            run.catalog_version = manifest["catalog_version"]
            run.expected_record_count = manifest["record_count"]
            run.manifest_digest = _sha256(manifest_raw)
            _, _, models_raw = await _fetch(settings, _models_url(source.base_url))
            run.models_digest = _sha256(models_raw)
            if run.models_digest != manifest["models_sha256"]:
                raise ExternalCatalogError("models_digest_mismatch", "Models SHA-256 mismatch.")
            models = _validate_models(_parse_json(models_raw, "Models"), manifest)
            run.received_record_count = len(models)
            _write_model_snapshot(settings, source, run, manifest_raw, models_raw, manifest)
            run.status = "applying"
            await _apply_models(session, source, run, models)
            run.status = "succeeded"
            source.last_successful_catalog_version = run.catalog_version
            source.last_successful_etag = run.response_etag
            source.last_successful_digest = run.models_digest
        run.completed_at = _now()
        source.last_synced_at = run.completed_at
        source.status = "ready"
    except ExternalCatalogError as exc:
        run.status = "failed"
        run.completed_at = _now()
        run.error_code = exc.code
        run.error_summary = str(exc)[:1000]
        source.status = "error"
    return run
