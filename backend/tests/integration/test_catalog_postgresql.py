from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.catalog import (
    CatalogInvariantError,
    DataProduct,
    DataProductPublication,
    DataProductSource,
    DataProductVersion,
    DataResource,
    add_product_source,
    approve_version,
    publish_version,
    submit_version_for_review,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")
RUN_CONCURRENCY_TEST = os.getenv("MEDTRUST_RUN_CATALOG_CONCURRENCY_TEST") == "1"

CATALOG_TABLES = {
    "data_products",
    "data_product_versions",
    "data_resources",
    "product_sources",
    "data_product_publications",
}
CATALOG_TRIGGERS = {
    "trg_product_version_immutable",
    "trg_catalog_resource_draft",
    "trg_catalog_source_draft",
    "trg_catalog_publication",
}
CATALOG_FUNCTIONS = {
    "guard_product_version_immutable",
    "guard_catalog_resource_draft",
    "guard_catalog_source_draft",
    "guard_catalog_publication",
}
CATALOG_COMPOSITE_FOREIGN_KEYS = {
    "fk_product_versions_space_product",
    "fk_data_resources_space_version",
    "fk_publications_space_product",
    "fk_publications_product_version",
}
CATALOG_CHECKS = {
    "ck_data_product_versions_version_no_positive",
    "ck_data_product_versions_status",
    "ck_data_product_versions_snapshot_required_after_draft",
    "ck_data_product_publications_status",
    "ck_data_product_publications_visibility",
}
CATALOG_PARTIAL_UNIQUE_INDEXES = {
    "uq_publications_active_product",
    "uq_publications_active_version",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


@dataclass
class CatalogFixture:
    user: User
    provider: Organization
    space: Space
    connector: Connector
    product: DataProduct
    versions: list[DataProductVersion]
    resources: list[DataResource]
    sources: list[DataProductSource]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_catalog_schema_objects_exist_on_migrated_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_catalog_schema_objects(TEST_DATABASE_URL))


def test_catalog_foreign_keys_and_checks_reject_invalid_rows() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_foreign_keys_and_checks(TEST_DATABASE_URL))


def test_catalog_jsonb_round_trip_and_service_schema_validation() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_jsonb_and_service_schema_validation(TEST_DATABASE_URL))


def test_catalog_plpgsql_guards_reject_direct_sql_tampering() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_plpgsql_guards(TEST_DATABASE_URL))


def test_catalog_partial_unique_indexes_allow_only_one_active_publication() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_active_publication_uniqueness(TEST_DATABASE_URL))


@pytest.mark.skipif(
    not RUN_CONCURRENCY_TEST,
    reason="set MEDTRUST_RUN_CATALOG_CONCURRENCY_TEST=1 for the destructive race test",
)
def test_concurrent_publication_attempts_have_one_winner() -> None:
    """Run only against a disposable database; this test commits seed and winner rows."""

    assert TEST_DATABASE_URL is not None
    run(_assert_concurrent_publication_race(TEST_DATABASE_URL))


async def _assert_catalog_schema_objects(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "20260725_0032"

            tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            assert CATALOG_TABLES <= tables

            triggers = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tg.tgname "
                            "FROM pg_catalog.pg_trigger tg "
                            "JOIN pg_catalog.pg_class c ON c.oid = tg.tgrelid "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'medtrust' AND NOT tg.tgisinternal"
                        )
                    )
                ).all()
            )
            assert CATALOG_TRIGGERS <= triggers

            functions = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT p.proname "
                            "FROM pg_catalog.pg_proc p "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                            "WHERE n.nspname = 'medtrust'"
                        )
                    )
                ).all()
            )
            assert CATALOG_FUNCTIONS <= functions

            constraints = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT con.conname "
                            "FROM pg_catalog.pg_constraint con "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = con.connamespace "
                            "WHERE n.nspname = 'medtrust'"
                        )
                    )
                ).all()
            )
            assert CATALOG_COMPOSITE_FOREIGN_KEYS <= constraints
            assert CATALOG_CHECKS <= constraints

            partial_unique_indexes = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT indexname FROM pg_catalog.pg_indexes "
                            "WHERE schemaname = 'medtrust' "
                            "AND indexdef LIKE 'CREATE UNIQUE INDEX%' "
                            "AND indexdef LIKE '%WHERE%status%active%'"
                        )
                    )
                ).all()
            )
            assert CATALOG_PARTIAL_UNIQUE_INDEXES <= partial_unique_indexes
    finally:
        await engine.dispose()


