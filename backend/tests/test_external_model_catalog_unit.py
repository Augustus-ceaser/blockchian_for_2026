from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.modules.external_catalog.model_services import (
    ExternalCatalogError,
    _models_url,
    _validate_manifest,
    _validate_models,
    _write_model_snapshot,
)


def source():
    return SimpleNamespace(
        id=uuid4(), source_code="lxltx-public-medical-models",
        expected_schema_version="1.0",
    )


def manifest(**overrides):
    value = {
        "schema_version": "1.0", "catalog_version": "2026.07.27-test",
        "record_count": 1, "models_sha256": "a" * 64,
        "source_catalog": "lxltx-medical-model-catalog",
        "weight_assets_included": False,
    }
    value.update(overrides)
    return value


def model(**overrides):
    value = {
        "external_model_id": "lxltx_model_0123456789abcdef",
        "canonical_name": "Test", "execution_status": "not_materialized",
        "source_evidence": [{"source_url": "https://example.invalid/model"}],
    }
    value.update(overrides)
    return value


def test_models_static_url_is_derived_from_configured_api_root():
    assert _models_url("http://host.docker.internal:3000/api/v1") == (
        "http://host.docker.internal:3000/catalog/v1/models.json"
    )
    with pytest.raises(ExternalCatalogError):
        _models_url("http://host.docker.internal:3000/other")


def test_manifest_rejects_assets_count_and_digest_contracts():
    assert _validate_manifest(source(), manifest())["record_count"] == 1
    for invalid in (
        manifest(weight_assets_included=True),
        manifest(record_count=-1),
        manifest(models_sha256="bad"),
        manifest(source_catalog="unexpected"),
    ):
        with pytest.raises(ExternalCatalogError):
            _validate_manifest(source(), invalid)


def test_model_validation_rejects_duplicate_and_executable_records():
    assert len(_validate_models([model()], manifest())) == 1
    with pytest.raises(ExternalCatalogError):
        _validate_models([model(), model()], manifest(record_count=2))
    with pytest.raises(ExternalCatalogError):
        _validate_models([model(execution_status="execution_ready")], manifest())
    with pytest.raises(ExternalCatalogError):
        _validate_models([model(source_evidence=[])], manifest())


def test_snapshot_uses_model_roots_and_detects_conflict(tmp_path: Path):
    settings = Settings.model_construct(
        storage_root=tmp_path / "data", cache_root=tmp_path / "cache"
    )
    run = SimpleNamespace(
        id=uuid4(), response_etag='"etag"', manifest_digest="b" * 64,
        models_digest="a" * 64,
    )
    _write_model_snapshot(
        settings, source(), run, b'{"manifest":true}', b'[{"model":true}]', manifest()
    )
    snapshot = tmp_path / "data/model-catalog-snapshots/lxltx-public-medical-models/2026.07.27-test/models.json"
    assert snapshot.read_bytes() == b'[{"model":true}]'
    snapshot.write_bytes(b"mutated")
    with pytest.raises(ExternalCatalogError):
        _write_model_snapshot(
            settings, source(), run, b'{"manifest":true}', b'[{"model":true}]', manifest()
        )
