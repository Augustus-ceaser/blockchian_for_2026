from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

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
from tests.test_application_models import create_schema, make_engine
from tests.test_compute_models import _create_ready_job


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


def test_callback_receive_identity_and_semantic_idempotency() -> None:
    run(_callback_receive_identity_and_semantic_idempotency())


async def _callback_receive_identity_and_semantic_idempotency() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job, _, _, _, _, user = await _create_ready_job(
                session, number=f"CTR-CALLBACK-{uuid4().hex}", run_limit=2
            )
            compute_run = await prepare_compute_run(session, job, created_by=user.id)
            envelope = _envelope(compute_run)
            first = await receive_execution_callback(session, envelope=envelope)
            replay = await receive_execution_callback(session, envelope=envelope)
            same_fact = await receive_execution_callback(
                session, envelope=replace(envelope, callback_id="callback-2")
            )
            other_namespace = await receive_execution_callback(
                session,
                envelope=replace(envelope, executor_namespace="fake-shadow"),
            )
            assert first.created
            assert not replay.created and replay.entry.id == first.entry.id
            assert same_fact.duplicate_fact and same_fact.entry.id == first.entry.id
            assert other_namespace.created
            with pytest.raises(CallbackInboxIdempotencyConflict):
                await receive_execution_callback(
                    session,
                    envelope=replace(
                        envelope,
                        payload_snapshot={
                            "schema_version": 1,
                            "started_at": "2026-07-22T12:01:00Z",
                            "runtime_summary": {"executor": "test"},
                        },
                    ),
                )
    finally:
        await engine.dispose()


def test_callback_claim_reclaim_complete_and_stale_owner() -> None:
    run(_callback_claim_reclaim_complete_and_stale_owner())


async def _callback_claim_reclaim_complete_and_stale_owner() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job, _, _, _, _, user = await _create_ready_job(
                session, number=f"CTR-CALLBACK-LEASE-{uuid4().hex}", run_limit=2
            )
            compute_run = await prepare_compute_run(session, job, created_by=user.id)
            received = await receive_execution_callback(
                session, envelope=_envelope(compute_run)
            )
            entry_id = received.entry.id
            await session.commit()

        async with factory() as session:
            claimed = await claim_callback_batch(
                session, worker_id="old-worker", batch_size=1
            )
            assert len(claimed) == 1
            claimed[0].lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        async with factory() as session:
            reclaimed = await reclaim_expired_callbacks(
                session, worker_id="new-worker", batch_size=1
            )
            assert len(reclaimed) == 1 and reclaimed[0].attempt_count == 2
            await complete_callback(
                session,
                entry_id=entry_id,
                worker_id="new-worker",
                outcome_code="run_started",
                outcome_reference_type="compute_run",
                outcome_reference_id=compute_run.id,
            )
            await session.commit()

        async with factory() as session:
            with pytest.raises(CallbackInboxInvariantError):
                await complete_callback(
                    session,
                    entry_id=entry_id,
                    worker_id="old-worker",
                    outcome_code="run_started",
                )
    finally:
        await engine.dispose()