async def _assert_foreign_keys_and_checks(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture = await _create_catalog_fixture(session)
            second_space = Space(
                code=f"CATALOG-OTHER-{uuid4().hex}",
                name="Catalog PostgreSQL 鍏朵粬绌洪棿",
                space_type="industry",
                operator_organization_id=fixture.provider.id,
                status="active",
                ruleset_version="rules-v1",
                classification_scheme_version="classification-v1",
                default_retention_policy={"retention_days": 90},
                is_demo=True,
                created_by=fixture.user.id,
            )
            session.add(second_space)
            await session.flush()

            await _expect_db_rejection(
                session,
                update(DataProduct)
                .where(DataProduct.id == fixture.product.id)
                .values(lifecycle_status="invalid"),
                "ck_data_products_lifecycle_status",
            )
            await _expect_db_rejection(
                session,
                insert(DataProductVersion).values(
                    id=uuid4(),
                    space_id=second_space.id,
                    data_product_id=fixture.product.id,
                    version_no=2,
                    version_label="v2.0-wrong-space",
                    status="draft",
                    content_summary="Cross-space invalid version.",
                    scope_metadata={},
                    linkage_metadata={},
                    quality_report={},
                    classification_level="sensitive_personal_information",
                    default_use_mode="controlled_compute",
                    default_policy_template={},
                    default_policy_digest=f"sha256:policy-{uuid4().hex}",
                    provenance_summary={},
                    snapshot_digest=None,
                    created_by=fixture.user.id,
                ),
                "fk_product_versions_space_product",
            )
            await _expect_db_rejection(
                session,
                insert(DataProductVersion).values(
                    id=uuid4(),
                    space_id=fixture.space.id,
                    data_product_id=fixture.product.id,
                    version_no=0,
                    version_label="v0-invalid",
                    status="draft",
                    content_summary="Invalid version number.",
                    scope_metadata={},
                    linkage_metadata={},
                    quality_report={},
                    classification_level="sensitive_personal_information",
                    default_use_mode="controlled_compute",
                    default_policy_template={},
                    default_policy_digest=f"sha256:policy-{uuid4().hex}",
                    provenance_summary={},
                    snapshot_digest=None,
                    created_by=fixture.user.id,
                ),
                "ck_data_product_versions_version_no_positive",
            )
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _assert_jsonb_and_service_schema_validation(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture = await _create_catalog_fixture(session)
            version = fixture.versions[0]
            version.scope_metadata = {
                "schema_version": "1.0",
                "modalities": ["WSI", "clinical"],
                "case_count": 1000,
            }
            await session.flush()
            stored = await session.scalar(
                select(DataProductVersion.scope_metadata).where(
                    DataProductVersion.id == version.id
                )
            )
            assert stored == version.scope_metadata
            database_type = await session.scalar(
                text(
                    "SELECT pg_typeof(scope_metadata)::text "
                    "FROM medtrust.data_product_versions WHERE id = :version_id"
                ),
                {"version_id": version.id},
            )
            assert database_type == "jsonb"

            version.scope_metadata = {"schema_version": 1}
            with pytest.raises(CatalogInvariantError, match="scope_metadata"):
                await submit_version_for_review(session, version)
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _assert_plpgsql_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture = await _create_catalog_fixture(session)
            version = fixture.versions[0]
            resource = fixture.resources[0]
            source = fixture.sources[0]

            await submit_version_for_review(session, version)
            await _expect_db_rejection(
                session,
                update(DataProductVersion)
                .where(DataProductVersion.id == version.id)
                .values(content_summary="under_review direct SQL tamper"),
                "invalid product version transition: under_review -> under_review",
            )
            await _expect_db_rejection(
                session,
                update(DataResource)
                .where(DataResource.id == resource.id)
                .values(name="under_review resource tamper"),
                "resources can only change in a draft version",
            )
            await _expect_db_rejection(
                session,
                update(DataProductSource)
                .where(
                    DataProductSource.data_resource_id == source.data_resource_id,
                    DataProductSource.connector_id == source.connector_id,
                    DataProductSource.local_resource_alias == source.local_resource_alias,
                )
                .values(source_digest="sha256:source-tamper"),
                "sources can only change in a draft version",
            )
            await _expect_db_rejection(
                session,
                insert(DataProductPublication).values(
                    id=uuid4(),
                    space_id=fixture.space.id,
                    data_product_id=fixture.product.id,
                    data_product_version_id=version.id,
                    status="active",
                    visibility="space",
                    published_by=fixture.user.id,
                ),
                "only an approved version can create an active publication",
            )

            await approve_version(session, version, approved_by=fixture.user.id)
            await _expect_db_rejection(
                session,
                update(DataProductVersion)
                .where(DataProductVersion.id == version.id)
                .values(snapshot_digest="sha256:approved-tamper"),
                "approved product version",
            )
            publication = await publish_version(
                session,
                fixture.product,
                version,
                published_by=fixture.user.id,
                visibility="space",
            )
            await _expect_db_rejection(
                session,
                update(DataProductPublication)
                .where(DataProductPublication.id == publication.id)
                .values(
                    status="withdrawn",
                    visibility="restricted",
                    withdrawn_at=datetime.now(timezone.utc),
                    withdrawn_by=fixture.user.id,
                    withdrawal_reason="direct SQL identity tamper",
                ),
                "publication identity and visibility are immutable",
            )
            await _expect_db_rejection(
                session,
                DataProductPublication.__table__.delete().where(
                    DataProductPublication.id == publication.id
                ),
                "publication history cannot be deleted",
            )
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _assert_active_publication_uniqueness(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture = await _create_catalog_fixture(session, version_count=2, approve=True)
            await publish_version(
                session,
                fixture.product,
                fixture.versions[0],
                published_by=fixture.user.id,
                visibility="space",
            )
            await _expect_db_rejection(
                session,
                insert(DataProductPublication).values(
                    id=uuid4(),
                    space_id=fixture.space.id,
                    data_product_id=fixture.product.id,
                    data_product_version_id=fixture.versions[1].id,
                    status="active",
                    visibility="space",
                    published_by=fixture.user.id,
                ),
                "uq_publications_active_product",
                expected_error=IntegrityError,
            )
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _assert_concurrent_publication_race(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"
    try:
        async with AsyncSession(engine, expire_on_commit=False) as seed_session:
            fixture = await _create_catalog_fixture(seed_session, version_count=2, approve=True)
            await seed_session.commit()

        ready = asyncio.Event()

        async def publish(version_id: UUID) -> str:
            await ready.wait()
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        insert(DataProductPublication).values(
                            id=uuid4(),
                            space_id=fixture.space.id,
                            data_product_id=fixture.product.id,
                            data_product_version_id=version_id,
                            status="active",
                            visibility="space",
                            published_by=fixture.user.id,
                        )
                    )
                return "published"
            except IntegrityError:
                return "conflict"

        attempts = [
            asyncio.create_task(publish(fixture.versions[0].id)),
            asyncio.create_task(publish(fixture.versions[1].id)),
        ]
        ready.set()
        results = await asyncio.gather(*attempts)
        assert sorted(results) == ["conflict", "published"]
    finally:
        await engine.dispose()


async def _create_catalog_fixture(
    session: AsyncSession,
    *,
    version_count: int = 1,
    approve: bool = False,
) -> CatalogFixture:
    suffix = uuid4().hex
    user = User(
        identity_issuer="integration-test",
        identity_subject=f"catalog-user-{suffix}",
        display_name="Catalog PostgreSQL 闆嗘垚娴嬭瘯鐢ㄦ埛",
        status="active",
        is_demo=True,
    )
    provider = Organization(
        legal_name=f"Catalog PostgreSQL 闆嗘垚娴嬭瘯鏈烘瀯 {suffix}",
        display_name="Catalog PostgreSQL 闆嗘垚娴嬭瘯鏈烘瀯",
        organization_type="hospital",
        verification_status="verified",
        status="active",
        is_demo=True,
    )
    session.add_all([user, provider])
    await session.flush()
    provider.created_by = user.id

    space = Space(
        code=f"CATALOG-INTEGRATION-{suffix}",
        name="Catalog PostgreSQL 闆嗘垚娴嬭瘯绌洪棿",
        space_type="industry",
        operator_organization_id=provider.id,
        status="active",
        ruleset_version="rules-v1",
        classification_scheme_version="classification-v1",
        default_retention_policy={"retention_days": 90},
        is_demo=True,
        created_by=user.id,
    )
    connector = Connector(
        space=space,
        owner_organization=provider,
        external_connector_id=f"catalog-node-{suffix}",
        name="Catalog PostgreSQL 闆嗘垚娴嬭瘯鑺傜偣",
        verification_status="verified",
        runtime_status="online",
        endpoint_metadata={"protocol": "https"},
        is_demo=True,
        created_by=user.id,
    )
    connector.capabilities.append(
        ConnectorCapability(
            capability_code="product_publish",
            capability_version="1.0",
            status="verified",
            parameters={"mode": "integration-test"},
            verified_at=datetime.now(timezone.utc),
        )
    )
    session.add(connector)
    await session.flush()

    product = DataProduct(
        space_id=space.id,
        provider_organization_id=provider.id,
        product_code=f"NPC-CATALOG-{suffix}",
        name="Catalog PostgreSQL 婕旂ず浜у搧",
        description="Catalog migration integration verification.",
        product_type="controlled_compute",
        domain="digital_pathology",
        lifecycle_status="draft",
        is_demo=True,
        created_by=user.id,
    )
    session.add(product)
    await session.flush()

    versions: list[DataProductVersion] = []
    resources: list[DataResource] = []
    sources: list[DataProductSource] = []
    for version_no in range(1, version_count + 1):
        version = DataProductVersion(
            space_id=space.id,
            data_product_id=product.id,
            version_no=version_no,
            version_label=f"v{version_no}.0",
            status="draft",
            content_summary=f"Catalog PostgreSQL integration version {version_no}.",
            scope_metadata={"schema_version": "1.0"},
            linkage_metadata={"schema_version": "1.0"},
            quality_report={"schema_version": "1.0"},
            classification_level="sensitive_personal_information",
            default_use_mode="controlled_compute",
            default_policy_template={"schema_version": "1.0"},
            default_policy_digest=f"sha256:policy-{suffix}-{version_no}",
            provenance_summary={"schema_version": "1.0"},
            snapshot_digest=f"sha256:version-{suffix}-{version_no}",
            created_by=user.id,
        )
        session.add(version)
        await session.flush()
        resource = DataResource(
            space_id=space.id,
            data_product_version_id=version.id,
            resource_code="wsi-he",
            name="HE WSI 闆嗘垚娴嬭瘯璧勬簮",
            resource_type="image_collection",
            modality="wsi",
            format="DICOM-WSI",
            schema_metadata={"schema_version": "1.0"},
            scope_metadata={"schema_version": "1.0"},
            quality_report={"schema_version": "1.0"},
            classification_level="sensitive_personal_information",
            resource_digest=f"sha256:resource-{suffix}-{version_no}",
            position_no=1,
            created_by=user.id,
        )
        session.add(resource)
        await session.flush()
        source = await add_product_source(
            session,
            resource,
            connector,
            local_resource_alias=f"wsi-snapshot-{suffix}-{version_no}",
            source_digest=f"sha256:source-{suffix}-{version_no}",
            source_role="primary",
            source_snapshot_at=datetime.now(timezone.utc),
        )
        if approve:
            await submit_version_for_review(session, version)
            await approve_version(session, version, approved_by=user.id)
        versions.append(version)
        resources.append(resource)
        sources.append(source)

    return CatalogFixture(
        user=user,
        provider=provider,
        space=space,
        connector=connector,
        product=product,
        versions=versions,
        resources=resources,
        sources=sources,
    )


async def _expect_db_rejection(
    session: AsyncSession,
    statement: object,
    message_fragment: str,
    *,
    expected_error: type[DBAPIError] = DBAPIError,
) -> None:
    savepoint = await session.begin_nested()
    caught: pytest.ExceptionInfo[DBAPIError]
    try:
        with pytest.raises(expected_error) as caught:
            await session.execute(statement)  # type: ignore[arg-type]
    finally:
        if savepoint.is_active:
            await savepoint.rollback()
    assert message_fragment in str(caught.value.orig)

