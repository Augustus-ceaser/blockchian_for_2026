from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.messaging import OutboxEnvelope
from app.modules.audit import (
    AuditEvent,
    OutboxMessage,
    append_audit_event_with_outbox,
    claim_outbox_batch,
    digest_idempotency_key,
)
from app.modules.compute import prepare_compute_run
from app.modules.inbox import (
    ConsumerInboxEntry,
    InboxIdempotencyConflict,
    InboxInvariantError,
    claim_inbox_batch,
    complete_inbox,
    reclaim_expired_inbox,
    receive_inbox_envelope,
)
from tests.test_application_models import create_schema, make_engine
from tests.test_compute_models import _create_ready_job


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


async def _dispatch_envelope(session) -> tuple[OutboxEnvelope, object]:
    job, _, _, _, _, user = await _create_ready_job(
        session, number=f"CTR-INBOX-{uuid4().hex}", run_limit=2
    )
    compute_run = await prepare_compute_run(session, job, created_by=user.id)
    appended = await append_audit_event_with_outbox(
        session,
        space_id=job.space_id,
        event_type="compute.run.reserved",
        actor_type="system",
        actor_service_code="medtrust.compute",
        subject_type="compute_run",
        subject_id=compute_run.id,
        result="success",
        correlation_id=uuid4(),
        command_id=uuid4(),
        idempotency_key=digest_idempotency_key(f"inbox-{uuid4()}"),
        evidence_snapshot={
            "schema_version": "compute-run-reserved-evidence/v1",
            "compute_run_id": str(compute_run.id),
        },
    )
    dispatch = next(
        message for message in appended.messages if message.destination == "compute.dispatch"
    )
    dispatch._delivery_transition_validated = True
    dispatch.status = "processing"
    dispatch.attempt_count = 1
    now = datetime.now(timezone.utc)
    dispatch.locked_at = now
    dispatch.lock_owner = "dispatcher"
    dispatch.lease_expires_at = now + timedelta(seconds=60)
    dispatch.row_version += 1
    await session.flush()
    return OutboxEnvelope.from_records(dispatch, appended.event), compute_run


def test_inbox_receive_is_consumer_scoped_and_idempotent() -> None:
    run(_inbox_receive_is_consumer_scoped_and_idempotent())


async def _inbox_receive_is_consumer_scoped_and_idempotent() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            envelope, _ = await _dispatch_envelope(session)
            first = await receive_inbox_envelope(
                session, consumer_name="execution-coordinator", envelope=envelope
            )
            replay = await receive_inbox_envelope(
                session, consumer_name="execution-coordinator", envelope=envelope
            )
            second_consumer = await receive_inbox_envelope(
                session, consumer_name="coordinator-shadow", envelope=envelope
            )
            assert first.created is True
            assert replay.created is False and replay.entry.id == first.entry.id
            assert second_consumer.created is True
            with pytest.raises(InboxIdempotencyConflict):
                await receive_inbox_envelope(
                    session,
                    consumer_name="execution-coordinator",
                    envelope=replace(envelope, payload_digest="sha256:" + "f" * 64),
                )
    finally:
        await engine.dispose()


def test_inbox_claim_complete_and_stale_owner_guard() -> None:
    run(_inbox_claim_complete_and_stale_owner_guard())


async def _inbox_claim_complete_and_stale_owner_guard() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            envelope, compute_run = await _dispatch_envelope(session)
            result = await receive_inbox_envelope(
                session, consumer_name="execution-coordinator", envelope=envelope
            )
            await session.commit()
            entry_id = result.entry.id

        async with factory() as session:
            claimed = await claim_inbox_batch(
                session,
                consumer_name="execution-coordinator",
                worker_id="worker-old",
                batch_size=1,
            )
            assert len(claimed) == 1 and claimed[0].attempt_count == 1
            claimed[0].lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        async with factory() as session:
            reclaimed = await reclaim_expired_inbox(
                session,
                consumer_name="execution-coordinator",
                worker_id="worker-new",
                batch_size=1,
            )
            assert len(reclaimed) == 1 and reclaimed[0].attempt_count == 2
            await complete_inbox(
                session,
                entry_id=entry_id,
                worker_id="worker-new",
                outcome_code="executor_submitted",
                outcome_reference_type="compute_run",
                outcome_reference_id=compute_run.id,
            )
            await session.commit()

        async with factory() as session:
            entry = await session.get(ConsumerInboxEntry, entry_id)
            assert entry is not None and entry.status == "completed"
            with pytest.raises(InboxInvariantError):
                await complete_inbox(
                    session,
                    entry_id=entry_id,
                    worker_id="worker-old",
                    outcome_code="already_dispatched",
                )
    finally:
        await engine.dispose()
