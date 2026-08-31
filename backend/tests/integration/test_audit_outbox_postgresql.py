from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.modules.audit import (
    AuditEvent,
    AuditInvariantError,
    IdempotencyConflict,
    OutboxMessage,
    append_audit_event_with_outbox,
    claim_outbox_batch,
    digest_idempotency_key,
    mark_outbox_failed,
    mark_outbox_published,
    reclaim_expired_outbox,
)
from app.modules.compute import AuditEvidenceUnavailable, prepare_compute_run, reserve_compute_run
from app.messaging import InMemoryPublisher, OutboxEnvelope
from app.workers.outbox_dispatcher import DispatcherConfig, OutboxDispatcher
from tests.test_compute_models import _create_ready_job


TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_audit_outbox_schema_and_canonical_crosscheck() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_schema_and_canonical(TEST_DATABASE_URL))


def test_audit_chain_idempotency_immutability_and_atomic_rollback() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_chain_and_guards(TEST_DATABASE_URL))


def test_same_space_chain_serializes_and_different_spaces_remain_independent() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_chain_concurrency(TEST_DATABASE_URL))


def test_outbox_skip_locked_lease_retry_and_terminal_states() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_outbox_lifecycle(TEST_DATABASE_URL))


def test_dispatcher_multi_worker_lease_takeover_and_duplicate_delivery() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_dispatcher_delivery(TEST_DATABASE_URL))


async def _append_job_event(
    session: AsyncSession,
    job,
    *,
    raw_key: str,
    command_id=None,
    correlation_id=None,
):
    return await append_audit_event_with_outbox(
        session,
        space_id=job.space_id,
        event_type="compute.job.created",
        actor_type="system",
        actor_service_code="medtrust.compute",
        subject_type="compute_job",
        subject_id=job.id,
        result="success",
        correlation_id=correlation_id or uuid4(),
        command_id=command_id or uuid4(),
        idempotency_key=digest_idempotency_key(raw_key),
        evidence_snapshot={
            "schema_version": "compute-job-created/v1",
            "job_id": str(job.id),
            "authorization_digest": job.creation_authorization_evaluation_digest,
        },
    )


async def _new_job(session: AsyncSession, prefix: str):
    return await _create_ready_job(
        session, number=f"CTR-AUDIT-PG-{prefix}-{uuid4().hex}", run_limit=4
    )


async def _assert_schema_and_canonical(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260725_0032"
            assert await connection.scalar(
                text("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='medtrust'")
            ) == 54
            vectors = [
                {},
                {"z": 1, "a": 2},
                {"涓枃": [3, None, True], "emoji": "馃檪"},
                {"nested": {"z": 2, "a": [1, {"b": False}]}},
            ]
            from app.modules.audit import canonical_json_digest_v1, canonical_json_text_v1

            for vector in vectors:
                db_text = await connection.scalar(
                    text("SELECT medtrust.canonicalize_jsonb_v1(CAST(:value AS jsonb))"),
                    {"value": __import__("json").dumps(vector, ensure_ascii=False)},
                )
                db_digest = await connection.scalar(
                    text("SELECT medtrust.sha256_canonical_jsonb_v1(CAST(:value AS jsonb))"),
                    {"value": __import__("json").dumps(vector, ensure_ascii=False)},
                )
                assert db_text == canonical_json_text_v1(vector)
                assert db_digest == canonical_json_digest_v1(vector)
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text("SELECT medtrust.canonicalize_jsonb_v1('1.25'::jsonb)")
                )
    finally:
        await engine.dispose()


