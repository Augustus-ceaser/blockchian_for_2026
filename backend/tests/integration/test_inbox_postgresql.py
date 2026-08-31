from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.messaging import OutboxEnvelope
from app.modules.audit import AuditEvent, OutboxMessage
from app.modules.compute import ComputeRun, prepare_compute_run, reserve_compute_run
from app.modules.inbox import (
    ConsumerInboxEntry,
    InboxIdempotencyConflict,
    InboxInvariantError,
    claim_inbox_batch,
    complete_inbox,
    reclaim_expired_inbox,
    receive_inbox_envelope,
)
from tests.test_compute_models import _create_ready_job
from tests.test_contract_models import _system_audit_command

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


def test_inbox_schema_receive_idempotency_and_source_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_schema_receive_and_source_guards(TEST_DATABASE_URL))


def test_inbox_skip_locked_reclaim_ownership_and_terminal_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_lifecycle_and_concurrency(TEST_DATABASE_URL))


async def _make_delivery(session: AsyncSession):
    job, _, _, _, _, user = await _create_ready_job(
        session, number=f"CTR-INBOX-PG-{uuid4().hex}", run_limit=3
    )
    compute_run = await prepare_compute_run(session, job, created_by=user.id)
    await reserve_compute_run(
        session,
        compute_run,
        audit_command=_system_audit_command(
            f"inbox-reserve:{compute_run.id}", "medtrust.compute"
        ),
    )
    event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "compute.run.reserved",
            AuditEvent.subject_id == compute_run.id,
        )
    )
    assert event is not None
    message = await session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.audit_event_id == event.event_id,
            OutboxMessage.destination == "compute.dispatch",
        )
    )
    assert message is not None
    now = datetime.now(timezone.utc)
    message._delivery_transition_validated = True
    message.status = "processing"
    message.attempt_count = 1
    message.locked_at = now
    message.lock_owner = "dispatcher-inbox-test"
    message.lease_expires_at = now + timedelta(seconds=60)
    message.row_version += 1
    await session.flush()
    return OutboxEnvelope.from_records(message, event), compute_run


async def _schema_receive_and_source_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260725_0032"
            assert await connection.scalar(text("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='medtrust'")) == 54

        async with factory() as session:
            envelope, _ = await _make_delivery(session)
            first = await receive_inbox_envelope(
                session, consumer_name="execution-coordinator", envelope=envelope
            )
            replay = await receive_inbox_envelope(
                session, consumer_name="execution-coordinator", envelope=envelope
            )
            other = await receive_inbox_envelope(
                session, consumer_name="execution-coordinator-shadow", envelope=envelope
            )
            assert first.created and not replay.created and other.created
            assert replay.entry.id == first.entry.id
            with pytest.raises(InboxIdempotencyConflict):
                await receive_inbox_envelope(
                    session,
                    consumer_name="execution-coordinator",
                    envelope=replace(envelope, payload_digest="sha256:" + "a" * 64),
                )
            await session.commit()

        async with engine.begin() as connection:
            entry = await connection.execute(
                text(
                    "SELECT id,space_id,event_id,source_message_id,payload_digest "
                    "FROM medtrust.consumer_inbox_entries "
                    "WHERE consumer_name='execution-coordinator' ORDER BY created_at DESC LIMIT 1"
                )
            )
            row = entry.one()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(ConsumerInboxEntry)
                    .where(ConsumerInboxEntry.id == row.id)
                    .values(payload_digest="sha256:" + "b" * 64, row_version=2)
                )
    finally:
        await engine.dispose()


async def _lifecycle_and_concurrency(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            envelope, compute_run = await _make_delivery(session)
            received = await receive_inbox_envelope(
                session,
                consumer_name=f"execution-coordinator-{uuid4().hex}",
                envelope=envelope,
            )
            consumer_name = received.entry.consumer_name
            entry_id = received.entry.id
            run_id = compute_run.id
            await session.commit()

        async with factory() as first, factory() as second:
            first_tx = await first.begin()
            second_tx = await second.begin()
            first_claim = await claim_inbox_batch(
                first,
                consumer_name=consumer_name,
                worker_id="worker-a",
                batch_size=1,
                lease_seconds=-1,
            )
            second_claim = await claim_inbox_batch(
                second, consumer_name=consumer_name, worker_id="worker-b", batch_size=1
            )
            assert len(first_claim) == 1 and second_claim == []
            await first_tx.commit()
            await second_tx.commit()

        async with factory() as session:
            reclaimed = await reclaim_expired_inbox(
                session,
                consumer_name=consumer_name,
                worker_id="worker-b",
                batch_size=1,
            )
            assert len(reclaimed) == 1
            await session.commit()

        async with factory() as session:
            with pytest.raises(InboxInvariantError):
                await complete_inbox(
                    session,
                    entry_id=entry_id,
                    worker_id="worker-a",
                    outcome_code="executor_submitted",
                    outcome_reference_type="compute_run",
                    outcome_reference_id=run_id,
                )

        async with factory() as session:
            await complete_inbox(
                session,
                entry_id=entry_id,
                worker_id="worker-b",
                outcome_code="executor_submitted",
                outcome_reference_type="compute_run",
                outcome_reference_id=run_id,
            )
            await session.commit()

        async with engine.begin() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text(
                        "UPDATE medtrust.consumer_inbox_entries SET status='received', "
                        "completed_at=NULL,terminal_at=NULL,outcome_code=NULL,row_version=row_version+1 "
                        "WHERE id=:id"
                    ),
                    {"id": entry_id},
                )
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text("DELETE FROM medtrust.consumer_inbox_entries WHERE id=:id"),
                    {"id": entry_id},
                )
    finally:
        await engine.dispose()

