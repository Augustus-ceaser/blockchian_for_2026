from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalDatasetRecord,
    ExternalDatasetVersion,
    ExternalModelRecord,
    ExternalModelVersion,
)
from app.modules.external_catalog.orthopedic_materialization import (
    FRACATLAS_EXTERNAL_ID,
    FRACATLAS_MODEL_EXTERNAL_ID,
    LOCAL_MATERIALIZATION_RESOURCE,
    OrthopedicMaterializationError,
    materialize_local_orthopedic_assets,
    verify_local_orthopedic_assets,
)
from app.modules.external_catalog.orthopedic_seed import (
    DATASET_SOURCE_CODE,
    MODEL_SOURCE_CODE,
    ensure_orthopedic_catalog_seed,
)
from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space


def _digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _asset_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    asset_root = tmp_path / "assets"
    resource_root = tmp_path / "resources"

    archive = asset_root / "datasets/fracatlas/v6/archive/FracAtlas.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"fixed-fracatlas-archive")
    extracted = asset_root / "datasets/fracatlas/v6/extracted/FracAtlas"
    dataset_index = extracted / "dataset.csv"
    dataset_index.parent.mkdir(parents=True)
    dataset_index.write_bytes(b"image_id,fractured\n1,1\n2,0\n3,0\n")
    fractured = extracted / "images/Fractured"
    non_fractured = extracted / "images/Non_fractured"
    fractured.mkdir(parents=True)
    non_fractured.mkdir(parents=True)
    (fractured / "one.jpg").write_bytes(b"one")
    (non_fractured / "two.jpg").write_bytes(b"two")
    (non_fractured / "three.jpg").write_bytes(b"three")

    model_root = (
        asset_root / "models/target-task/medtrust-fracatlas-mnv3/v1"
    )
    model_root.mkdir(parents=True)
    weights = model_root / "fracatlas_mobilenet_v3_small_v1.pt"
    weights.write_bytes(b"fixed-model-weights")
    evaluation = model_root / "evaluation.json"
    _write_json(
        evaluation,
        {
            "schema_version": "medtrust.model-evaluation/v1",
            "model_id": "medtrust-fracatlas-mobilenet-v3-small-v1",
            "evidence_scope": "image_level_technical_validation",
            "limitations": ["image-level split only"],
            "test": {
                "metrics_valid": True,
                "accuracy": 0.75,
                "balanced_accuracy": 0.7,
                "precision": 0.6,
                "sensitivity": 0.5,
                "specificity": 0.9,
                "f1": 0.55,
            },
        },
    )
    split_inventory = model_root / "split_inventory.json"
    _write_json(
        split_inventory,
        {
            "schema_version": "medtrust.dataset-split-inventory/v1",
            "dataset_version": "v6",
            "split_scope": "image_level",
            "splits": {
                "train": {"count": 1},
                "valid": {"count": 1},
                "test": {"count": 1},
            },
        },
    )
    model_manifest = model_root / "model_manifest.json"
    _write_json(
        model_manifest,
        {
            "schema_version": "medtrust.fixed-model-manifest/v1",
            "model_id": "medtrust-fracatlas-mobilenet-v3-small-v1",
            "version": "1.0.0",
            "architecture": "mobilenet_v3_small_frozen_backbone_linear_head",
            "task": "fracture_presence_image_classification",
            "classes": ["non_fractured", "fractured"],
            "input": {"color_space": "RGB", "transform": "resize 224"},
            "weights": {
                "path": weights.name,
                "bytes": weights.stat().st_size,
                "sha256": _digest(weights),
            },
            "evidence": {
                "level": "image_level_technical_validation",
                "evaluation_path": evaluation.name,
                "evaluation_sha256": _digest(evaluation),
                "split_inventory_path": split_inventory.name,
                "split_inventory_sha256": _digest(split_inventory),
            },
            "license_notes": {
                "dataset": "CC BY 4.0",
                "backbone": "upstream terms",
                "local_head": "MedTrust generated",
            },
            "readiness": {
                "application_eligible": False,
                "clinical_use": False,
                "compute_eligible": False,
                "executor_registered": False,
                "hard_isolation": False,
            },
        },
    )

    overlay = {
        "schema_version": "medtrust.orthopedic-local-materialization/v1",
        "manifest_id": "test-orthopedic-local-assets",
        "manifest_version": "test-local-v1",
        "dataset": {
            "external_id": FRACATLAS_EXTERNAL_ID,
            "asset_id": "orthopedic.dataset.fracatlas.v6",
            "archive": {
                "asset_id": "orthopedic.dataset.fracatlas.v6.archive",
                "relative_path": "datasets/fracatlas/v6/archive/FracAtlas.zip",
                "size_bytes": archive.stat().st_size,
                "sha256": _digest(archive),
                "md5": _digest(archive, "md5"),
            },
            "extracted": {
                "asset_id": "orthopedic.dataset.fracatlas.v6.extracted",
                "relative_path": "datasets/fracatlas/v6/extracted/FracAtlas",
                "dataset_index": {
                    "asset_id": "orthopedic.dataset.fracatlas.v6.dataset-index",
                    "relative_path": "dataset.csv",
                    "sha256": _digest(dataset_index),
                },
                "class_directories": {
                    "fractured": {
                        "relative_path": "images/Fractured",
                        "file_count": 1,
                    },
                    "non_fractured": {
                        "relative_path": "images/Non_fractured",
                        "file_count": 2,
                    },
                },
                "image_file_count": 3,
            },
        },
        "model": {
            "external_model_id": FRACATLAS_MODEL_EXTERNAL_ID,
            "asset_id": "orthopedic.model.medtrust-fracatlas-mnv3.v1",
            "linked_dataset_external_id": FRACATLAS_EXTERNAL_ID,
            "manifest": {
                "asset_id": "orthopedic.model.medtrust-fracatlas-mnv3.v1.manifest",
                "relative_path": (
                    "models/target-task/medtrust-fracatlas-mnv3/v1/"
                    "model_manifest.json"
                ),
                "sha256": _digest(model_manifest),
            },
        },
    }
    resource_root.mkdir(parents=True)
    _write_json(resource_root / LOCAL_MATERIALIZATION_RESOURCE, overlay)
    return asset_root, resource_root, weights


