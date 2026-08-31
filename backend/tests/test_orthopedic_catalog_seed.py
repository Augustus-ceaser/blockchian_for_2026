from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.routes.external_model_catalog import _model_source
from app.db.base import Base
from app.modules.external_catalog.model_services import SOURCE_CODE as MODEL_SOURCE_CODE
from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalDatasetRecord,
    ExternalDatasetVersion,
    ExternalModelRecord,
    ExternalModelVersion,
)
from app.modules.external_catalog.orthopedic_seed import (
    CATALOG_RESOURCE,
    DATASET_SOURCE_CODE,
    EXPECTED_DATASET_IDS,
    EXPECTED_TARGET_WEIGHT_IDS,
    EXPECTED_TEMPLATE_IDS,
    MODEL_SOURCE_CODE as ORTHOPEDIC_MODEL_SOURCE_CODE,
    RESOURCE_ROOT,
    OrthopedicCatalogSeedError,
    ensure_orthopedic_catalog_seed,
    load_orthopedic_catalog_seed,
)
from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space


def test_static_catalog_has_exact_scope_and_honest_boundaries() -> None:
    catalog = load_orthopedic_catalog_seed()
    assert {item["external_id"] for item in catalog["datasets"]} == EXPECTED_DATASET_IDS
    assert {
        item["external_model_id"]
        for item in catalog["models"]
        if item["medtrust_profile"]["asset_kind"] == "target_task_weights"
    } == EXPECTED_TARGET_WEIGHT_IDS
    assert {
        item["external_model_id"]
        for item in catalog["models"]
        if item["medtrust_profile"]["asset_kind"] == "algorithm_template"
    } == EXPECTED_TEMPLATE_IDS

    for item in catalog["datasets"]:
        profile = item["medtrust_profile"]
        assert profile["materialization_status"] == "not_materialized"
        assert profile["execution_readiness"] == "not_ready"
        assert profile["executor_registered"] is False
        assert profile["platform_validation"] == "not_validated"
        assert profile["application_eligible"] is False
        assert profile["can_execute"] is False

    for item in catalog["models"]:
        profile = item["medtrust_profile"]
        assert item["execution_status"] == "not_materialized"
        assert profile["materialization_status"] == "not_materialized"
        assert profile["execution_readiness"] == "not_ready"
        assert profile["executor_registered"] is False
        assert profile["platform_validation"] == "not_validated"
        assert profile["can_execute"] is False
        if profile["asset_kind"] == "algorithm_template":
            assert profile["target_task_weights"] is False
            assert "not_target_task_weights" in " ".join(item["quality_flags"])
        else:
            assert profile["target_task_weights"] is True


