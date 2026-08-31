import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.catalog import (
    CatalogInvariantError,
    DataProduct,
    DataProductPublication,
    DataProductVersion,
    DataResource,
    add_product_source,
    approve_version,
    publish_version,
    submit_version_for_review,
    withdraw_publication,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole


def test_catalog_graph_can_complete_publication_lifecycle() -> None:
    asyncio.run(_complete_publication_lifecycle())


def test_duplicate_version_number_is_rejected() -> None:
    asyncio.run(_reject_duplicate_version_number())


def test_duplicate_version_label_is_rejected() -> None:
    asyncio.run(_reject_duplicate_version_label())


def test_cross_space_product_version_is_rejected() -> None:
    asyncio.run(_reject_cross_space_version())


def test_under_review_version_cannot_be_edited_in_place() -> None:
    asyncio.run(_reject_under_review_edit())


def test_draft_version_cannot_be_published() -> None:
    asyncio.run(_reject_draft_publication())


def test_source_connector_must_match_product_space() -> None:
    asyncio.run(_reject_cross_space_source())


def test_draft_version_cleanup_uses_database_cascade() -> None:
    asyncio.run(_delete_draft_version_graph())


def test_approved_version_cannot_be_deleted() -> None:
    asyncio.run(_reject_approved_version_delete())


def make_engine():
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

    return engine


async def create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def seed_catalog_context(session):
    seed_suffix = uuid4().hex
    user = User(
        identity_issuer="demo-local",
        identity_subject=f"catalog-admin-{seed_suffix}",
        display_name="Catalog 演示管理员",
        status="active",
        is_demo=True,
    )
    operator = Organization(
        legal_name="Catalog 测试空间运营机构（演示）",
        display_name="Catalog 测试运营机构（演示）",
        organization_type="operator",
        verification_status="verified",
        status="active",
        is_demo=True,
    )
    provider = Organization(
        legal_name="数字病理数据提供机构（演示）",
        display_name="数字病理数据提供机构（演示）",
        organization_type="hospital",
        verification_status="verified",
        status="active",
        is_demo=True,
    )
    session.add_all([user, operator, provider])
    await session.flush()
    operator.created_by = user.id
    provider.created_by = user.id

    first_space = Space(
        code=f"CATALOG-PATHOLOGY-{seed_suffix}",
        name="数字病理 Catalog 测试空间（演示）",
        space_type="industry",
        operator_organization=operator,
        status="active",
        ruleset_version="rules-v1",
        classification_scheme_version="classification-v1",
        default_retention_policy={"retention_days": 90},
        is_demo=True,
        created_by=user.id,
    )
    second_space = Space(
        code=f"CATALOG-IMAGING-{seed_suffix}",
        name="医学影像 Catalog 测试空间（演示）",
        space_type="industry",
        operator_organization=operator,
        status="active",
        ruleset_version="rules-v1",
        classification_scheme_version="classification-v1",
        default_retention_policy={"retention_days": 90},
        is_demo=True,
        created_by=user.id,
    )
    session.add_all([first_space, second_space])
    await session.flush()

    for space in (first_space, second_space):
        participant = SpaceParticipant(
            space=space,
            organization=provider,
            admission_status="admitted",
            ruleset_accepted_version="rules-v1",
            admitted_at=datetime.now(timezone.utc),
            created_by=user.id,
        )
        participant.roles.append(
            SpaceParticipantRole(role_code="provider", granted_by=user.id)
        )
        session.add(participant)

    connector = make_connector(
        space=first_space,
        provider=provider,
        user_id=user.id,
        external_id="catalog-pathology-node",
        name="数字病理产品连接器（演示）",
    )
    connector.capabilities.append(
        ConnectorCapability(
            capability_code="product_publish",
            capability_version="1.0",
            status="verified",
            parameters={"formats": ["DICOM-WSI"]},
            verified_at=datetime.now(timezone.utc),
        )
    )
    second_connector = make_connector(
        space=second_space,
        provider=provider,
        user_id=user.id,
        external_id="catalog-imaging-node",
        name="医学影像产品连接器（演示）",
    )
    second_connector.capabilities.append(
        ConnectorCapability(
            capability_code="product_publish",
            capability_version="1.0",
            status="verified",
            parameters={"formats": ["DICOM"]},
            verified_at=datetime.now(timezone.utc),
        )
    )
    session.add_all([connector, second_connector])
    await session.flush()
    return user, provider, first_space, second_space, connector, second_connector


def make_connector(
    *,
    space: Space,
    provider: Organization,
    user_id,
    external_id: str,
    name: str,
) -> Connector:
    return Connector(
        space=space,
        owner_organization=provider,
        external_connector_id=external_id,
        name=name,
        verification_status="verified",
        runtime_status="online",
        endpoint_metadata={"protocol": "https", "endpoint_ref": "demo-node"},
        certificate_fingerprint=f"fingerprint-{external_id}",
        last_heartbeat_at=datetime.now(timezone.utc),
        is_demo=True,
        created_by=user_id,
    )


def make_product(*, space: Space, provider: Organization, user_id) -> DataProduct:
    return DataProduct(
        space_id=space.id,
        provider_organization_id=provider.id,
        product_code="NPC-PATHOLOGY-DEMO",
        name="鼻咽癌数字病理多模态研究数据产品（演示）",
        description="用于验证 Catalog 领域关系的合成数据产品。",
        product_type="controlled_compute",
        domain="digital_pathology",
        lifecycle_status="draft",
        is_demo=True,
        created_by=user_id,
    )


def make_version(
    *,
    product: DataProduct,
    space: Space,
    user_id,
    version_no: int = 1,
    version_label: str = "v1.0",
    snapshot_digest: str = "sha256:version-v1",
) -> DataProductVersion:
    return DataProductVersion(
        space_id=space.id,
        data_product_id=product.id,
        version_no=version_no,
        version_label=version_label,
        status="draft",
        content_summary="WSI、临床变量与随访结果的演示版本。",
        scope_metadata={"schema_version": "1.0", "case_count": 1000},
        linkage_metadata={"schema_version": "1.0", "method": "anonymous_case_key"},
        quality_report={"schema_version": "1.0", "completeness": 0.98},
        classification_level="sensitive_personal_information",
        default_use_mode="controlled_compute",
        default_policy_template={
            "schema_version": "1.0",
            "permit": ["model_training"],
            "deny": ["raw_export"],
        },
        default_policy_digest="sha256:policy-v1",
        provenance_summary={"schema_version": "1.0", "deidentified": True},
        snapshot_digest=snapshot_digest,
        created_by=user_id,
    )


def make_resource(
    *, version: DataProductVersion, space: Space, user_id
) -> DataResource:
    return DataResource(
        space_id=space.id,
        data_product_version_id=version.id,
        resource_code="wsi-he",
        name="HE 全切片图像集合（演示）",
        resource_type="image_collection",
        modality="wsi",
        format="DICOM-WSI",
        schema_metadata={"schema_version": "1.0", "fields": ["image"]},
        scope_metadata={"schema_version": "1.0", "slide_count": 1200},
        quality_report={"schema_version": "1.0", "usable_rate": 0.97},
        classification_level="sensitive_personal_information",
        resource_digest="sha256:resource-wsi-v1",
        position_no=1,
        created_by=user_id,
    )


async def create_catalog_graph(session):
    user, provider, first_space, second_space, connector, second_connector = (
        await seed_catalog_context(session)
    )
    product = make_product(
        space=first_space, provider=provider, user_id=user.id
    )
    session.add(product)
    await session.flush()
    version = make_version(product=product, space=first_space, user_id=user.id)
    session.add(version)
    await session.flush()
    resource = make_resource(version=version, space=first_space, user_id=user.id)
    session.add(resource)
    await session.flush()
    await add_product_source(
        session,
        resource,
        connector,
        local_resource_alias="npc_wsi_snapshot_v1",
        source_digest="sha256:source-wsi-v1",
        source_role="primary",
        source_snapshot_at=datetime.now(timezone.utc),
    )
    return (
        user,
        product,
        version,
        resource,
        first_space,
        second_space,
        second_connector,
    )


async def _complete_publication_lifecycle() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, _, _, _ = await create_catalog_graph(session)
        await submit_version_for_review(session, version)
        await approve_version(session, version, approved_by=user.id)
        publication = await publish_version(
            session,
            product,
            version,
            published_by=user.id,
            visibility="space",
        )
        await session.commit()
        assert product.lifecycle_status == "active"
        assert publication.status == "active"

        await withdraw_publication(
            session,
            publication,
            withdrawn_by=user.id,
            reason="切换至下一产品版本（演示）",
        )
        await session.commit()

    async with session_factory() as session:
        stored = await session.scalar(
            select(DataProduct)
            .where(DataProduct.product_code == "NPC-PATHOLOGY-DEMO")
            .options(
                selectinload(DataProduct.versions),
                selectinload(DataProduct.publications),
            )
        )
        assert stored is not None
        assert [version.status for version in stored.versions] == ["approved"]
        assert [publication.status for publication in stored.publications] == ["withdrawn"]

    await engine.dispose()


async def _reject_duplicate_version_number() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, _, _, first_space, _, _ = await create_catalog_graph(session)
        session.add(
            make_version(
                product=product,
                space=first_space,
                user_id=user.id,
                version_no=1,
                version_label="v1.1",
                snapshot_digest="sha256:duplicate-number",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
    await engine.dispose()


async def _reject_duplicate_version_label() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, _, _, first_space, _, _ = await create_catalog_graph(session)
        session.add(
            make_version(
                product=product,
                space=first_space,
                user_id=user.id,
                version_no=2,
                version_label="v1.0",
                snapshot_digest="sha256:duplicate-label",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
    await engine.dispose()


async def _reject_cross_space_version() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, _, _, _, second_space, _ = await create_catalog_graph(session)
        session.add(
            make_version(
                product=product,
                space=second_space,
                user_id=user.id,
                version_no=2,
                version_label="v2.0",
                snapshot_digest="sha256:cross-space-version",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
    await engine.dispose()


async def _reject_under_review_edit() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        _, _, version, _, _, _, _ = await create_catalog_graph(session)
        await submit_version_for_review(session, version)
        await session.commit()
        version.content_summary = "审核中被篡改的摘要"
        with pytest.raises(CatalogInvariantError):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _reject_draft_publication() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, _, _, _ = await create_catalog_graph(session)
        with pytest.raises(CatalogInvariantError, match="approved"):
            await publish_version(
                session,
                product,
                version,
                published_by=user.id,
                visibility="space",
            )
        await session.rollback()
    await engine.dispose()


async def _reject_cross_space_source() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        _, _, version, _, first_space, _, second_connector = await create_catalog_graph(
            session
        )
        second_resource = DataResource(
            space_id=first_space.id,
            data_product_version_id=version.id,
            resource_code="clinical-core",
            name="临床变量表（演示）",
            resource_type="tabular",
            modality="clinical",
            format="Parquet",
            schema_metadata={"schema_version": "1.0"},
            scope_metadata={"schema_version": "1.0"},
            quality_report={"schema_version": "1.0"},
            classification_level="sensitive_personal_information",
            resource_digest="sha256:resource-clinical-v1",
            position_no=2,
            created_by=version.created_by,
        )
        session.add(second_resource)
        await session.flush()
        with pytest.raises(CatalogInvariantError, match="product space"):
            await add_product_source(
                session,
                second_resource,
                second_connector,
                local_resource_alias="cross_space_clinical",
                source_digest="sha256:cross-space-source",
                source_role="primary",
                source_snapshot_at=datetime.now(timezone.utc),
            )
        await session.rollback()
    await engine.dispose()


async def _delete_draft_version_graph() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        _, _, version, _, _, _, _ = await create_catalog_graph(session)
        version_id = version.id
        await session.commit()
        await session.delete(version)
        await session.commit()

    async with session_factory() as session:
        assert await session.get(DataProductVersion, version_id) is None
        assert await session.scalar(select(func.count()).select_from(DataResource)) == 0
    await engine.dispose()


async def _reject_approved_version_delete() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, _, version, _, _, _, _ = await create_catalog_graph(session)
        await submit_version_for_review(session, version)
        await approve_version(session, version, approved_by=user.id)
        await session.commit()
        await session.delete(version)
        with pytest.raises(CatalogInvariantError, match="draft"):
            await session.flush()
        await session.rollback()
    await engine.dispose()
