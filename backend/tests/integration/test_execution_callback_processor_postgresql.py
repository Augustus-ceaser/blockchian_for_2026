from __future__ import annotations

import asyncio
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.execution import (
    DatasetRegistry,
    FakeExecutorAdapter,
    LocalBuiltInExecutorAdapter,
    ModelRegistry,
)
from app.execution.callback_processor import ExecutionCallbackWorker
from app.execution.coordinator import CONSUMER_NAME, ExecutionCoordinatorService
from app.messaging import OutboxEnvelope
from app.modules.audit import AuditEvent, OutboxMessage
from app.modules.callback_inbox import (
    ExecutionCallbackInboxEntry,
    claim_callback_batch,
    receive_execution_callback,
)
from app.modules.compute import Artifact, ComputeRun, prepare_compute_run, reserve_compute_run
from app.modules.inbox import claim_inbox_batch, receive_inbox_envelope
from tests.test_compute_models import _create_ready_job
from tests.test_contract_models import _system_audit_command

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="MEDTRUST_TEST_DATABASE_URL is not configured"),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_fake_executor_callback_loop_is_audited_and_idempotent() -> None:
    assert TEST_DATABASE_URL is not None
    run(_callback_loop(TEST_DATABASE_URL))


def test_local_builtin_executor_synthetic_self_test(tmp_path: Path) -> None:
    assert TEST_DATABASE_URL is not None
    run(_local_self_test(TEST_DATABASE_URL, tmp_path))


class _SyntheticQuarantineWriter:
    def upload(self, *, run_id, workspace_reference, manifest, manifest_digest):
        assert workspace_reference == f"workspace-output:{run_id}"
        assert manifest
        assert manifest_digest.startswith("sha256:")
        return f"test-quarantine/{run_id}/{manifest_digest.removeprefix('sha256:')}"


@pytest.mark.parametrize(
    ("callback_type", "expected_status", "expected_event"),
    [
        ("execution.failed", "failed", "compute.run.failed"),
        ("execution.interrupted", "interrupted", "compute.run.interrupted"),
    ],
)
def test_terminal_callbacks_are_audited_without_artifacts(
    callback_type: str, expected_status: str, expected_event: str
) -> None:
    assert TEST_DATABASE_URL is not None
    run(
        _terminal_callback(
            TEST_DATABASE_URL, callback_type, expected_status, expected_event
        )
    )