def test_asset_verification_rejects_tampered_referenced_weight(tmp_path: Path) -> None:
    asset_root, resource_root, weights = _asset_fixture(tmp_path)
    verified = verify_local_orthopedic_assets(
        asset_root=asset_root, resource_root=resource_root
    )
    assert verified.dataset["image_file_count"] == 3
    assert verified.evaluation["test"]["accuracy"] == 0.75

    weights.write_bytes(b"tampered-model-weights")
    with pytest.raises(OrthopedicMaterializationError, match="weights sha256 mismatch"):
        verify_local_orthopedic_assets(
            asset_root=asset_root, resource_root=resource_root
        )


def test_materialization_overlay_is_idempotent_and_preserves_history(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_materialization_overlay(tmp_path))


async def _assert_materialization_overlay(tmp_path: Path) -> None:
    asset_root, resource_root, _ = _asset_fixture(tmp_path)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"medtrust": None}},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        User.__table__,
        Organization.__table__,
        Space.__table__,
        ExternalCatalogSource.__table__,
        ExternalDatasetRecord.__table__,
        ExternalDatasetVersion.__table__,
        ExternalModelRecord.__table__,
        ExternalModelVersion.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection, tables=tables
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        user = User(
            identity_issuer="orthopedic-materialization-test",
            identity_subject="operator",
            display_name="Orthopedic Materialization Operator",
            status="active",
            is_demo=True,
        )
        session.add(user)
        await session.flush()
        organization = Organization(
            legal_name="Orthopedic Materialization Operator",
            display_name="Orthopedic Materialization Operator",
            organization_type="operator",
            verification_status="verified",
            status="active",
            is_demo=True,
            created_by=user.id,
        )
        session.add(organization)
        await session.flush()
        space = Space(
            code="ORTHOPEDIC-MATERIALIZATION-TEST",
            name="Orthopedic Materialization Test Space",
            space_type="industry",
            operator_organization_id=organization.id,
            status="active",
            ruleset_version="test-v1",
            classification_scheme_version="test-v1",
            default_retention_policy={"days": 1},
            is_demo=True,
            created_by=user.id,
        )
        session.add(space)
        await session.flush()
        await ensure_orthopedic_catalog_seed(session, space_id=space.id)

        first = await materialize_local_orthopedic_assets(
            session,
            space_id=space.id,
            asset_root=asset_root,
            resource_root=resource_root,
        )
        second = await materialize_local_orthopedic_assets(
            session,
            space_id=space.id,
            asset_root=asset_root,
            resource_root=resource_root,
        )
        assert (first.dataset_outcome, first.model_outcome) == ("updated", "inserted")
        assert (second.dataset_outcome, second.model_outcome) == (
            "unchanged",
            "unchanged",
        )
        assert first.dataset_version_id == second.dataset_version_id
        assert first.model_version_id == second.model_version_id

        data_source_id = await session.scalar(
            select(ExternalCatalogSource.id).where(
                ExternalCatalogSource.source_code == DATASET_SOURCE_CODE
            )
        )
        dataset = await session.scalar(
            select(ExternalDatasetRecord).where(
                ExternalDatasetRecord.source_id == data_source_id,
                ExternalDatasetRecord.external_id == FRACATLAS_EXTERNAL_ID,
            )
        )
        assert dataset is not None
        assert await session.scalar(
            select(func.count())
            .select_from(ExternalDatasetVersion)
            .where(ExternalDatasetVersion.record_id == dataset.id)
        ) == 2
        dataset_version = await session.get(
            ExternalDatasetVersion, dataset.current_version_id
        )
        assert dataset_version is not None
        data_profile = dataset_version.normalized_payload["medtrust_profile"]
        assert data_profile["catalog_stage"] == "static_candidate"
        assert data_profile["materialization_status"] == "materialized"
        assert data_profile["asset_residency"] == "local_verified"
        assert data_profile["application_eligible"] is False
        assert data_profile["executor_registered"] is False
        assert data_profile["can_execute"] is False

        model_source_id = await session.scalar(
            select(ExternalCatalogSource.id).where(
                ExternalCatalogSource.source_code == MODEL_SOURCE_CODE
            )
        )
        model = await session.scalar(
            select(ExternalModelRecord).where(
                ExternalModelRecord.source_id == model_source_id,
                ExternalModelRecord.external_model_id == FRACATLAS_MODEL_EXTERNAL_ID,
            )
        )
        assert model is not None
        assert model.execution_status == "not_materialized"
        assert await session.scalar(
            select(func.count())
            .select_from(ExternalModelVersion)
            .where(ExternalModelVersion.record_id == model.id)
        ) == 1
        model_version = await session.get(ExternalModelVersion, model.current_version_id)
        assert model_version is not None
        model_profile = model_version.normalized_payload["medtrust_profile"]
        assert model_profile["catalog_stage"] == "static_candidate"
        assert model_profile["materialization_status"] == "materialized"
        assert model_profile["execution_readiness"] == "validation_ready"
        assert model_profile["application_eligible"] is False
        assert model_profile["executor_registered"] is False
        assert model_profile["can_execute"] is False
        serialized = json.dumps(
            {
                "dataset": dataset_version.normalized_payload,
                "model": model_version.normalized_payload,
            },
            sort_keys=True,
        )
        assert str(tmp_path) not in serialized
        assert "relative_path" not in serialized

        await ensure_orthopedic_catalog_seed(session, space_id=space.id)
        restored = await materialize_local_orthopedic_assets(
            session,
            space_id=space.id,
            asset_root=asset_root,
            resource_root=resource_root,
        )
        assert restored.dataset_outcome == "updated"
        assert restored.dataset_version_id == first.dataset_version_id
        assert await session.scalar(
            select(func.count())
            .select_from(ExternalDatasetVersion)
            .where(ExternalDatasetVersion.record_id == dataset.id)
        ) == 2

    await engine.dispose()
