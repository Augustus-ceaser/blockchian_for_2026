from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.execution import DatasetRegistry, LocalBuiltInExecutorAdapter, ModelRegistry
from app.execution.callback_processor import ExecutionCallbackWorker
from app.execution.coordinator import (
    CONSUMER_NAME,
    ExecutionCoordinatorConsumer,
    ExecutionCoordinatorService,
)
from app.execution.pathmnist import PathMNISTAssetBinding
from app.execution.quarantine import MinioQuarantineArtifactWriter
from app.messaging import OutboxEnvelope, PublishResult
from app.modules.audit import AuditEvent, OutboxMessage
from app.modules.callback_inbox import (
    ExecutionCallbackInboxEntry,
    claim_callback_batch,
    receive_execution_callback,
)
from app.modules.compute import (
    Artifact,
    ComputeJob,
    ComputeRun,
    create_compute_job,
    prepare_compute_run,
    reserve_compute_run,
    validate_compute_job,
)
from app.modules.inbox import ConsumerInboxEntry
from app.workers.outbox_dispatcher import DispatcherConfig, OutboxDispatcher
from tests.test_compute_models import _make_active_compute_contract
from tests.test_contract_models import _system_audit_command


TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")
DATASET_PATH = os.getenv("MEDTRUST_PATHMNIST_DATASET_PATH")
MODEL_PATH = os.getenv("MEDTRUST_PATHMNIST_MODEL_PATH")
DATASET_MANIFEST_PATH = os.getenv("MEDTRUST_PATHMNIST_DATASET_MANIFEST")
MODEL_MANIFEST_PATH = os.getenv("MEDTRUST_PATHMNIST_MODEL_MANIFEST")
SMOKE_PLAN_PATH = os.getenv("MEDTRUST_PATHMNIST_SMOKE_PLAN")
RESULT_PATH = os.getenv("MEDTRUST_PATHMNIST_RESULT_PATH")
MINIO_ENDPOINT = os.getenv("MEDTRUST_MINIO_ENDPOINT")
MINIO_ACCESS_KEY = os.getenv("MEDTRUST_MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MEDTRUST_MINIO_SECRET_KEY")
MINIO_QUARANTINE_BUCKET = os.getenv("MEDTRUST_MINIO_QUARANTINE_BUCKET")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(
            (
                TEST_DATABASE_URL,
                DATASET_PATH,
                MODEL_PATH,
                DATASET_MANIFEST_PATH,
                MODEL_MANIFEST_PATH,
                SMOKE_PLAN_PATH,
                RESULT_PATH,
                MINIO_ENDPOINT,
                MINIO_ACCESS_KEY,
                MINIO_SECRET_KEY,
                MINIO_QUARANTINE_BUCKET,
            )
        ),
        reason="the explicit PathMNIST controlled-smoke environment is not configured",
    ),
]


class _SmokeRoutingPublisher:
    """Route only compute.dispatch into its durable Inbox; ACK audit projections locally."""

    def __init__(self, coordinator_consumer: ExecutionCoordinatorConsumer) -> None:
        self._coordinator_consumer = coordinator_consumer
        self.target_run_id = None
        self.compute_delivery_attempts = 0
        self.compute_duplicate_acks = 0

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        if message.destination == "compute.dispatch":
            if message.subject_id != self.target_run_id:
                return PublishResult.acknowledged_result(
                    external_message_id=f"controlled-smoke:ignored:{message.message_id}"
                )
            self.compute_delivery_attempts += 1
            first = await self._coordinator_consumer.publish(message)
            second = await self._coordinator_consumer.publish(message)
            if first.acknowledged and second.acknowledged:
                self.compute_duplicate_acks += 1
                return first
            return PublishResult.failed_result(
                "controlled_smoke_inbox_delivery_failed", retryable=False
            )
        return PublishResult.acknowledged_result(
            external_message_id=f"controlled-smoke:{message.destination}:{message.message_id}"
        )


def test_pathmnist_fixed_resnet18_controlled_smoke(tmp_path: Path) -> None:
    assert TEST_DATABASE_URL is not None
    asyncio.run(_controlled_smoke(TEST_DATABASE_URL, tmp_path))


async def _dispatch_until_idle(dispatcher: OutboxDispatcher) -> int:
    total = 0
    for _ in range(20):
        count = await dispatcher.dispatch_once()
        total += count
        if count == 0:
            return total
    raise AssertionError("controlled smoke dispatcher did not become idle")


async def _process_callback(
    factory,
    worker: ExecutionCallbackWorker,
    envelope,
    *,
    worker_id: str,
):
    async with factory() as session:
        async with session.begin():
            received = await receive_execution_callback(session, envelope=envelope)
            entry_id = received.entry.id
    async with factory() as session:
        async with session.begin():
            claimed = await claim_callback_batch(
                session, worker_id=worker_id, batch_size=10, lease_seconds=120
            )
            assert entry_id in {row.id for row in claimed}
    return await worker.process_one(entry_id=entry_id, worker_id=worker_id)


