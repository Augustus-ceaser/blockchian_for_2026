from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.demo.phase4 import DemoActor, command_for
from app.modules.audit.services import append_audit_event_with_outbox
from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalCatalogSyncRun,
    ExternalDatasetRecord,
    ExternalDatasetVersion,
)

SOURCE_CODE = "lxltx-public-medical-datasets"
SOURCE_NAME = "LXLTX Public Medical Dataset Catalog"
EXPECTED_SOURCE_CATALOG = "lxltx-dataset-browser"
MAX_SUMMARY_LENGTH = 1000


class ExternalCatalogError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_record(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _normalized_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _validate_url(settings: Settings, value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ExternalCatalogError("invalid_source_url", "Catalog URL must use HTTP(S).")
    if parsed.username or parsed.password or parsed.fragment:
        raise ExternalCatalogError("invalid_source_url", "Catalog URL contains forbidden parts.")
    if parsed.scheme == "http":
        allowed = {"127.0.0.1", "localhost", "host.docker.internal"}
        if (
            settings.deployment_mode not in {"local", "lan-roadshow"}
            or not settings.allow_insecure_local_catalog
            or parsed.hostname not in allowed
        ):
            raise ExternalCatalogError(
                "insecure_transport", "HTTP catalog access is restricted to explicit local mode."
            )


def _request_bytes(
    settings: Settings, url: str, *, etag: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    _validate_url(settings, url)
    headers = {"Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    request = Request(url, headers=headers, method="GET")
    class RejectRedirects(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ExternalCatalogError(
                "redirect_rejected", "Catalog redirects are not allowed."
            )

    try:
        response = build_opener(RejectRedirects).open(
            request, timeout=settings.external_catalog_timeout_seconds
        )
    except HTTPError as exc:
        if exc.code == 304:
            return 304, {key.lower(): value for key, value in exc.headers.items()}, b""
        raise ExternalCatalogError("upstream_http_error", f"Catalog returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise ExternalCatalogError(
            "upstream_unreachable", "Configured catalog source is unreachable."
        ) from exc
    with response:
        if response.status != 200:
            raise ExternalCatalogError(
                "upstream_http_error", f"Catalog returned HTTP {response.status}."
            )
        final_url = response.geturl()
        if final_url != url:
            raise ExternalCatalogError("redirect_rejected", "Catalog redirects are not allowed.")
        content_type = response.headers.get_content_type()
        if content_type not in {"application/json", "text/json"}:
            raise ExternalCatalogError("invalid_content_type", "Catalog response is not JSON.")
        declared = response.headers.get("Content-Length")
        limit = settings.external_catalog_max_response_bytes
        if declared and int(declared) > limit:
            raise ExternalCatalogError("response_too_large", "Catalog response exceeds 50 MB.")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(min(1024 * 1024, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise ExternalCatalogError("response_too_large", "Catalog response exceeds 50 MB.")
        return (
            response.status,
            {key.lower(): value for key, value in response.headers.items()},
            b"".join(chunks),
        )


async def _fetch(
    settings: Settings, url: str, *, etag: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    return await asyncio.to_thread(_request_bytes, settings, url, etag=etag)


def _static_datasets_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    suffix = "/api/v1"
    if not path.endswith(suffix):
        raise ExternalCatalogError(
            "invalid_source_url", "Catalog base URL must end with /api/v1."
        )
    root = path[: -len(suffix)]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{root}/catalog/v1/datasets.json", "", ""))


def _parse_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalCatalogError("invalid_json", f"{label} is not valid UTF-8 JSON.") from exc


def _validate_manifest(source: ExternalCatalogSource, document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ExternalCatalogError("invalid_manifest", "Manifest must be an object.")
    required = {
        "schema_version",
        "catalog_version",
        "record_count",
        "datasets_sha256",
        "source_catalog",
        "download_assets_included",
    }
    if not required.issubset(document):
        raise ExternalCatalogError("invalid_manifest", "Manifest is missing required fields.")
    if document["schema_version"] != source.expected_schema_version:
        raise ExternalCatalogError("unsupported_schema", "Catalog schema version is unsupported.")
    if document["source_catalog"] != EXPECTED_SOURCE_CATALOG:
        raise ExternalCatalogError("unexpected_catalog", "Catalog source identity does not match.")
    if document["download_assets_included"] is not False:
        raise ExternalCatalogError("assets_included", "Metadata catalog unexpectedly includes assets.")
    if not isinstance(document["record_count"], int) or document["record_count"] < 0:
        raise ExternalCatalogError("invalid_manifest", "Manifest record_count is invalid.")
    digest = document["datasets_sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ExternalCatalogError("invalid_manifest", "Manifest digest is invalid.")
    return document


def _validate_records(document: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(document, list):
        raise ExternalCatalogError("invalid_dataset_catalog", "Datasets root must be an array.")
    if len(document) != manifest["record_count"]:
        raise ExternalCatalogError("record_count_mismatch", "Dataset count differs from manifest.")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in document:
        if not isinstance(item, dict) or not isinstance(item.get("external_id"), str):
            raise ExternalCatalogError("invalid_dataset_record", "Dataset record is invalid.")
        external_id = item["external_id"]
        if external_id in seen:
            raise ExternalCatalogError("duplicate_external_id", "Duplicate external_id detected.")
        seen.add(external_id)
        result.append(item)
    return result


def _safe_version(value: str) -> str:
    if not value or any(part in value for part in ("..", "/", "\\", ":")):
        raise ExternalCatalogError("invalid_catalog_version", "Catalog version is unsafe.")
    return value


def _write_snapshot(
    settings: Settings,
    source: ExternalCatalogSource,
    run: ExternalCatalogSyncRun,
    manifest_raw: bytes,
    datasets_raw: bytes,
    manifest: dict[str, Any],
) -> None:
    version = _safe_version(manifest["catalog_version"])
    data_root = settings.storage_root
    cache_root = settings.cache_root
    partial_root = cache_root / "partial"
    partial_root.mkdir(parents=True, exist_ok=True)
    stage = partial_root / f"catalog-{run.id}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    try:
        (stage / "manifest.json").write_bytes(manifest_raw)
        (stage / "datasets.json").write_bytes(datasets_raw)
        provenance = {
            "source_code": source.source_code,
            "base_url": _normalized_base_url(source.base_url),
            "fetched_at": _now().isoformat(),
            "http_etag": run.response_etag,
            "schema_version": manifest["schema_version"],
            "catalog_version": version,
            "record_count": manifest["record_count"],
            "manifest_digest": run.manifest_digest,
            "datasets_digest": run.datasets_digest,
            "sync_run_id": str(run.id),
        }
        (stage / "provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        destinations = {
            "manifest.json": data_root / "catalog-manifests" / source.source_code / version / "manifest.json",
            "datasets.json": data_root / "catalog-snapshots" / source.source_code / version / "datasets.json",
            "provenance.json": data_root / "catalog-provenance" / source.source_code / version / "provenance.json",
        }
        for filename, destination in destinations.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = stage / filename
            if destination.exists():
                if _sha256(destination.read_bytes()) != _sha256(staged.read_bytes()):
                    raise ExternalCatalogError(
                        "snapshot_digest_conflict",
                        "Existing snapshot version has different contents.",
                    )
                continue
            os.replace(staged, destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


async def ensure_configured_source(
    session: AsyncSession, *, settings: Settings, space_id: UUID
) -> ExternalCatalogSource:
    if not settings.external_catalog_base_url:
        raise ExternalCatalogError(
            "source_not_configured", "External catalog base URL is not configured."
        )
    base_url = _normalized_base_url(settings.external_catalog_base_url)
    _validate_url(settings, base_url)
    source = await session.scalar(
        select(ExternalCatalogSource).where(
            ExternalCatalogSource.space_id == space_id,
            ExternalCatalogSource.source_code == SOURCE_CODE,
        )
    )
    if source is None:
        source = ExternalCatalogSource(
            space_id=space_id,
            source_code=SOURCE_CODE,
            display_name=SOURCE_NAME,
            base_url=base_url,
            source_type="versioned_rest_catalog",
            auth_mode="none",
            enabled=True,
            expected_schema_version="1.0",
            status="ready",
        )
        session.add(source)
        await session.flush()
    elif source.base_url != base_url:
        source.base_url = base_url
    source.source_type = "versioned_rest_catalog"
    return source


def _record_values(item: dict[str, Any], digest: str, observed_at: datetime) -> dict[str, Any]:
    return {
        "canonical_name": str(item.get("canonical_name") or item["external_id"]),
        "display_name_cn": item.get("display_name_cn"),
        "display_name_en": item.get("display_name_en"),
        "source_catalog": str(item.get("source_catalog") or EXPECTED_SOURCE_CATALOG),
        "official_source_name": item.get("official_source_name"),
        "official_source_url": item.get("official_source_url"),
        "catalog_source_url": item.get("catalog_source_url"),
        "modalities": item.get("modalities") or [],
        "disease_areas": item.get("disease_areas") or [],
        "organs": item.get("organs") or [],
        "task_types": item.get("task_types") or [],
        "species": item.get("species"),
        "sample_count": item.get("sample_count"),
        "patient_count": item.get("patient_count"),
        "file_count": item.get("file_count"),
        "approximate_size_bytes": item.get("approximate_size_bytes"),
        "data_formats": item.get("data_formats") or [],
        "license_name": item.get("license_name"),
        "license_url": item.get("license_url"),
        "license_status": item.get("license_status") or "unknown",
        "access_level": item.get("access_level") or "unknown",
        "registration_required": item.get("registration_required"),
        "dataset_version": item.get("dataset_version"),
        "upstream_updated_at": None,
        "link_status": item.get("link_status") or "unknown",
        "quality_flags": item.get("quality_flags") or [],
        "duplicate_group_id": item.get("duplicate_group_id"),
        "raw_record_digest": digest,
        "last_seen_at": observed_at,
        "status": "active",
    }


async def _apply_records(
    session: AsyncSession,
    source: ExternalCatalogSource,
    run: ExternalCatalogSyncRun,
    records: list[dict[str, Any]],
) -> None:
    observed_at = _now()
    existing = {
        row.external_id: row
        for row in (
            await session.scalars(
                select(ExternalDatasetRecord).where(
                    ExternalDatasetRecord.source_id == source.id
                )
            )
        ).all()
    }
    seen: set[str] = set()
    for item in records:
        external_id = item["external_id"]
        seen.add(external_id)
        digest = _sha256(_canonical_record(item))
        row = existing.get(external_id)
        if row is None:
            row = ExternalDatasetRecord(
                source_id=source.id,
                external_id=external_id,
                first_seen_at=observed_at,
                **_record_values(item, digest, observed_at),
            )
            session.add(row)
            await session.flush()
            version = ExternalDatasetVersion(
                record_id=row.id,
                catalog_version=run.catalog_version or "",
                record_digest=digest,
                normalized_payload={k: v for k, v in item.items() if k != "source_payload"},
                source_payload=item.get("source_payload") or {},
                observed_at=observed_at,
                is_current=True,
            )
            session.add(version)
            await session.flush()
            row.current_version_id = version.id
            run.inserted_count += 1
        elif row.raw_record_digest != digest:
            if row.current_version_id:
                current = await session.get(ExternalDatasetVersion, row.current_version_id)
                if current:
                    current.is_current = False
            for key, value in _record_values(item, digest, observed_at).items():
                setattr(row, key, value)
            version = ExternalDatasetVersion(
                record_id=row.id,
                catalog_version=run.catalog_version or "",
                record_digest=digest,
                normalized_payload={k: v for k, v in item.items() if k != "source_payload"},
                source_payload=item.get("source_payload") or {},
                observed_at=observed_at,
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


async def synchronize_catalog(
    session: AsyncSession,
    *,
    settings: Settings,
    source: ExternalCatalogSource,
    actor: DemoActor,
    raw_key: str,
) -> ExternalCatalogSyncRun:
    run = ExternalCatalogSyncRun(
        source_id=source.id,
        status="fetching_manifest",
        request_etag=source.last_successful_etag,
    )
    session.add(run)
    await session.flush()
    command = command_for(actor, f"external-catalog-sync:{run.id}", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=source.space_id,
        event_type="external_catalog.sync.started",
        subject_type="external_catalog_sync_run",
        subject_id=run.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.11.2/external-catalog-sync-start/v1",
            "source_code": source.source_code,
            "request_etag_present": bool(run.request_etag),
        },
        **command.append_kwargs(),
    )
    event_type = "external_catalog.sync.failed"
    result = "failure"
    try:
        manifest_url = f"{source.base_url}/catalog/manifest"
        status, headers, manifest_raw = await _fetch(
            settings, manifest_url, etag=source.last_successful_etag
        )
        run.http_status = status
        run.response_etag = headers.get("etag")
        if status == 304:
            run.status = "not_modified"
            run.completed_at = _now()
            run.unchanged_count = int(
                await session.scalar(
                    select(func.count(ExternalDatasetRecord.id)).where(
                        ExternalDatasetRecord.source_id == source.id
                    )
                )
                or 0
            )
            source.last_synced_at = run.completed_at
            source.status = "ready"
            event_type = "external_catalog.sync.not_modified"
            result = "success"
        else:
            run.status = "validating"
            manifest = _validate_manifest(source, _parse_json(manifest_raw, "Manifest"))
            run.schema_version = manifest["schema_version"]
            run.catalog_version = manifest["catalog_version"]
            run.expected_record_count = manifest["record_count"]
            run.manifest_digest = _sha256(manifest_raw)
            _, _, datasets_raw = await _fetch(settings, _static_datasets_url(source.base_url))
            run.datasets_digest = _sha256(datasets_raw)
            if run.datasets_digest != manifest["datasets_sha256"]:
                raise ExternalCatalogError("datasets_digest_mismatch", "Datasets SHA-256 mismatch.")
            records = _validate_records(_parse_json(datasets_raw, "Datasets"), manifest)
            run.received_record_count = len(records)
            _write_snapshot(settings, source, run, manifest_raw, datasets_raw, manifest)
            run.status = "applying"
            await _apply_records(session, source, run, records)
            run.status = "succeeded"
            run.completed_at = _now()
            source.last_successful_catalog_version = run.catalog_version
            source.last_successful_etag = run.response_etag
            source.last_successful_digest = run.datasets_digest
            source.last_synced_at = run.completed_at
            source.status = "ready"
            event_type = "external_catalog.sync.succeeded"
            result = "success"
    except ExternalCatalogError as exc:
        run.status = "failed"
        run.completed_at = _now()
        run.error_code = exc.code
        run.error_summary = str(exc)[:MAX_SUMMARY_LENGTH]
        source.status = "error"
    await append_audit_event_with_outbox(
        session,
        space_id=source.space_id,
        event_type=event_type,
        subject_type="external_catalog_sync_run",
        subject_id=run.id,
        result=result,
        evidence_snapshot={
            "schema_version": "phase5.11.2/external-catalog-sync/v1",
            "source_code": source.source_code,
            "sync_status": run.status,
            "catalog_version": run.catalog_version,
            "http_status": run.http_status,
            "received_count": run.received_record_count,
            "inserted_count": run.inserted_count,
            "updated_count": run.updated_count,
            "stale_count": run.stale_count,
            "datasets_digest": run.datasets_digest,
            "error_code": run.error_code,
        },
        **command.append_kwargs(),
    )
    return run
