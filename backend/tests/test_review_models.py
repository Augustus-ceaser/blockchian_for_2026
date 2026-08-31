import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.applications import ApplicationSnapshot
from app.modules.applications.services import _snapshot_digest
from app.modules.identity.models import OrganizationMember
from app.modules.reviews.models import ReviewDecision, ReviewTask
from app.modules.reviews.services import (
    ReviewInvariantError,
    claim_review_task,
    release_review_task,
    submit_review_decision,
)
from tests.test_application_models import create_schema, make_application, make_engine
from tests.test_catalog_models import create_catalog_graph


def test_create_and_transition_review_task() -> None:
    asyncio.run(_create_and_transition_review_task())


def test_invalid_review_type_is_rejected() -> None:
    asyncio.run(_reject_invalid_review_type())


def test_decision_is_append_only() -> None:
    asyncio.run(_protect_decision_update_and_delete())


def test_review_task_requires_matching_snapshot_evidence() -> None:
    asyncio.run(_reject_mismatched_snapshot_evidence())


async def _make_review_graph(session):
    user, product, _, _, space, _, _ = await create_catalog_graph(session)
    provider = product.provider_organization
    member = OrganizationMember(
        organization_id=provider.id,
        user_id=user.id,
        status="active",
        valid_from=datetime.now(timezone.utc),
        created_by=user.id,
    )
    session.add(member)
    await session.flush()

    application = await make_application(
        session,
        user=user,
        provider=provider,
        space=space,
        application_number="APP-REVIEW-001",
    )
    application.status = "submitted"
    application.submitted_at = datetime.now(timezone.utc)
    await session.flush()

    manifest = {"schema_version": "1.0", "application_id": str(application.id)}
    snapshot = ApplicationSnapshot(
        application_id=application.id,
        schema_version="1.0",
        manifest=manifest,
        snapshot_digest=_snapshot_digest(manifest),
        digest_algorithm="sha256",
        captured_by=user.id,
    )
    session.add(snapshot)
    await session.flush()
    return user, provider, space, application, snapshot


def _make_task(*, user, provider, space, application, snapshot) -> ReviewTask:
    return ReviewTask(
        space_id=space.id,
        review_type="provider_review",
        application_id=application.id,
        application_snapshot_id=snapshot.id,
        target_digest=snapshot.snapshot_digest,
        assignee_organization_id=provider.id,
        task_status="pending",
        sequence_no=20,
        is_required=True,
        routing_rule_digest=_snapshot_digest(
            {"schema_version": "1.0", "route": "provider_review"}
        ),
        created_by=user.id,
    )


async def _create_and_transition_review_task() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user, provider, space, application, snapshot = await _make_review_graph(session)
        task = _make_task(
            user=user,
            provider=provider,
            space=space,
            application=application,
            snapshot=snapshot,
        )
        session.add(task)
        await session.flush()
        assert task.task_status == "pending"

        claim_review_task(task, user_id=user.id)
        await session.flush()
        assert task.task_status == "claimed"

        release_review_task(task)
        await session.flush()
        assert task.task_status == "pending"

        claim_review_task(task, user_id=user.id)
        decision = await submit_review_decision(
            session,
            task,
            decision="approved",
            decided_by_user_id=user.id,
            decided_for_organization_id=provider.id,
            evidence={"schema_version": "1.0", "checks": ["purpose"]},
        )
        await session.commit()
        assert task.task_status == "decided"
        assert decision.target_digest == snapshot.snapshot_digest
        assert decision.decision_digest.startswith("sha256:")
    await engine.dispose()


async def _reject_invalid_review_type() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user, provider, space, application, snapshot = await _make_review_graph(session)
        task = _make_task(
            user=user,
            provider=provider,
            space=space,
            application=application,
            snapshot=snapshot,
        )
        task.review_type = "random_review"
        session.add(task)
        with pytest.raises(ReviewInvariantError, match="unknown review type"):
            await session.flush()
    await engine.dispose()


async def _protect_decision_update_and_delete() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user, provider, space, application, snapshot = await _make_review_graph(session)
        task = _make_task(
            user=user,
            provider=provider,
            space=space,
            application=application,
            snapshot=snapshot,
        )
        session.add(task)
        await session.flush()
        claim_review_task(task, user_id=user.id)
        await session.flush()
        decision = await submit_review_decision(
            session,
            task,
            decision="rejected",
            reason_code="missing_ethics_material",
            remediation="clone_and_resubmit",
            decided_by_user_id=user.id,
            decided_for_organization_id=provider.id,
            evidence={"schema_version": "1.0"},
        )
        await session.commit()
        decision_id = decision.id

        decision.comment = "tampered"
        with pytest.raises(ReviewInvariantError, match="append-only"):
            await session.flush()
        await session.rollback()

        stored = await session.get(ReviewDecision, decision_id)
        assert stored is not None
        await session.delete(stored)
        with pytest.raises(ReviewInvariantError, match="append-only"):
            await session.flush()
    await engine.dispose()


async def _reject_mismatched_snapshot_evidence() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user, provider, space, application, snapshot = await _make_review_graph(session)
        task = _make_task(
            user=user,
            provider=provider,
            space=space,
            application=application,
            snapshot=snapshot,
        )
        task.target_digest = _snapshot_digest({"tampered": True})
        session.add(task)
        with pytest.raises(IntegrityError):
            await session.flush()
    await engine.dispose()