def test_catalog_rejects_any_executable_claim(tmp_path: Path) -> None:
    document = json.loads((RESOURCE_ROOT / CATALOG_RESOURCE).read_text(encoding="utf-8"))
    unsafe = deepcopy(document)
    unsafe["models"][0]["medtrust_profile"]["can_execute"] = True
    (tmp_path / CATALOG_RESOURCE).write_text(
        json.dumps(unsafe, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(OrthopedicCatalogSeedError, match="boundary is unsafe"):
        load_orthopedic_catalog_seed(resource_root=tmp_path)


def test_static_seed_does_not_read_runtime_or_registered_assets() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app/modules/external_catalog/orthopedic_seed.py"
    ).read_text(encoding="utf-8")
    assert ".runtime" not in source
    assert "registered_assets" not in source


def test_seed_is_idempotent_and_keeps_embedded_sources_queryable() -> None:
    asyncio.run(_assert_seed_is_idempotent())


async def _assert_seed_is_idempotent() -> None:
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
            identity_issuer="orthopedic-seed-test",
            identity_subject="operator",
            display_name="Orthopedic Seed Operator",
            status="active",
            is_demo=True,
        )
        session.add(user)
        await session.flush()
        organization = Organization(
            legal_name="Orthopedic Seed Test Operator",
            display_name="Orthopedic Seed Test Operator",
            organization_type="operator",
            verification_status="verified",
            status="active",
            is_demo=True,
            created_by=user.id,
        )
        session.add(organization)
        await session.flush()
        space = Space(
            code="ORTHOPEDIC-SEED-TEST",
            name="Orthopedic Seed Test Space",
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

        first = await ensure_orthopedic_catalog_seed(session, space_id=space.id)
        second = await ensure_orthopedic_catalog_seed(session, space_id=space.id)
        assert (first.datasets_inserted, first.models_inserted) == (4, 7)
        assert (second.datasets_inserted, second.models_inserted) == (0, 0)
        assert (second.datasets_unchanged, second.models_unchanged) == (4, 7)
        assert first.catalog_digest == second.catalog_digest
        assert second.fracatlas_materialization_status == "not_materialized"

        sources = list(
            (
                await session.scalars(
                    select(ExternalCatalogSource).where(
                        ExternalCatalogSource.source_code.in_(
                            [DATASET_SOURCE_CODE, ORTHOPEDIC_MODEL_SOURCE_CODE]
                        )
                    )
                )
            ).all()
        )
        assert len(sources) == 2
        assert all(source.enabled is True for source in sources)
        assert all(source.source_type == "embedded_static_catalog" for source in sources)
        assert await session.scalar(
            select(func.count()).select_from(ExternalDatasetRecord)
        ) == 4
        assert await session.scalar(
            select(func.count()).select_from(ExternalDatasetVersion)
        ) == 4
        assert await session.scalar(
            select(func.count()).select_from(ExternalModelRecord)
        ) == 7
        assert await session.scalar(
            select(func.count()).select_from(ExternalModelVersion)
        ) == 7

        visible_dataset_count = await session.scalar(
            select(func.count())
            .select_from(ExternalDatasetRecord)
            .join(
                ExternalDatasetVersion,
                ExternalDatasetVersion.id == ExternalDatasetRecord.current_version_id,
            )
            .join(
                ExternalCatalogSource,
                ExternalCatalogSource.id == ExternalDatasetRecord.source_id,
            )
            .where(
                ExternalCatalogSource.space_id == space.id,
                ExternalCatalogSource.enabled.is_(True),
                ExternalDatasetRecord.status == "active",
                ExternalDatasetVersion.is_current.is_(True),
            )
        )
        visible_model_count = await session.scalar(
            select(func.count())
            .select_from(ExternalModelRecord)
            .join(
                ExternalModelVersion,
                ExternalModelVersion.id == ExternalModelRecord.current_version_id,
            )
            .join(
                ExternalCatalogSource,
                ExternalCatalogSource.id == ExternalModelRecord.source_id,
            )
            .where(
                ExternalCatalogSource.space_id == space.id,
                ExternalCatalogSource.enabled.is_(True),
                ExternalModelRecord.status == "active",
                ExternalModelVersion.is_current.is_(True),
            )
        )
        assert (visible_dataset_count, visible_model_count) == (4, 7)

        model_rows = list((await session.scalars(select(ExternalModelRecord))).all())
        for row in model_rows:
            assert row.execution_status == "not_materialized"
            version = await session.get(ExternalModelVersion, row.current_version_id)
            assert version is not None and version.is_current is True
            profile = version.normalized_payload["medtrust_profile"]
            assert profile["executor_registered"] is False
            assert profile["platform_validation"] == "not_validated"
            assert profile["can_execute"] is False

        configured_source = ExternalCatalogSource(
            space_id=space.id,
            source_code=MODEL_SOURCE_CODE,
            display_name="Configured model source",
            base_url="https://example.invalid/api/v1",
            source_type="versioned_rest_model_catalog",
            resource_kind="model",
            auth_mode="none",
            enabled=True,
            expected_schema_version="1.0",
            status="ready",
        )
        session.add(configured_source)
        await session.flush()
        assert (await _model_source(session, space.id)).id == configured_source.id

    await engine.dispose()