async def _assert_chain_and_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job, revision, *_ = await _new_job(session, "CHAIN")
            command_id = uuid4()
            correlation_id = uuid4()
            raw_key = f"chain-{uuid4()}"
            key = digest_idempotency_key(raw_key)
            first = await _append_job_event(
                session,
                job,
                raw_key=raw_key,
                command_id=command_id,
                correlation_id=correlation_id,
            )
            replay = await _append_job_event(
                session,
                job,
                raw_key=raw_key,
                command_id=command_id,
                correlation_id=correlation_id,
            )
            second = await append_audit_event_with_outbox(
                session,
                space_id=job.space_id,
                event_type="contract.revision.activated",
                actor_type="system",
                actor_service_code="medtrust.contract",
                subject_type="contract_revision",
                subject_id=revision.id,
                result="success",
                correlation_id=correlation_id,
                command_id=command_id,
                idempotency_key=key,
                evidence_snapshot={
                    "schema_version": "contract-activated/v1",
                    "revision_id": str(revision.id),
                },
            )
            assert replay.created is False
            assert first.event.stream_sequence >= 1
            assert second.event.stream_sequence == first.event.stream_sequence + 1
            assert second.event.previous_event_digest == first.event.event_digest
            with pytest.raises(IdempotencyConflict):
                await append_audit_event_with_outbox(
                    session,
                    space_id=job.space_id,
                    event_type="compute.job.created",
                    actor_type="system",
                    actor_service_code="medtrust.compute",
                    subject_type="compute_job",
                    subject_id=job.id,
                    result="success",
                    correlation_id=correlation_id,
                    command_id=command_id,
                    idempotency_key=key,
                    evidence_snapshot={"schema_version": "compute-job-created/v1", "tampered": True},
                )
            event_id = first.event.event_id
            message_id = first.messages[0].message_id
            space_id = job.space_id
            await session.commit()

        async with engine.connect() as connection:
            valid = (
                await connection.execute(
                    text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
                    {"space_id": space_id},
                )
            ).one()
            assert valid.is_valid is True

        async def rejected(statement: object, expected: str) -> None:
            async with factory() as session:
                try:
                    with pytest.raises(DBAPIError) as caught:
                        await session.execute(statement)  # type: ignore[arg-type]
                    assert expected in str(caught.value.orig)
                finally:
                    await session.rollback()

        await rejected(
            update(AuditEvent).where(AuditEvent.event_id == event_id).values(result="failure"),
            "append-only",
        )
        await rejected(
            delete(AuditEvent).where(AuditEvent.event_id == event_id),
            "append-only",
        )
        await rejected(
            update(OutboxMessage)
            .where(OutboxMessage.message_id == message_id)
            .values(topic="tampered", row_version=OutboxMessage.row_version + 1),
            "core fields are immutable",
        )

        async with factory() as session:
            job, *_ = await _new_job(session, "ROLLBACK")
            appended = await _append_job_event(
                session, job, raw_key=f"rollback-{uuid4()}"
            )
            event_id = appended.event.event_id
            await session.rollback()
        async with factory() as session:
            assert await session.get(AuditEvent, event_id) is None
            assert await session.scalar(
                select(OutboxMessage).where(OutboxMessage.audit_event_id == event_id)
            ) is None
    finally:
        await engine.dispose()


