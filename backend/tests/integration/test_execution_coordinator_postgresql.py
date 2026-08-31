from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.execution import FakeExecutorAdapter
from app.execution.coordinator import CONSUMER_NAME, ExecutionCoordinatorService
from app.messaging import OutboxEnvelope
from app.modules.audit import AuditEvent, OutboxMessage
from app.modules.compute import ComputeRun, prepare_compute_run, reserve_compute_run
from app.modules.inbox import ConsumerInboxEntry, claim_inbox_batch, receive_inbox_envelope
from tests.test_compute_models import _create_ready_job
from tests.test_contract_models import _system_audit_command

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="MEDTRUST_TEST_DATABASE_URL is not configured"),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_coordinator_fake_executor_dispatch_is_atomic_and_idempotent() -> None:
    assert TEST_DATABASE_URL is not None
    run(_coordinator_dispatch(TEST_DATABASE_URL))


async def _coordinator_dispatch(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = FakeExecutorAdapter()
    service = ExecutionCoordinatorService(session_maker=factory, executor=adapter)
    try:
        async with factory() as session:
            job, _, _, _, _, user = await _create_ready_job(
                session, number=f"CTR-COORD-PG-{uuid4().hex}", run_limit=2
            )
            compute_run = await prepare_compute_run(session, job, created_by=user.id)
            await reserve_compute_run(
                session,
                compute_run,
                audit_command=_system_audit_command(
                    f"coordinator-reserve:{compute_run.id}", "medtrust.compute"
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
            message.lock_owner = "dispatcher-coordinator-test"
            message.lease_expires_at = now + timedelta(seconds=60)
            message.row_version += 1
            await session.flush()
            envelope = OutboxEnvelope.from_records(message, event)
            received = await receive_inbox_envelope(
                session, consumer_name=CONSUMER_NAME, envelope=envelope
            )
            run_id = compute_run.id
            entry_id = received.entry.id
            await session.commit()

        async with factory() as session:
            claimed = await claim_inbox_batch(
                session,
                consumer_name=CONSUMER_NAME,
                worker_id="coordinator-test",
                batch_size=1,
                lease_seconds=120,
            )
            assert [entry.id for entry in claimed] == [entry_id]
            await session.commit()

        result = await service.process_entry(entry_id=entry_id, worker_id="coordinator-test")
        assert result.outcome_code == "executor_submitted"
        assert adapter.submit_calls == 1

        async with factory() as session:
            compute_run = await session.get(ComputeRun, run_id)
            entry = await session.get(ConsumerInboxEntry, entry_id)
            assert compute_run is not None and compute_run.status == "dispatched"
            assert compute_run.execution_reference == f"fake:{run_id}"
            assert entry is not None and entry.status == "completed"
            assert entry.outcome_reference_id == run_id
            assert await session.scalar(
                select(func.count(AuditEvent.event_id)).where(
                    AuditEvent.event_type == "compute.run.dispatched",
                    AuditEvent.subject_id == run_id,
                )
            ) == 1
            dispatch_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.event_type == "compute.run.dispatched",
                    AuditEvent.subject_id == run_id,
                )
            )
            assert dispatch_event is not None
            targets = list(
                (
                    await session.scalars(
                        select(OutboxMessage.destination).where(
                            OutboxMessage.audit_event_id == dispatch_event.event_id
                        )
                    )
                ).all()
            )
            assert targets == ["audit.timeline"]

        async with engine.connect() as connection:
            transaction = await connection.begin()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(ComputeRun)
                    .where(ComputeRun.id == run_id)
                    .values(status="running", started_at=func.now(), row_version=ComputeRun.row_version + 1)
                )
                await transaction.commit()
            if transaction.is_active:
                await transaction.rollback()
    finally:
        await engine.dispose()
