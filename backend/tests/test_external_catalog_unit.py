from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.api.routes.external_catalog import _matches_disease_or_organ
from app.modules.external_catalog.models import ExternalCatalogSource, ExternalCatalogSyncRun
from app.modules.external_catalog.services import (
    ExternalCatalogError,
    _static_datasets_url,
    _validate_manifest,
    _validate_records,
    _validate_url,
    _write_snapshot,
    _request_bytes,
)


def local_settings(tmp_path: Path) -> Settings:
    return Settings(
        deployment_mode="local",
        external_catalog_base_url="http://127.0.0.1:3000/api/v1",
        allow_insecure_local_catalog=True,
        storage_root=tmp_path / "data",
        cache_root=tmp_path / "cache",
    )


def source() -> ExternalCatalogSource:
    return ExternalCatalogSource(
        id=uuid4(),
        space_id=uuid4(),
        source_code="lxltx-public-medical-datasets",
        display_name="test",
        base_url="http://127.0.0.1:3000/api/v1",
        source_type="http_json",
        auth_mode="none",
        expected_schema_version="1.0",
        status="ready",
    )


def manifest(dataset_bytes: bytes) -> dict:
    return {
        "schema_version": "1.0",
        "catalog_version": "2026.07.27-test",
        "record_count": 1,
        "datasets_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "source_catalog": "lxltx-dataset-browser",
        "download_assets_included": False,
    }


def test_local_http_requires_explicit_allowance(tmp_path: Path) -> None:
    settings = local_settings(tmp_path)
    _validate_url(settings, "http://127.0.0.1:3000/api/v1")
    with pytest.raises(ValueError):
        Settings(
            deployment_mode="remote-preview",
            external_catalog_base_url="http://127.0.0.1:3000/api/v1",
            allow_insecure_local_catalog=True,
        )


@pytest.mark.parametrize("url", ["file:///tmp/data", "ftp://127.0.0.1/a", "gopher://127.0.0.1/a"])
def test_non_http_schemes_are_rejected(tmp_path: Path, url: str) -> None:
    with pytest.raises(ExternalCatalogError):
        _validate_url(local_settings(tmp_path), url)


def test_static_url_is_derived_from_controlled_api_base() -> None:
    assert (
        _static_datasets_url("http://host.docker.internal:3000/api/v1")
        == "http://host.docker.internal:3000/catalog/v1/datasets.json"
    )


def test_manifest_and_records_reject_invalid_inputs() -> None:
    row = source()
    raw = json.dumps([{"external_id": "one"}]).encode()
    valid = manifest(raw)
    assert _validate_manifest(row, valid)["record_count"] == 1
    with pytest.raises(ExternalCatalogError, match="unsupported"):
        _validate_manifest(row, {**valid, "schema_version": "2.0"})
    with pytest.raises(ExternalCatalogError, match="Duplicate"):
        _validate_records([{"external_id": "one"}, {"external_id": "one"}], {**valid, "record_count": 2})


def test_snapshot_is_written_to_configured_roots_and_conflicts_fail(tmp_path: Path) -> None:
    settings = local_settings(tmp_path)
    row = source()
    datasets = json.dumps([{"external_id": "one"}], separators=(",", ":")).encode()
    document = manifest(datasets)
    manifest_raw = json.dumps(document, separators=(",", ":")).encode()
    run = ExternalCatalogSyncRun(
        id=uuid4(),
        source_id=row.id,
        status="validating",
        response_etag='"test"',
        manifest_digest=hashlib.sha256(manifest_raw).hexdigest(),
        datasets_digest=hashlib.sha256(datasets).hexdigest(),
    )
    _write_snapshot(settings, row, run, manifest_raw, datasets, document)
    snapshot = settings.storage_root / "catalog-snapshots" / row.source_code / document["catalog_version"] / "datasets.json"
    assert snapshot.read_bytes() == datasets
    assert not any((settings.cache_root / "partial").iterdir())
    snapshot.write_bytes(b"mutated")
    with pytest.raises(ExternalCatalogError, match="different contents"):
        _write_snapshot(settings, row, run, manifest_raw, datasets, document)


def test_unreachable_catalog_is_redacted(tmp_path: Path) -> None:
    settings = local_settings(tmp_path)
    settings.external_catalog_timeout_seconds = 0.1
    with pytest.raises(ExternalCatalogError) as captured:
        _request_bytes(settings, "http://127.0.0.1:9/api/v1/catalog/manifest")
    assert captured.value.code == "upstream_unreachable"
    assert str(captured.value) == "Configured catalog source is unreachable."


def test_disease_or_organ_search_is_partial_case_insensitive_and_optional() -> None:
    diseases = ["Prostate cancer", "Adult ADHD"]
    organs = ["Prostate", "Brain"]

    assert _matches_disease_or_organ(diseases, organs, "prost")
    assert _matches_disease_or_organ(diseases, organs, "BRAIN")
    assert _matches_disease_or_organ(diseases, organs, "  adult  ")
    assert _matches_disease_or_organ(diseases, organs, "   ")
    assert not _matches_disease_or_organ(diseases, organs, "kidney")
