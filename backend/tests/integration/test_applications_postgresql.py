from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.applications import (
    Application,
    ApplicationAttachment,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
    ApplicationSnapshot,
    submit_application,
)
from app.modules.catalog import publish_version
from app.modules.identity.models import Organization
from tests.integration.test_catalog_postgresql import _create_catalog_fixture

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

APPLICATION_TABLES = {
    "application_attachments",
    "applications",
    "application_items",
    "application_requested_actions",
    "application_requested_output_types",
    "application_snapshots",
}
APPLICATION_TRIGGERS = {
    "trg_application_action_draft",
    "trg_application_attachment_draft",
    "trg_application_lifecycle",
    "trg_application_item_draft",
    "trg_application_output_draft",
    "trg_application_snapshot_immutable",
    "trg_application_requires_snapshot",
}
APPLICATION_FUNCTIONS = {
    "guard_application_component_draft",
    "guard_application_lifecycle",
    "guard_application_item_draft",
    "guard_application_snapshot_immutable",
    "require_application_snapshot",
}
APPLICATION_COMPOSITE_FOREIGN_KEYS = {
    "fk_application_items_application_scope",
    "fk_application_items_product_provider",
    "fk_application_items_product_version",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_application_schema_objects_exist_on_migrated_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_application_schema_objects(TEST_DATABASE_URL))


def test_application_composite_foreign_keys_reject_scope_mismatch() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_application_scope_constraints(TEST_DATABASE_URL))


def test_application_snapshot_trigger_rejects_direct_sql_tampering() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_snapshot_trigger(TEST_DATABASE_URL))


def test_application_extension_constraints_and_guard() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_extension_constraints_and_guard(TEST_DATABASE_URL))


async def _assert_application_schema_objects(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20260725_0032"
            )
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
            assert APPLICATION_TABLES <= tables

            triggers = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tg.tgname FROM pg_catalog.pg_trigger tg "
                            "JOIN pg_catalog.pg_class c ON c.oid = tg.tgrelid "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'medtrust' AND NOT tg.tgisinternal"
                        )
                    )
                ).all()
            )
            assert APPLICATION_TRIGGERS <= triggers

            functions = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT p.proname FROM pg_catalog.pg_proc p "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                            "WHERE n.nspname = 'medtrust'"
                        )
                    )
                ).all()
            )
            assert APPLICATION_FUNCTIONS <= functions

            constraints = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT con.conname FROM pg_catalog.pg_constraint con "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = con.connamespace "
                            "WHERE n.nspname = 'medtrust'"
                        )
                    )
                ).all()
            )
            assert APPLICATION_COMPOSITE_FOREIGN_KEYS <= constraints
            assert "uq_data_products_space_provider_id_pair" in constraints
    finally:
        await engine.dispose()


