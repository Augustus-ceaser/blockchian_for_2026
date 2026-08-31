from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.applications import submit_application
from app.modules.catalog import publish_version
from app.modules.identity.models import OrganizationMember
from app.modules.reviews.models import ReviewDecision, ReviewTask
from app.modules.reviews.services import claim_review_task, submit_review_decision
from app.modules.spaces.models import SpaceParticipant
from tests.integration.test_applications_postgresql import (
    _add_minimum_usage_request,
    _create_consumer,
    _make_application,
    _make_item,
)
from tests.integration.test_catalog_postgresql import _create_catalog_fixture

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

REVIEW_TABLES = {"review_tasks", "review_decisions"}
REVIEW_TRIGGERS = {
    "trg_review_task_lifecycle",
    "trg_review_decision_append_only",
    "trg_review_task_decision_consistency",
    "trg_review_decision_task_consistency",
}
REVIEW_FUNCTIONS = {
    "guard_review_task_lifecycle",
    "guard_review_decision",
    "require_review_decision_consistency",
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


def test_review_schema_objects_exist_on_migrated_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_review_schema_objects(TEST_DATABASE_URL))


def test_review_decision_is_append_only_on_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_review_decision_guards(TEST_DATABASE_URL))


def test_review_task_requires_one_decision_at_terminal_state() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_deferred_task_decision_consistency(TEST_DATABASE_URL))


async def _assert_review_schema_objects(database_url: str) -> None:
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
            assert REVIEW_TABLES <= tables
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
            assert REVIEW_TRIGGERS <= triggers
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
            assert REVIEW_FUNCTIONS <= functions
    finally:
        await engine.dispose()


async def _create_review_fixture(session: AsyncSession, *, review_type="provider_review"):
    fixture = await _create_catalog_fixture(session, approve=True)
    await publish_version(
        session,
        fixture.product,
        fixture.versions[0],
        published_by=fixture.user.id,
        visibility="space",
    )
    session.add_all(
        [
            SpaceParticipant(
                space_id=fixture.space.id,
                organization_id=fixture.provider.id,
                admission_status="admitted",
                ruleset_accepted_version="rules-v1",
                admitted_at=datetime.now(timezone.utc),
                created_by=fixture.user.id,
            ),
            OrganizationMember(
                organization_id=fixture.provider.id,
                user_id=fixture.user.id,
                status="active",
                valid_from=datetime.now(timezone.utc),
                created_by=fixture.user.id,
            ),
        ]
    )
    await session.flush()

    consumer = await _create_consumer(session, fixture.user.id)
    application = _make_application(
        fixture=fixture,
        consumer=consumer,
        application_number=f"APP-REVIEW-PG-{uuid4().hex}",
    )
    session.add(application)
    await session.flush()
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
    task = ReviewTask(
        space_id=fixture.space.id,
        review_type=review_type,
        application_id=application.id,
        application_snapshot_id=snapshot.id,
        target_digest=snapshot.snapshot_digest,
        assignee_organization_id=fixture.provider.id,
        task_status="pending",
        sequence_no=20,
        is_required=True,
        routing_rule_digest=f"sha256:{'a' * 64}",
        created_by=fixture.user.id,
    )
    session.add(task)
    await session.flush()
    return fixture, task


async def _assert_review_decision_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture, task = await _create_review_fixture(session)
            claim_review_task(task, user_id=fixture.user.id)
            await session.flush()
            decision = await submit_review_decision(
                session,
                task,
                decision="approved",
                decided_by_user_id=fixture.user.id,
                decided_for_organization_id=fixture.provider.id,
                evidence={"schema_version": "1.0"},
            )
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

            for statement in (
                update(ReviewDecision)
                .where(ReviewDecision.id == decision.id)
                .values(comment="tampered"),
                delete(ReviewDecision).where(ReviewDecision.id == decision.id),
            ):
                savepoint = await session.begin_nested()
                try:
                    with pytest.raises(DBAPIError) as caught:
                        await session.execute(statement)
                    assert "review decision is append-only" in str(caught.value.orig)
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _assert_deferred_task_decision_consistency(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture, task = await _create_review_fixture(
                session, review_type="ethics_review"
            )
            claim_review_task(task, user_id=fixture.user.id)
            await session.flush()
            savepoint = await session.begin_nested()
            try:
                await session.execute(
                    update(ReviewTask)
                    .where(ReviewTask.id == task.id)
                    .values(
                        task_status="decided",
                        decided_at=datetime.now(timezone.utc),
                    )
                )
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                assert "terminal state are inconsistent" in str(caught.value.orig)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()

