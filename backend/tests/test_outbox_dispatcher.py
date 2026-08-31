from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.messaging import (
    FakePublisher,
    InMemoryPublisher,
    PublishResult,
    UnavailablePublisher,
)
from app.modules.audit import (
    AuditInvariantError,
    OutboxMessage,
    append_audit_event_with_outbox,
    claim_outbox_batch,
    digest_idempotency_key,
    mark_outbox_published,
)
from app.workers.outbox_dispatcher import DispatcherConfig, OutboxDispatcher
from tests.test_application_models import create_schema, make_engine
from tests.test_compute_models import _create_ready_job


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


async def _seed_message(session, label: str) -> None:
    job, *_ = await _create_ready_job(
        session, number=f"CTR-DISPATCH-{label}-{uuid4().hex}", run_limit=3
    )
    await append_audit_event_with_outbox(
        session,
        space_id=job.space_id,
        event_type="compute.job.created",
        actor_type="system",
        actor_service_code="medtrust.compute",
        subject_type="compute_job",
        subject_id=job.id,
        result="success",
        correlation_id=uuid4(),
        command_id=uuid4(),
        idempotency_key=digest_idempotency_key(f"dispatcher-{label}-{uuid4()}"),
        evidence_snapshot={
            "schema_version": "compute-job-created/v1",
            "job_id": str(job.id),
            "note": "safe evidence must never be logged",
        },
    )


def _config(worker_id: str) -> DispatcherConfig:
    return DispatcherConfig(
        worker_id=worker_id,
        batch_size=1,
        poll_interval=0.05,
        lease_seconds=60,
        shutdown_timeout=0.1,
    )


def test_dispatcher_marks_published_only_after_ack() -> None:
    run(_dispatcher_marks_published_only_after_ack())


async def _dispatcher_marks_published_only_after_ack() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_message(session, "ACK")
            await session.commit()
        publisher = FakePublisher([PublishResult.acknowledged_result()])
        dispatcher = OutboxDispatcher(
            session_maker=factory, publisher=publisher, config=_config("worker-ack")
        )
        assert await dispatcher.dispatch_once() == 1
        async with factory() as session:
            message = await session.get(
                OutboxMessage, publisher.messages[0].message_id
            )
            assert message is not None and message.status == "published"
        assert dispatcher.stats.published_count == 1
        assert len(publisher.messages) == 1
    finally:
        await engine.dispose()


def test_unavailable_publisher_never_acknowledges() -> None:
    async def exercise() -> None:
        publisher = UnavailablePublisher()
        # The concrete envelope is irrelevant because this publisher performs no I/O.
        result = await publisher.publish(None)  # type: ignore[arg-type]
        assert result.acknowledged is False
        assert result.retryable is True
        assert result.error_code == "publisher_unavailable"

    run(exercise())


def test_dispatcher_nack_retries_and_tenth_failure_dead_letters() -> None:
    run(_dispatcher_nack_retries_and_tenth_failure_dead_letters())


async def _dispatcher_nack_retries_and_tenth_failure_dead_letters() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_message(session, "DEAD")
            await session.commit()
        async with factory() as session:
            message = await session.scalar(
                select(OutboxMessage).order_by(
                    OutboxMessage.available_at,
                    OutboxMessage.created_at,
                    OutboxMessage.message_id,
                )
            )
            assert message is not None
            message_id = message.message_id
            message._delivery_transition_validated = True
            message.attempt_count = 9
            message.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
            message._delivery_transition_validated = False
        publisher = FakePublisher(
            [PublishResult.failed_result("temporary_failure", retryable=True)]
        )
        dispatcher = OutboxDispatcher(
            session_maker=factory, publisher=publisher, config=_config("worker-dead")
        )
        assert await dispatcher.dispatch_once() == 1
        async with factory() as session:
            message = await session.get(OutboxMessage, message_id)
            assert message is not None
            assert message.status == "dead_letter" and message.attempt_count == 10
        assert dispatcher.stats.dead_letter_count == 1
    finally:
        await engine.dispose()


def test_dispatcher_nack_does_not_mark_message_published() -> None:
    run(_dispatcher_nack_does_not_mark_message_published())