async def _assert_chain_concurrency(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_size=6, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job_a, *_ = await _new_job(session, "CONCURRENT-A")
            job_b, *_ = await _new_job(session, "CONCURRENT-B")
            base_a = await session.scalar(
                select(AuditEvent.stream_sequence)
                .where(AuditEvent.space_id == job_a.space_id)
                .order_by(AuditEvent.stream_sequence.desc())
                .limit(1)
            )
            base_b = await session.scalar(
                select(AuditEvent.stream_sequence)
                .where(AuditEvent.space_id == job_b.space_id)
                .order_by(AuditEvent.stream_sequence.desc())
                .limit(1)
            )
            ids = (
                job_a.id,
                job_a.space_id,
                job_b.id,
                job_b.space_id,
                int(base_a or 0),
                int(base_b or 0),
            )
            await session.commit()

        async def append(job_id, space_id, label):
            async with factory() as session:
                job = await session.get(__import__("app.modules.compute.models", fromlist=["ComputeJob"]).ComputeJob, job_id)
                assert job is not None and job.space_id == space_id
                result = await _append_job_event(session, job, raw_key=f"{label}-{uuid4()}")
                await session.commit()
                return result.event.stream_sequence

        same_space = await asyncio.gather(
            append(ids[0], ids[1], "same-a"),
            append(ids[0], ids[1], "same-b"),
        )
        assert sorted(same_space) == [ids[4] + 1, ids[4] + 2]
        different_spaces = await asyncio.gather(
            append(ids[0], ids[1], "different-a"),
            append(ids[2], ids[3], "different-b"),
        )
        assert different_spaces[0] == ids[4] + 3
        assert different_spaces[1] == ids[5] + 1
    finally:
        await engine.dispose()


async def _assert_outbox_lifecycle(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_size=6, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            jobs = []
            for label in ("OUTBOX-A", "OUTBOX-B", "OUTBOX-C", "OUTBOX-D"):
                job, *_ = await _new_job(session, label)
                await _append_job_event(session, job, raw_key=f"{label}-{uuid4()}")
                jobs.append(job)
            await session.commit()

        async def claim_one(worker):
            async with factory() as session:
                messages = await claim_outbox_batch(
                    session, worker_id=worker, batch_size=1, lease_seconds=60
                )
                await session.commit()
                return messages[0].message_id

        claimed_ids = await asyncio.gather(claim_one("worker-a"), claim_one("worker-b"))
        assert len(set(claimed_ids)) == 2

        async with factory() as session:
            unsafe = await session.get(OutboxMessage, claimed_ids[1])
            assert unsafe is not None and unsafe.status == "processing"
            with pytest.raises(DBAPIError) as caught:
                await session.execute(
                    update(OutboxMessage)
                    .where(OutboxMessage.message_id == unsafe.message_id)
                    .values(
                        status="pending",
                        locked_at=None,
                        lock_owner=None,
                        lease_expires_at=None,
                        last_error="token=raw-secret",
                        available_at=datetime.now(timezone.utc),
                        row_version=OutboxMessage.row_version + 1,
                    )
                )
            assert "sensitive content" in str(caught.value.orig)
            await session.rollback()

        async with factory() as session:
            message = await session.get(OutboxMessage, claimed_ids[0])
            assert message is not None
            first_owner = message.lock_owner
            assert await reclaim_expired_outbox(
                session, worker_id="worker-c", batch_size=10, lease_seconds=60
            ) == []
            await session.rollback()

        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE medtrust.outbox_messages DISABLE TRIGGER trg_guard_outbox_message_v8"))
            await connection.execute(
                text("UPDATE medtrust.outbox_messages SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE message_id=:message_id"),
                {"message_id": claimed_ids[0]},
            )
            await connection.execute(text("ALTER TABLE medtrust.outbox_messages ENABLE TRIGGER trg_guard_outbox_message_v8"))

        async with factory() as session:
            reclaimed = await reclaim_expired_outbox(
                session, worker_id="worker-c", batch_size=1, lease_seconds=60
            )
            assert len(reclaimed) == 1
            assert reclaimed[0].message_id == claimed_ids[0]
            assert reclaimed[0].lock_owner == "worker-c" != first_owner
            await mark_outbox_published(
                session, message_id=reclaimed[0].message_id, worker_id="worker-c"
            )
            await session.commit()

        async with factory() as session:
            message = await session.get(OutboxMessage, claimed_ids[0])
            assert message is not None and message.status == "published"
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(OutboxMessage)
                    .where(OutboxMessage.message_id == message.message_id)
                    .values(
                        status="pending",
                        published_at=None,
                        row_version=OutboxMessage.row_version + 1,
                    )
                )
            await session.rollback()

        async with factory() as session:
            pending = await session.scalar(
                select(OutboxMessage).where(OutboxMessage.status == "pending").limit(1)
            )
            assert pending is not None
            pending_id = pending.message_id
            await session.commit()
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE medtrust.outbox_messages DISABLE TRIGGER trg_guard_outbox_message_v8"))
            await connection.execute(
                text("UPDATE medtrust.outbox_messages SET attempt_count=9, available_at='2000-01-01T00:00:00Z' WHERE message_id=:message_id"),
                {"message_id": pending_id},
            )
            await connection.execute(text("ALTER TABLE medtrust.outbox_messages ENABLE TRIGGER trg_guard_outbox_message_v8"))
        async with factory() as session:
            claimed = await claim_outbox_batch(
                session, worker_id="worker-dead", batch_size=1, lease_seconds=60
            )
            target = next(row for row in claimed if row.message_id == pending_id)
            assert target.attempt_count == 10
            await mark_outbox_failed(
                session,
                message_id=target.message_id,
                worker_id="worker-dead",
                error="Authorization: Bearer super-secret https://example.test/file?token=hidden",
                retryable=True,
            )
            assert target.status == "dead_letter"
            assert "super-secret" not in (target.last_error or "")
            await session.commit()

        async with factory() as session:
            dead = await session.get(OutboxMessage, pending_id)
            assert dead is not None and dead.status == "dead_letter"
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(OutboxMessage)
                    .where(OutboxMessage.message_id == pending_id)
                    .values(
                        status="pending",
                        last_error=None,
                        row_version=OutboxMessage.row_version + 1,
                    )
                )
            await session.rollback()

        async with factory() as session:
            job, *_ = await _new_job(session, "FAIL-CLOSED")
            run_row = await prepare_compute_run(session, job, created_by=job.requester_user_id)
            run_id = run_row.id
            await session.commit()

        async with factory() as session:
            run_row = await session.get(type(run_row), run_id)
            assert run_row is not None
            with pytest.raises(AuditEvidenceUnavailable):
                await reserve_compute_run(session, run_row)
            await session.rollback()
            persisted = await session.get(type(run_row), run_id)
            assert persisted is not None and persisted.status == "prepared"
    finally:
        await engine.dispose()