async def _controlled_smoke(database_url: str, tmp_path: Path) -> None:
    assert all(
        (
            DATASET_PATH,
            MODEL_PATH,
            DATASET_MANIFEST_PATH,
            MODEL_MANIFEST_PATH,
            SMOKE_PLAN_PATH,
            RESULT_PATH,
        )
    )
    dataset_path = Path(DATASET_PATH)
    model_path = Path(MODEL_PATH)
    dataset_manifest = json.loads(Path(DATASET_MANIFEST_PATH).read_text(encoding="utf-8"))
    model_manifest = yaml.safe_load(Path(MODEL_MANIFEST_PATH).read_text(encoding="utf-8"))
    smoke_plan = yaml.safe_load(Path(SMOKE_PLAN_PATH).read_text(encoding="utf-8"))
    assert isinstance(dataset_manifest, dict)
    assert isinstance(model_manifest, dict)
    assert isinstance(smoke_plan, dict)

    models, datasets = ModelRegistry(), DatasetRegistry()
    model = models.register(model_manifest)
    dataset = datasets.register(dataset_manifest)
    indices = tuple(smoke_plan["test_indices"])
    assert len(indices) == 20
    workspace_root = tmp_path / "pathmnist-controlled-workspaces"
    adapter = LocalBuiltInExecutorAdapter(
        model_registry=models,
        dataset_registry=datasets,
        dataset_manifest_digest=dataset.manifest_digest,
        workspace_root=workspace_root,
        pathmnist_asset_binding=PathMNISTAssetBinding(
            dataset_path=dataset_path,
            model_path=model_path,
            dataset_digest=dataset.manifest_digest,
            model_digest=model.model_digest,
        ),
        pathmnist_test_indices=indices,
    )

    engine = create_async_engine(database_url, pool_size=6, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    coordinator_consumer = ExecutionCoordinatorConsumer(factory)
    routing_publisher = _SmokeRoutingPublisher(coordinator_consumer)
    dispatcher = OutboxDispatcher(
        session_maker=factory,
        publisher=routing_publisher,
        config=DispatcherConfig(
            worker_id="pathmnist-controlled-dispatcher",
            batch_size=100,
            lease_seconds=120,
        ),
    )
    coordinator = ExecutionCoordinatorService(session_maker=factory, executor=adapter)
    callback_worker = ExecutionCallbackWorker(
        factory,
        artifact_writer=MinioQuarantineArtifactWriter(
            endpoint=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False,
            bucket_name=MINIO_QUARANTINE_BUCKET,
            workspace_root=workspace_root,
        ),
    )
    run_id = None
    try:
        async with factory() as session:
            _, revision, contract_object, consumer, connector, user = (
                await _make_active_compute_contract(
                    session,
                    number=f"CTR-PATHMNIST-{uuid4().hex}",
                    run_limit=1,
                    algorithm_digest=model.model_digest,
                    purpose_code="model_validation",
                )
            )
            algorithm_spec = {
                "schema_version": "algorithm-spec/v1",
                "algorithm_name": model.model_name,
                "algorithm_version": model.model_version,
                "algorithm_digest": model.model_digest,
                "registration_digest": model.registration_digest,
                "entrypoint_id": model.entrypoint_id,
                "execution_profile": "local_builtin_cpu_inference",
                "declared_output_types": ["model_artifact"],
            }
            job = await create_compute_job(
                session,
                revision_id=revision.id,
                party_id=consumer.id,
                contract_object_id=contract_object.id,
                requester_organization_id=consumer.organization_id,
                requester_user_id=user.id,
                purpose_code="model_validation",
                requested_output_types=["model_artifact"],
                algorithm_spec_snapshot=algorithm_spec,
                audit_command=_system_audit_command(
                    f"pathmnist-create-job:{uuid4().hex}", "medtrust.compute"
                ),
            )
            await validate_compute_job(session, job)
            run_row = await prepare_compute_run(session, job, created_by=user.id)
            await reserve_compute_run(
                session,
                run_row,
                audit_command=_system_audit_command(
                    f"pathmnist-reserve:{run_row.id}", "medtrust.compute"
                ),
            )
            await session.refresh(run_row)
            assert run_row.reservation_ordinal == 1
            run_id = run_row.id
            space_id = run_row.space_id
            connector_id = connector.id
            await session.commit()

        routing_publisher.target_run_id = run_id
        await _dispatch_until_idle(dispatcher)
        assert routing_publisher.compute_delivery_attempts == 1
        assert routing_publisher.compute_duplicate_acks == 1

        async with factory() as session:
            assert await session.scalar(
                select(func.count(ConsumerInboxEntry.id)).where(
                    ConsumerInboxEntry.consumer_name == CONSUMER_NAME,
                    ConsumerInboxEntry.space_id == space_id,
                )
            ) == 1
        assert await coordinator.claim_and_process_once(
            worker_id="pathmnist-controlled-coordinator",
            batch_size=10,
            lease_seconds=120,
        ) == 1

        started, completed = await adapter.execute_self_test(f"local-builtin:{run_id}")
        started_result = await _process_callback(
            factory,
            callback_worker,
            started,
            worker_id="pathmnist-controlled-callback-worker",
        )
        assert started_result.outcome_code == "run_started"
        completed_result = await _process_callback(
            factory,
            callback_worker,
            completed,
            worker_id="pathmnist-controlled-callback-worker",
        )
        assert completed_result.outcome_code == "run_completed"
        assert completed_result.artifact_id is not None

        async with factory() as session:
            async with session.begin():
                replay = await receive_execution_callback(session, envelope=completed)
                assert replay.created is False

        output_dir = workspace_root / str(run_id) / "output"
        aggregate_metrics = json.loads(
            (output_dir / "aggregate_metrics.json").read_text(encoding="utf-8")
        )
        confusion_matrix = (
            output_dir / "confusion_matrix.csv"
        ).read_text(encoding="utf-8")
        execution_summary = json.loads(
            (output_dir / "execution_summary.json").read_text(encoding="utf-8")
        )
        assert set(path.name for path in output_dir.iterdir()) == {
            "aggregate_metrics.json",
            "confusion_matrix.csv",
            "execution_summary.json",
        }
        assert confusion_matrix.startswith("expected/predicted,")

        await _dispatch_until_idle(dispatcher)
        async with factory() as session:
            run_row = await session.get(ComputeRun, run_id)
            job_row = await session.get(
                ComputeJob, run_row.compute_job_id if run_row else None
            )
            artifact = await session.get(Artifact, completed_result.artifact_id)
            assert run_row is not None and run_row.status == "succeeded"
            assert run_row.reservation_ordinal == 1
            assert job_row is not None and job_row.status == "succeeded"
            assert artifact is not None and artifact.release_status == "quarantined"
            assert artifact.released_at is None and artifact.release_evidence is None
            assert artifact.content_digest == completed.payload_snapshot["output_digest"]
            assert artifact.storage_reference.startswith(
                f"minio-quarantine/{MINIO_QUARANTINE_BUCKET}/quarantine/{run_id}/"
            )
            assert await session.scalar(
                select(func.count(Artifact.id)).where(Artifact.compute_run_id == run_id)
            ) == 1
            assert await session.scalar(
                select(func.count(ExecutionCallbackInboxEntry.id)).where(
                    ExecutionCallbackInboxEntry.compute_run_id == run_id
                )
            ) == 2
            expected_events = {
                "contract.revision.activated",
                "compute.job.created",
                "compute.run.reserved",
                "compute.run.dispatched",
                "compute.run.started",
                "compute.run.completed",
                "artifact.created",
            }
            actual_events = set(
                (
                    await session.scalars(
                        select(AuditEvent.event_type).where(AuditEvent.space_id == space_id)
                    )
                ).all()
            )
            assert expected_events.issubset(actual_events)
            assert await session.scalar(
                select(func.count(OutboxMessage.message_id)).where(
                    OutboxMessage.space_id == space_id,
                    OutboxMessage.status != "published",
                )
            ) == 0
            chain = (
                await session.execute(
                    text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
                    {"space_id": space_id},
                )
            ).mappings().one()
            assert chain["is_valid"] is True

        from minio import Minio

        object_prefix = artifact.storage_reference.split(
            f"minio-quarantine/{MINIO_QUARANTINE_BUCKET}/", 1
        )[1]
        objects = list(
            Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=False,
            ).list_objects(
                MINIO_QUARANTINE_BUCKET,
                prefix=f"{object_prefix}/",
                recursive=True,
            )
        )
        assert {item.object_name.rsplit("/", 1)[-1] for item in objects} == {
            "aggregate_metrics.json",
            "confusion_matrix.csv",
            "execution_summary.json",
        }

        result = {
            "schema_version": "pathmnist-controlled-smoke-result/v1",
            "run_id": str(run_id),
            "artifact_id": str(completed_result.artifact_id),
            "connector_id": str(connector_id),
            "sample_count": aggregate_metrics["sample_count"],
            "accuracy": aggregate_metrics["accuracy"],
            "mean_confidence": aggregate_metrics["mean_confidence"],
            "confusion_matrix": aggregate_metrics["confusion_matrix"],
            "confusion_matrix_csv_digest": next(
                item["digest"]
                for item in completed.payload_snapshot["output_manifest"]
                if item["name"] == "confusion_matrix.csv"
            ),
            "prediction_digest": aggregate_metrics["prediction_digest"],
            "resource_usage": execution_summary["resource_usage"],
            "artifact_status": "quarantined",
            "consumer_inbox_rows": 1,
            "callback_inbox_rows": 2,
            "duplicate_outbox_delivery_deduplicated": True,
            "duplicate_callback_deduplicated": True,
            "audit_chain_valid": True,
        }
        Path(RESULT_PATH).write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
    finally:
        if run_id is not None:
            adapter.cleanup(run_id)
        await engine.dispose()