async def _assert_application_scope_constraints(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture = await _create_catalog_fixture(session, approve=True)
            consumer = await _create_consumer(session, fixture.user.id)
            other_provider = Organization(
                legal_name=f"Application PostgreSQL wrong provider {uuid4().hex}",
                display_name="Application PostgreSQL wrong provider",
                organization_type="hospital",
                verification_status="verified",
                status="active",
                is_demo=True,
                created_by=fixture.user.id,
            )
            session.add(other_provider)
            await session.flush()

            application = _make_application(
                fixture=fixture,
                consumer=consumer,
                application_number=f"APP-PG-{uuid4().hex}",
            )
            session.add(application)
            await session.flush()

            wrong_space_id = uuid4()
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        _make_item(
                            application=application,
                            fixture=fixture,
                            space_id=wrong_space_id,
                        )
                    )
                    await session.flush()

            wrong_provider_application = _make_application(
                fixture=fixture,
                consumer=consumer,
                application_number=f"APP-PG-{uuid4().hex}",
                provider_id=other_provider.id,
            )
            session.add(wrong_provider_application)
            await session.flush()
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        _make_item(
                            application=wrong_provider_application,
                            fixture=fixture,
                        )
                    )
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _assert_snapshot_trigger(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture = await _create_catalog_fixture(session, approve=True)
            await publish_version(
                session,
                fixture.product,
                fixture.versions[0],
                published_by=fixture.user.id,
                visibility="space",
            )
            consumer = await _create_consumer(session, fixture.user.id)
            application = _make_application(
                fixture=fixture,
                consumer=consumer,
                application_number=f"APP-PG-{uuid4().hex}",
            )
            session.add(application)
            await session.flush()

            early_snapshot_savepoint = await session.begin_nested()
            early_snapshot_error: pytest.ExceptionInfo[DBAPIError]
            try:
                with pytest.raises(DBAPIError) as early_snapshot_error:
                    await session.execute(
                        insert(ApplicationSnapshot).values(
                            id=uuid4(),
                            application_id=application.id,
                            schema_version="1.0",
                            manifest={"schema_version": "1.0"},
                            snapshot_digest=f"sha256:{uuid4().hex}",
                            digest_algorithm="sha256",
                            captured_by=fixture.user.id,
                        )
                    )
            finally:
                if early_snapshot_savepoint.is_active:
                    await early_snapshot_savepoint.rollback()
            assert "snapshot can only be created during submission" in str(
                early_snapshot_error.value.orig
            )

            session.add(_make_item(application=application, fixture=fixture))
            await _add_minimum_usage_request(
                session,
                application=application,
                user_id=fixture.user.id,
            )
            snapshot = await submit_application(
                session,
                application,
                submitted_by=fixture.user.id,
            )
            await session.flush()

            savepoint = await session.begin_nested()
            caught: pytest.ExceptionInfo[DBAPIError]
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        update(ApplicationSnapshot)
                        .where(ApplicationSnapshot.id == snapshot.id)
                        .values(snapshot_digest="sha256:tampered")
                    )
                    await session.flush()
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
            assert "application snapshot is immutable" in str(caught.value.orig)
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _assert_extension_constraints_and_guard(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture = await _create_catalog_fixture(session, approve=True)
            await publish_version(
                session,
                fixture.product,
                fixture.versions[0],
                published_by=fixture.user.id,
                visibility="space",
            )
            consumer = await _create_consumer(session, fixture.user.id)
            application = _make_application(
                fixture=fixture,
                consumer=consumer,
                application_number=f"APP-EXT-PG-{uuid4().hex}",
            )
            session.add(application)
            await session.flush()

            invalid_action = await session.begin_nested()
            try:
                with pytest.raises(DBAPIError):
                    await session.execute(
                        insert(ApplicationRequestedAction).values(
                            application_id=application.id,
                            action_code="AI-Training",
                            parameters={"schema_version": "1.0"},
                        )
                    )
            finally:
                if invalid_action.is_active:
                    await invalid_action.rollback()

            session.add(_make_item(application=application, fixture=fixture))
            await _add_minimum_usage_request(
                session,
                application=application,
                user_id=fixture.user.id,
            )
            snapshot = await submit_application(
                session,
                application,
                submitted_by=fixture.user.id,
            )
            await session.flush()
            assert snapshot.manifest["requested_actions"]
            assert snapshot.manifest["requested_output_types"][0][
                "review_rule_digest"
            ].startswith("sha256:")
            assert snapshot.manifest["attachments"][0]["scan_status"] == "clean"

            savepoint = await session.begin_nested()
            caught: pytest.ExceptionInfo[DBAPIError]
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        update(ApplicationRequestedAction)
                        .where(
                            ApplicationRequestedAction.application_id
                            == application.id
                        )
                        .values(parameters={"schema_version": "2.0"})
                    )
                    await session.flush()
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
            assert "application components can only change in draft" in str(
                caught.value.orig
            )
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _create_consumer(session: AsyncSession, user_id) -> Organization:
    consumer = Organization(
        legal_name=f"Application PostgreSQL 浣跨敤鏈烘瀯 {uuid4().hex}",
        display_name="Application PostgreSQL 浣跨敤鏈烘瀯",
        organization_type="ai_company",
        verification_status="verified",
        status="active",
        is_demo=True,
        created_by=user_id,
    )
    session.add(consumer)
    await session.flush()
    return consumer


def _make_application(
    *,
    fixture,
    consumer: Organization,
    application_number: str,
    provider_id=None,
) -> Application:
    return Application(
        space_id=fixture.space.id,
        application_number=application_number,
        applicant_organization_id=consumer.id,
        applicant_user_id=fixture.user.id,
        provider_organization_id=provider_id or fixture.provider.id,
        purpose="PostgreSQL Application绾︽潫楠岃瘉锛堟紨绀猴級",
        legal_or_ethics_basis="婕旂ず渚濇嵁",
        algorithm_name="PG-Demo",
        algorithm_version="1.0",
        algorithm_digest=f"sha256:algorithm-{uuid4().hex}",
        requested_duration_seconds=3600,
        requested_run_limit=1,
        status="draft",
        is_demo=True,
        created_by=fixture.user.id,
    )


def _make_item(*, application: Application, fixture, space_id=None) -> ApplicationItem:
    version = fixture.versions[0]
    return ApplicationItem(
        application_id=application.id,
        space_id=space_id or application.space_id,
        provider_organization_id=application.provider_organization_id,
        data_product_id=fixture.product.id,
        data_product_version_id=version.id,
        position_no=1,
        requested_product_snapshot_digest=version.snapshot_digest,
        requested_policy_digest=version.default_policy_digest,
        requested_scope={"schema_version": "1.0"},
    )


async def _add_minimum_usage_request(
    session: AsyncSession,
    *,
    application: Application,
    user_id,
) -> ApplicationAttachment:
    attachment = ApplicationAttachment(
        application_id=application.id,
        attachment_type="research_protocol",
        display_name="research-protocol.pdf",
        storage_ref=f"application/{application.id}/research-protocol",
        content_digest=f"sha256:{uuid4().hex}{uuid4().hex}",
        size_bytes=1024,
        scan_status="pending",
        created_by=user_id,
    )
    session.add_all(
        [
            ApplicationRequestedAction(
                application_id=application.id,
                action_code="ai_training",
                parameters={"schema_version": "1.0"},
            ),
            ApplicationRequestedOutputType(
                application_id=application.id,
                output_type="model_artifact",
                requires_manual_review=False,
            ),
            attachment,
        ]
    )
    await session.flush()
    attachment.scan_status = "clean"
    await session.flush()
    return attachment