async def _dispatcher_nack_does_not_mark_message_published() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_message(session, "RETRY")
            await session.commit()
        publisher = FakePublisher(
            [PublishResult.failed_result("transport_timeout", retryable=True)]
        )
        dispatcher = OutboxDispatcher(
            session_maker=factory, publisher=publisher, config=_config("worker-retry")
        )
        assert await dispatcher.dispatch_once() == 1
        async with factory() as session:
            message = await session.get(
                OutboxMessage, publisher.messages[0].message_id
            )
            assert message is not None
            assert message.status == "pending" and message.published_at is None
            assert message.attempt_count == 1
        assert dispatcher.stats.retry_count == 1
        assert dispatcher.stats.published_count == 0
    finally:
        await engine.dispose()


def test_stale_worker_cannot_overwrite_reclaimed_message() -> None:
    run(_stale_worker_cannot_overwrite_reclaimed_message())


async def _stale_worker_cannot_overwrite_reclaimed_message() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_message(session, "LEASE")
            await session.commit()
        async with factory() as session:
            claimed = await claim_outbox_batch(
                session, worker_id="worker-old", batch_size=1, lease_seconds=60
            )
            message_id = claimed[0].message_id
            await session.commit()
        async with factory() as session:
            message = await session.get(OutboxMessage, message_id)
            assert message is not None
            message._delivery_transition_validated = True
            message.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
            message._delivery_transition_validated = False

        replacement = OutboxDispatcher(
            session_maker=factory,
            publisher=FakePublisher(),
            config=_config("worker-new"),
        )
        assert await replacement.dispatch_once() == 1
        assert replacement.stats.lease_reclaimed_count == 1

        async with factory() as session:
            with pytest.raises(AuditInvariantError, match="not processing"):
                await mark_outbox_published(
                    session, message_id=message_id, worker_id="worker-old"
                )
    finally:
        await engine.dispose()


def test_duplicate_delivery_is_idempotent_per_destination_and_event() -> None:
    run(_duplicate_delivery_is_idempotent_per_destination_and_event())


async def _duplicate_delivery_is_idempotent_per_destination_and_event() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    handled: list[object] = []
    publisher = InMemoryPublisher(lambda envelope: handled.append(envelope.event_id))
    try:
        async with factory() as session:
            await _seed_message(session, "DUPLICATE")
            await session.commit()
        async with factory() as session:
            claimed = await claim_outbox_batch(
                session, worker_id="crashed-worker", batch_size=1, lease_seconds=60
            )
            message = claimed[0]
            event = await session.get(__import__("app.modules.audit.models", fromlist=["AuditEvent"]).AuditEvent, message.audit_event_id)
            assert event is not None
            from app.messaging import OutboxEnvelope

            envelope = OutboxEnvelope.from_records(message, event)
            await session.commit()
        assert (await publisher.publish(envelope)).acknowledged is True

        async with factory() as session:
            message = await session.get(OutboxMessage, envelope.message_id)
            assert message is not None
            message._delivery_transition_validated = True
            message.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
            message._delivery_transition_validated = False

        dispatcher = OutboxDispatcher(
            session_maker=factory,
            publisher=publisher,
            config=_config("recovery-worker"),
        )
        assert await dispatcher.dispatch_once() == 1
        assert len(publisher.delivery_attempts) == 2
        assert handled == [envelope.event_id]
    finally:
        await engine.dispose()


def test_stop_before_dispatch_does_not_claim_and_logs_do_not_expose_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    run(_stop_before_dispatch_does_not_claim_and_logs_do_not_expose_payload(caplog))


async def _stop_before_dispatch_does_not_claim_and_logs_do_not_expose_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_message(session, "STOP")
            await session.commit()
        dispatcher = OutboxDispatcher(
            session_maker=factory,
            publisher=FakePublisher(),
            config=_config("worker-stop"),
        )
        with caplog.at_level(logging.INFO, logger="medtrust.outbox_dispatcher"):
            assert await dispatcher.dispatch_once() == 1
        dispatcher.request_stop()
        assert await dispatcher.dispatch_once() == 0
        async with factory() as session:
            pending = await session.scalar(
                select(OutboxMessage).where(OutboxMessage.status == "pending")
            )
            assert pending is not None
        assert "safe evidence must never be logged" not in caplog.text
        assert "message_id=" in caplog.text and "event_id=" in caplog.text
    finally:
        await engine.dispose()
