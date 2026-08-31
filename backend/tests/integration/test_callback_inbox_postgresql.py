from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.execution.callback import ExecutionCallbackEnvelope
from app.modules.callback_inbox import (
    CallbackInboxIdempotencyConflict,
    CallbackInboxInvariantError,
    claim_callback_batch,
    complete_callback,
    receive_execution_callback,
    reclaim_expired_callbacks,
)
from app.modules.compute import prepare_compute_run
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


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _envelope(run, *, callback_id: str = "callback-1", namespace: str = "fake"):
    return ExecutionCallbackEnvelope(
        space_id=run.space_id,
        compute_run_id=run.id,
        executor_namespace=namespace,
        external_execution_id=f"exec-{run.id}",
        callback_id=callback_id,
        callback_type="execution.started",
        callback_schema_version=1,
        occurred_at=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
        payload_snapshot={
            "schema_version": 1,
            "started_at": "2026-07-22T12:00:00Z",
            "runtime_summary": {"executor": "test"},
        },
        execution_evidence_digest=_digest("a"),
        authentication_evidence_digest=_digest("b"),
        correlation_id=uuid4(),
    )


def test_callback_inbox_schema_receive_and_database_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_schema_receive_and_guards(TEST_DATABASE_URL))


def test_callback_inbox_skip_locked_reclaim_and_terminal_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_lifecycle_and_concurrency(TEST_DATABASE_URL))


async def _schema_receive_and_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260725_0032"
            assert await connection.scalar(text("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='medtrust'")) == 54
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                job, _, _, _, _, user = await _create_ready_job(
                    session, number=f"CTR-CALLBACK-PG-{uuid4().hex}", run_limit=3
                )
                compute_run = await prepare_compute_run(session, job, created_by=user.id)
                envelope = _envelope(compute_run)
                first = await receive_execution_callback(session, envelope=envelope)
                replay = await receive_execution_callback(session, envelope=envelope)
                duplicate_fact = await receive_execution_callback(
                    session, envelope=replace(envelope, callback_id="callback-2")
                )
                other_namespace = await receive_execution_callback(
                    session, envelope=replace(envelope, executor_namespace="fake-shadow")
                )
                assert first.created and not replay.created
                assert duplicate_fact.duplicate_fact and duplicate_fact.entry.id == first.entry.id
                assert other_namespace.created
                with pytest.raises(CallbackInboxIdempotencyConflict):
                    await receive_execution_callback(
                        session,
                        envelope=replace(
                            envelope,
                            payload_snapshot={
                                "schema_version": 1,
                                "started_at": "changed",
                                "runtime_summary": {"executor": "test"},
                            },
                        ),
                    )
                entry_id = first.entry.id

                savepoint = await session.begin_nested()
                try:
                    with pytest.raises(DBAPIError):
                        await session.execute(
                            text(
                                "UPDATE medtrust.execution_callback_inbox_entries "
                                "SET payload_digest=:digest,row_version=row_version+1 WHERE id=:id"
                            ),
                            {"id": entry_id, "digest": _digest("f")},
                        )
                finally:
                    await savepoint.rollback()
                savepoint = await session.begin_nested()
                try:
                    with pytest.raises(DBAPIError):
                        await session.execute(
                            text(
                                "UPDATE medtrust.execution_callback_inbox_entries "
                                "SET space_id=:space_id,row_version=row_version+1 WHERE id=:id"
                            ),
                            {"id": entry_id, "space_id": uuid4()},
                        )
                finally:
                    await savepoint.rollback()
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _lifecycle_and_concurrency(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job, _, _, _, _, user = await _create_ready_job(
                session, number=f"CTR-CALLBACK-LEASE-PG-{uuid4().hex}", run_limit=3
            )
            compute_run = await prepare_compute_run(session, job, created_by=user.id)
            received = await receive_execution_callback(
                session, envelope=_envelope(compute_run, callback_id=f"callback-{uuid4().hex}")
            )
            entry_id = received.entry.id
            run_id = compute_run.id
            await session.commit()

        async with factory() as first, factory() as second:
            async with first.begin(), second.begin():
                first_claim = await claim_callback_batch(
                    first, worker_id="callback-worker-a", batch_size=1, lease_seconds=-1
                )
                second_claim = await claim_callback_batch(
                    second, worker_id="callback-worker-b", batch_size=1
                )
                assert len(first_claim) == 1 and second_claim == []

        async with factory() as session:
            reclaimed = await reclaim_expired_callbacks(
                session, worker_id="callback-worker-b", batch_size=1
            )
            assert len(reclaimed) == 1
            await session.commit()

        async with factory() as session:
            with pytest.raises(CallbackInboxInvariantError):
                await complete_callback(
                    session,
                    entry_id=entry_id,
                    worker_id="callback-worker-a",
                    outcome_code="run_started",
                )

        async with factory() as session:
            await complete_callback(
                session,
                entry_id=entry_id,
                worker_id="callback-worker-b",
                outcome_code="run_started",
                outcome_reference_type="compute_run",
                outcome_reference_id=run_id,
            )
            await session.commit()

        async with engine.begin() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text(
                        "UPDATE medtrust.execution_callback_inbox_entries SET "
                        "status='received',completed_at=NULL,terminal_at=NULL,outcome_code=NULL,"
                        "outcome_reference_type=NULL,outcome_reference_id=NULL,row_version=row_version+1 "
                        "WHERE id=:id"
                    ),
                    {"id": entry_id},
                )
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text("DELETE FROM medtrust.execution_callback_inbox_entries WHERE id=:id"),
                    {"id": entry_id},
                )
    finally:
        await engine.dispose()