async def _callback_loop(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = FakeExecutorAdapter()
    coordinator = ExecutionCoordinatorService(session_maker=factory, executor=adapter)
    callback_worker = ExecutionCallbackWorker(factory)
    try:
        async with factory() as session:
            job, _, _, _, _, user = await _create_ready_job(
                session, number=f"CTR-CALLBACK-PG-{uuid4().hex}", run_limit=2
            )
            compute_run = await prepare_compute_run(session, job, created_by=user.id)
            await reserve_compute_run(
                session,
                compute_run,
                audit_command=_system_audit_command(
                    f"callback-reserve:{compute_run.id}", "medtrust.compute"
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
            message.lock_owner = "callback-loop-dispatcher"
            message.lease_expires_at = now + timedelta(seconds=60)
            message.row_version += 1
            await session.flush()
            received = await receive_inbox_envelope(
                session,
                consumer_name=CONSUMER_NAME,
                envelope=OutboxEnvelope.from_records(message, event),
            )
            run_id, space_id, correlation_id = compute_run.id, compute_run.space_id, event.correlation_id
            coordinator_entry_id = received.entry.id
            await session.commit()

        async with factory() as session:
            async with session.begin():
                claimed = await claim_inbox_batch(
                    session,
                    consumer_name=CONSUMER_NAME,
                    worker_id="callback-loop-coordinator",
                    batch_size=100,
                    lease_seconds=120,
                )
                assert coordinator_entry_id in {row.id for row in claimed}
        await coordinator.process_entry(
            entry_id=coordinator_entry_id, worker_id="callback-loop-coordinator"
        )

        started = adapter.build_callback(
            run_id=run_id,
            space_id=space_id,
            callback_type="execution.started",
            correlation_id=correlation_id,
            payload_snapshot={
                "schema_version": 1,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "runtime_summary": {"executor": "fake", "network": "denied"},
            },
        )
        async with factory() as session:
            async with session.begin():
                result = await receive_execution_callback(session, envelope=started)
                started_entry_id = result.entry.id
        async with factory() as session:
            async with session.begin():
                claimed = await claim_callback_batch(
                    session, worker_id="callback-loop-worker", batch_size=100, lease_seconds=120
                )
                assert started_entry_id in {row.id for row in claimed}
        started_result = await callback_worker.process_one(
            entry_id=started_entry_id, worker_id="callback-loop-worker"
        )
        assert started_result.outcome_code == "run_started"

        completed = adapter.build_callback(
            run_id=run_id,
            space_id=space_id,
            callback_type="execution.completed",
            correlation_id=correlation_id,
            payload_snapshot={
                "schema_version": 1,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "output_manifest": [
                    {
                        "name": "synthetic-model-summary",
                        "media_type": "application/json",
                        "size_bytes": 128,
                        "digest": f"sha256:{'c' * 64}",
                    }
                ],
                "output_digest": f"sha256:{'c' * 64}",
                "execution_summary": {"mode": "synthetic"},
                "resource_usage_summary": {"cpu_seconds": 1},
                "artifact_type": "model_artifact",
                "object_storage_ref": f"quarantine/{run_id}/synthetic-output.json",
            },
        )
        async with factory() as session:
            async with session.begin():
                result = await receive_execution_callback(session, envelope=completed)
                completed_entry_id = result.entry.id
        async with factory() as session:
            async with session.begin():
                claimed = await claim_callback_batch(
                    session, worker_id="callback-loop-worker", batch_size=100, lease_seconds=120
                )
                assert completed_entry_id in {row.id for row in claimed}
        completed_result = await callback_worker.process_one(
            entry_id=completed_entry_id, worker_id="callback-loop-worker"
        )
        assert completed_result.outcome_code == "run_completed"
        assert completed_result.artifact_id is not None

        async with factory() as session:
            run_row = await session.get(ComputeRun, run_id)
            artifact = await session.get(Artifact, completed_result.artifact_id)
            assert run_row is not None and run_row.status == "succeeded"
            assert artifact is not None and artifact.release_status == "quarantined"
            assert await session.scalar(
                select(func.count(AuditEvent.event_id)).where(
                    AuditEvent.subject_id == run_id,
                    AuditEvent.event_type.in_(("compute.run.started", "compute.run.completed")),
                )
            ) == 2
            assert await session.scalar(
                select(func.count(ExecutionCallbackInboxEntry.id)).where(
                    ExecutionCallbackInboxEntry.compute_run_id == run_id,
                    ExecutionCallbackInboxEntry.status == "completed",
                )
            ) == 2

        async with factory() as session:
            async with session.begin():
                replay = await receive_execution_callback(session, envelope=completed)
                assert replay.created is False
        async with engine.begin() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(ComputeRun)
                    .where(ComputeRun.id == run_id)
                    .values(status="failed", row_version=ComputeRun.row_version + 1)
                )
    finally:
        await engine.dispose()


async def _prepare_dispatched_run(factory, adapter: FakeExecutorAdapter):
    coordinator = ExecutionCoordinatorService(session_maker=factory, executor=adapter)
    async with factory() as session:
        job, _, _, _, _, user = await _create_ready_job(
            session, number=f"CTR-CALLBACK-TERM-{uuid4().hex}", run_limit=2
        )
        run_row = await prepare_compute_run(session, job, created_by=user.id)
        await reserve_compute_run(
            session,
            run_row,
            audit_command=_system_audit_command(
                f"callback-terminal-reserve:{run_row.id}", "medtrust.compute"
            ),
        )
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "compute.run.reserved",
                AuditEvent.subject_id == run_row.id,
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
        message.lock_owner = "callback-terminal-dispatcher"
        message.lease_expires_at = now + timedelta(seconds=60)
        message.row_version += 1
        await session.flush()
        received = await receive_inbox_envelope(
            session,
            consumer_name=CONSUMER_NAME,
            envelope=OutboxEnvelope.from_records(message, event),
        )
        run_id, space_id, correlation_id = run_row.id, run_row.space_id, event.correlation_id
        entry_id = received.entry.id
        await session.commit()
    async with factory() as session:
        async with session.begin():
            await claim_inbox_batch(
                session,
                consumer_name=CONSUMER_NAME,
                worker_id="callback-terminal-coordinator",
                batch_size=100,
                lease_seconds=120,
            )
    await coordinator.process_entry(
        entry_id=entry_id, worker_id="callback-terminal-coordinator"
    )
    return run_id, space_id, correlation_id


async def _terminal_callback(
    database_url: str,
    callback_type: str,
    expected_status: str,
    expected_event: str,
) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = FakeExecutorAdapter()
    worker = ExecutionCallbackWorker(factory)
    try:
        run_id, space_id, correlation_id = await _prepare_dispatched_run(factory, adapter)
        timestamp_key = "failed_at" if callback_type == "execution.failed" else "interrupted_at"
        envelope = adapter.build_callback(
            run_id=run_id,
            space_id=space_id,
            callback_type=callback_type,
            correlation_id=correlation_id,
            payload_snapshot={
                "schema_version": 1,
                timestamp_key: datetime.now(timezone.utc).isoformat(),
                "error_code": "synthetic_executor_signal",
                "error_summary": "synthetic test terminal callback",
            },
        )
        async with factory() as session:
            async with session.begin():
                received = await receive_execution_callback(session, envelope=envelope)
                entry_id = received.entry.id
        async with factory() as session:
            async with session.begin():
                claimed = await claim_callback_batch(
                    session,
                    worker_id="callback-terminal-worker",
                    batch_size=100,
                    lease_seconds=120,
                )
                assert entry_id in {row.id for row in claimed}
        await worker.process_one(entry_id=entry_id, worker_id="callback-terminal-worker")
        async with factory() as session:
            run_row = await session.get(ComputeRun, run_id)
            assert run_row is not None and run_row.status == expected_status
            assert await session.scalar(
                select(func.count(Artifact.id)).where(Artifact.compute_run_id == run_id)
            ) == 0
            assert await session.scalar(
                select(func.count(AuditEvent.event_id)).where(
                    AuditEvent.subject_id == run_id,
                    AuditEvent.event_type == expected_event,
                )
            ) == 1
    finally:
        await engine.dispose()


async def _local_self_test(database_url: str, tmp_path: Path) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    models, datasets = ModelRegistry(), DatasetRegistry()
    model = models.register(
        {
            "model_name": "platform synthetic statistics self-test",
            "model_version": "1.0",
            "model_digest": f"sha256:{'c' * 64}",
            "entrypoint_id": "builtin.synthetic_statistics.v1",
            "runtime": "python-built-in",
            "dependency_lock_digest": f"sha256:{'a' * 64}",
            "input_schema_version": "synthetic-numeric-array/v1",
            "output_schema_version": "synthetic-statistics/v1",
            "allowed_output_types": ["model_artifact"],
            "network_access": False,
            "cpu_limit": 1,
            "memory_limit": 512,
            "timeout_seconds": 30,
            "enabled": True,
        }
    )
    dataset = datasets.register(
        {
            "dataset_name": "platform synthetic numeric fixture",
            "dataset_version": "1.0",
            "manifest_digest": f"sha256:{'d' * 64}",
            "data_type": "synthetic_numeric_array",
            "input_schema_version": "synthetic-numeric-array/v1",
            "source_type": "synthetic_fixture",
            "public_or_authorized": "synthetic",
            "case_count": 10,
            "allowed_model_types": ["builtin.synthetic_statistics.v1"],
            "authorized_use": ["ai_training"],
            "enabled": True,
        }
    )
    adapter = LocalBuiltInExecutorAdapter(
        model_registry=models,
        dataset_registry=datasets,
        dataset_manifest_digest=dataset.manifest_digest,
        workspace_root=tmp_path / "local-executor-workspaces",
    )
    worker = ExecutionCallbackWorker(
        factory, artifact_writer=_SyntheticQuarantineWriter()
    )
    try:
        run_id, space_id, _ = await _prepare_dispatched_run(factory, adapter)
        started, completed = await adapter.execute_self_test(f"local-builtin:{run_id}")
        for envelope, expected in ((started, "run_started"), (completed, "run_completed")):
            async with factory() as session:
                async with session.begin():
                    received = await receive_execution_callback(session, envelope=envelope)
                    entry_id = received.entry.id
            async with factory() as session:
                async with session.begin():
                    claimed = await claim_callback_batch(
                        session,
                        worker_id="local-self-test-callback-worker",
                        batch_size=100,
                        lease_seconds=120,
                    )
                    assert entry_id in {row.id for row in claimed}
            result = await worker.process_one(
                entry_id=entry_id, worker_id="local-self-test-callback-worker"
            )
            assert result.outcome_code == expected
        async with factory() as session:
            run_row = await session.get(ComputeRun, run_id)
            assert run_row is not None and run_row.status == "succeeded"
            artifacts = list(
                (
                    await session.scalars(
                        select(Artifact).where(Artifact.compute_run_id == run_id)
                    )
                ).all()
            )
            assert len(artifacts) == 1
            assert artifacts[0].release_status == "quarantined"
            chain = (
                await session.execute(
                    text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
                    {"space_id": space_id},
                )
            ).mappings().one()
            assert chain["is_valid"] is True
        adapter.cleanup(run_id)
        assert not (tmp_path / "local-executor-workspaces" / str(run_id)).exists()
        assert model.entrypoint_id == "builtin.synthetic_statistics.v1"
    finally:
        await engine.dispose()
