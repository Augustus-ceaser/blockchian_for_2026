from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.main import create_app
from app.core.config import Settings
from app.modules.audit import canonical_json_digest_v1
from app.modules.connector_control.models import (
    ConnectorAssetMirror,
    ConnectorAssetMirrorVersion,
)
from app.modules.connector_control.services import (
    ConnectorControlError,
    _reject_prohibited_metadata,
)

ROOT = Path(__file__).resolve().parents[2]


def test_central_mirror_is_parallel_to_products_and_execution() -> None:
    assert ConnectorAssetMirror.__tablename__ == "connector_asset_mirrors"
    assert ConnectorAssetMirrorVersion.__tablename__ == "connector_asset_mirror_versions"
    assert "data_products" not in {fk.column.table.name for fk in ConnectorAssetMirror.__table__.foreign_keys}
    assert "compute_jobs" not in {fk.column.table.name for fk in ConnectorAssetMirrorVersion.__table__.foreign_keys}


@pytest.mark.parametrize("payload", [
    {"path": r"D:\secret\patient.csv"},
    {"nested": {"filename": "patient-001.dcm"}},
    {"patient_ids": ["p-001"]},
    {"database_url": "postgresql://secret"},
    {"internal_ip": "10.0.0.12"},
    {"safe": {"nested": [{"local_path": "/srv/data"}]}},
])
def test_prohibited_fields_are_rejected_recursively(payload: dict) -> None:
    with pytest.raises(ConnectorControlError, match="PROHIBITED_METADATA"):
        _reject_prohibited_metadata(payload)


def test_approved_summary_is_allowed() -> None:
    _reject_prohibited_metadata({
        "display_name": "PathMNIST Fixed 20-Sample Local Demo Asset",
        "record_count": {"mode": "exact", "value": 20},
        "warning_flags": ["metadata_only"],
    })


def test_local_registry_migration_fixture_and_append_only(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "hospital_connector_registry", ROOT / "hospital-connector/app/registry.py"
    )
    assert spec and spec.loader
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)

    db = sqlite3.connect(tmp_path / "connector.sqlite3")
    db.row_factory = sqlite3.Row
    registry.migrate(db)
    assert db.execute(
        "SELECT version FROM local_schema_migrations"
    ).fetchone()["version"] == "phase5.13C_0001"
    result = registry.seed_public_fixture(
        db, connector_id="00000000-0000-0000-0000-000000000001",
        canonical_digest=canonical_json_digest_v1,
    )
    assert result == {"assets": 1, "versions": 2, "quality_profiles": 2, "bundles": 2}
    assets = registry.list_assets(db)
    assert len(assets) == 1
    assert assets[0]["fitness_for_use_status"] == "locally_reviewed"
    bundles = db.execute("SELECT payload_json FROM local_asset_metadata_bundles").fetchall()
    assert len(bundles) == 2
    serialized = "\n".join(row["payload_json"] for row in bundles).lower()
    for forbidden in ("location_alias", "encrypted_location", "patient_id", "filename", "database_url", r"d:\\"):
        assert forbidden not in serialized
    version_id = db.execute("SELECT id FROM local_asset_versions LIMIT 1").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE local_asset_versions SET version_label='mutated' WHERE id=?", (version_id,))


def test_openapi_exposes_metadata_only_sync_without_file_surface() -> None:
    paths = create_app(Settings(app_env="test")).openapi()["paths"]
    assert "/api/v1/connector-control/ingress/connectors/{connector_id}/asset-metadata" in paths
    connector_paths = "\n".join(path for path in paths if "connector-control" in path)
    assert "upload" not in connector_paths
    assert "download" not in connector_paths
    assert "execution-order" not in connector_paths


def test_metadata_bundle_key_accepts_a1_identifier_without_relaxing_character_allowlist() -> None:
    from app.api.routes.connector_control import AssetMetadataBundleRequest
    import pytest
    from pydantic import ValidationError

    common = {
        "schema_version": "phase5.13C/metadata-bundle/v1",
        "bundle_id": "bundle-00000000-0000-0000-0000-000000000001",
        "bundle_sequence": 1,
        "version_label": "v1",
        "metadata_summary": {},
        "disclosure_summary": {},
        "quality_summary": {},
        "deidentification_summary": {},
        "known_limitations": [],
        "warning_flags": [],
        "metadata_digest": "sha256:" + "1" * 64,
        "schema_digest": "sha256:" + "2" * 64,
        "quality_digest": "sha256:" + "3" * 64,
        "bundle_digest": "sha256:" + "4" * 64,
        "signed_at": "2026-07-29T00:00:00Z",
        "nonce": "a" * 32,
    }
    assert AssetMetadataBundleRequest(
        **common, local_asset_key="A1-LOCAL-ASSET-20260729"
    ).local_asset_key.startswith("A1-")
    with pytest.raises(ValidationError):
        AssetMetadataBundleRequest(**common, local_asset_key="../private/path")


def test_migration_is_single_increment_and_has_immutability_guards() -> None:
    source = (ROOT / "backend/alembic/versions/20260729_0051_phase513c_connector_asset_mirror.py").read_text(encoding="utf-8")
    assert 'revision = "20260729_0051"' in source
    assert 'down_revision = "20260729_0050"' in source
    assert "guard_connector_asset_mirror_immutable" in source
    assert "append-only" in source


def test_local_audit_endpoint_verifies_digest_chain() -> None:
    source = (ROOT / "hospital-connector/app/main.py").read_text(encoding="utf-8")
    assert '"chain_valid": valid' in source
    assert '"head_digest": previous' in source