async def _assert_dispatcher_delivery(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_size=6, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for label in ("DISPATCH-A", "DISPATCH-B", "DISPATCH-C"):
                job, *_ = await _new_job(session, label)
                await _append_job_event(
                    session, job, raw_key=f"dispatcher-{label}-{uuid4()}"
                )
            await session.commit()

        publisher_a = InMemoryPublisher()
        publisher_b = InMemoryPublisher()
        dispatcher_a = OutboxDispatcher(
            session_maker=factory,
            publisher=publisher_a,
            config=DispatcherConfig(worker_id="dispatcher-pg-a", batch_size=1),
        )
        dispatcher_b = OutboxDispatcher(
            session_maker=factory,
            publisher=publisher_b,
            config=DispatcherConfig(worker_id="dispatcher-pg-b", batch_size=1),
        )
        assert await asyncio.gather(
            dispatcher_a.dispatch_once(), dispatcher_b.dispatch_once()
        ) == [1, 1]
        claimed = {
            publisher_a.delivery_attempts[0].message_id,
            publisher_b.delivery_attempts[0].message_id,
        }
        assert len(claimed) == 2

        async with factory() as session:
            old_claim = await claim_outbox_batch(
                session,
                worker_id="dispatcher-crashed",
                batch_size=1,
                lease_seconds=60,
            )
            assert len(old_claim) == 1
            old_message = old_claim[0]
            old_event = await session.get(AuditEvent, old_message.audit_event_id)
            assert old_event is not None
            duplicate_envelope = OutboxEnvelope.from_records(old_message, old_event)
            await session.commit()

        handled: list[object] = []
        idempotent_publisher = InMemoryPublisher(
            lambda envelope: handled.append(envelope.event_id)
        )
        assert (await idempotent_publisher.publish(duplicate_envelope)).acknowledged

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE medtrust.outbox_messages "
                    "DISABLE TRIGGER trg_guard_outbox_message_v8"
                )
            )
            await connection.execute(
                text(
                    "UPDATE medtrust.outbox_messages "
                    "SET lease_expires_at=clock_timestamp()-interval '1 second', "
                    "available_at='1900-01-01T00:00:00Z' "
                    "WHERE message_id=:message_id"
                ),
                {"message_id": old_message.message_id},
            )
            await connection.execute(
                text(
                    "ALTER TABLE medtrust.outbox_messages "
                    "ENABLE TRIGGER trg_guard_outbox_message_v8"
                )
            )

        recovery = OutboxDispatcher(
            session_maker=factory,
            publisher=idempotent_publisher,
            config=DispatcherConfig(worker_id="dispatcher-recovery", batch_size=1),
        )
        assert await recovery.dispatch_once() == 1
        assert recovery.stats.lease_reclaimed_count == 1
        assert len(idempotent_publisher.delivery_attempts) == 2
        assert handled == [duplicate_envelope.event_id]

        async with factory() as session:
            persisted = await session.get(OutboxMessage, old_message.message_id)
            assert persisted is not None and persisted.status == "published"
            with pytest.raises(AuditInvariantError):
                await mark_outbox_published(
                    session,
                    message_id=old_message.message_id,
                    worker_id="dispatcher-crashed",
                )
            await session.rollback()
    finally:
        await engine.dispose()

