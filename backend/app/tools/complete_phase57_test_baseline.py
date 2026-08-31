from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

import yaml
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.demo.phase4 import get_phase4_context
from app.execution import DatasetRegistry, LocalBuiltInExecutorAdapter, ModelRegistry
from app.execution.callback_processor import ExecutionCallbackWorker
from app.execution.coordinator import (
    ExecutionCoordinatorConsumer,
    ExecutionCoordinatorService,
)
from app.execution.pathmnist import PathMNISTAssetBinding
from app.execution.quarantine import MinioQuarantineArtifactWriter
from app.messaging import OutboxEnvelope, PublishResult
from app.modules.callback_inbox import claim_callback_batch, receive_execution_callback
from app.modules.compute import Artifact, ComputeJob, ComputeRun
from app.modules.compute.readiness import request_controlled_dispatch
from app.tools.preflight_model_onboarding import run_pathmnist_preflight
from app.workers.outbox_dispatcher import DispatcherConfig, OutboxDispatcher


class _BaselinePublisher:
    def __init__(self, coordinator: ExecutionCoordinatorConsumer) -> None:
        self._coordinator = coordinator

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        if message.destination == "compute.dispatch":
            return await self._coordinator.publish(message)
        return PublishResult.acknowledged_result(
            external_message_id=f"phase57-baseline:{message.message_id}"
        )


async def _dispatch_until_idle(dispatcher: OutboxDispatcher) -> None:
    for _ in range(20):
        if await dispatcher.dispatch_once() == 0:
            return
    raise RuntimeError("Phase 5.7 baseline dispatcher did not become idle")


async def _process_callback(
    factory: async_sessionmaker,
    worker: ExecutionCallbackWorker,
    envelope,
    *,
    worker_id: str,
) -> UUID:
    async with factory() as session:
        async with session.begin():
            received = await receive_execution_callback(session, envelope=envelope)
            entry_id = received.entry.id
    async with factory() as session:
        async with session.begin():
            claimed = await claim_callback_batch(
                session,
                worker_id=worker_id,
                batch_size=10,
                lease_seconds=120,
            )
            if entry_id not in {row.id for row in claimed}:
                raise RuntimeError("Phase 5.7 callback was not claimed")
    result = await worker.process_one(entry_id=entry_id, worker_id=worker_id)
    if result.artifact_id is None and envelope.callback_type == "execution.completed":
        raise RuntimeError("Completed callback did not create an Artifact")
    return result.artifact_id


async def _run(args: argparse.Namespace) -> dict[str, object]:
    root = args.repository_root.resolve(strict=True)
    registry_root = root / "registered_assets"
    smoke_root = root / "smoke_test_plans"
    model_manifest_path = (
        registry_root / "pathmnist_resnet18_v1" / "model_manifest.yaml"
    )
    dataset_manifest_path = (
        registry_root / "pathmnist_v1" / "dataset_manifest.json"
    )
    dependency_lock = (
        registry_root / "pathmnist_resnet18_v1" / "runtime_requirements.lock"
    )
    smoke_plan_path = smoke_root / "pathmnist_resnet18_20.yaml"
    preflight = run_pathmnist_preflight(
        model_manifest_path,
        dataset_manifest_path,
        smoke_plan_path,
        model_asset=args.model_asset,
        dataset_asset=args.dataset_asset,
        dependency_lock=dependency_lock,
        registry_root=registry_root,
        smoke_plan_root=smoke_root,
    )

    model_document = yaml.safe_load(model_manifest_path.read_text(encoding="utf-8"))
    dataset_document = json.loads(
        dataset_manifest_path.read_text(encoding="utf-8")
    )
    smoke_plan = yaml.safe_load(smoke_plan_path.read_text(encoding="utf-8"))
    models, datasets = ModelRegistry(), DatasetRegistry()
    model = models.register(model_document)
    dataset = datasets.register(dataset_document)

    engine = create_async_engine(args.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    workspace_root = root / ".runtime" / "phase57-test-baseline" / "workspaces"
    adapter = LocalBuiltInExecutorAdapter(
        model_registry=models,
        dataset_registry=datasets,
        dataset_manifest_digest=dataset.manifest_digest,
        workspace_root=workspace_root,
        pathmnist_asset_binding=PathMNISTAssetBinding(
            dataset_path=args.dataset_asset,
            model_path=args.model_asset,
            dataset_digest=dataset.manifest_digest,
            model_digest=model.model_digest,
        ),
        pathmnist_test_indices=tuple(smoke_plan["test_indices"]),
    )
    coordinator_consumer = ExecutionCoordinatorConsumer(factory)
    coordinator = ExecutionCoordinatorService(
        session_maker=factory,
        executor=adapter,
    )
    dispatcher = OutboxDispatcher(
        session_maker=factory,
        publisher=_BaselinePublisher(coordinator_consumer),
        config=DispatcherConfig(
            worker_id="phase57-baseline-dispatcher",
            batch_size=100,
            lease_seconds=120,
        ),
    )
    callback_worker = ExecutionCallbackWorker(
        factory,
        artifact_writer=MinioQuarantineArtifactWriter(
            endpoint=args.minio_endpoint,
            access_key=args.minio_access_key,
            secret_key=args.minio_secret_key,
            secure=False,
            bucket_name=args.minio_quarantine_bucket,
            workspace_root=workspace_root,
        ),
    )
    run_ids: list[UUID] = []
    artifact_ids: list[UUID] = []
    try:
        async with factory() as session:
            if await session.scalar(
                text("SELECT version_num FROM alembic_version")
            ) != "20260725_0032":
                raise RuntimeError("Phase 5.7 baseline database is not at current head")
            jobs = list(
                (
                    await session.scalars(
                        select(ComputeJob).order_by(ComputeJob.created_at)
                    )
                ).all()
            )
            if len(jobs) != 2:
                raise RuntimeError("Phase 5.7 baseline requires exactly two prepared Jobs")
            if await session.scalar(select(func.count(ComputeRun.id))) != 0:
                raise RuntimeError("Phase 5.7 baseline database already contains Runs")
            if await session.scalar(select(func.count(Artifact.id))) != 0:
                raise RuntimeError("Phase 5.7 baseline database already contains Artifacts")

        async with factory() as session:
            async with session.begin():
                context = await get_phase4_context(session)
                operator = context.actors["space_operator"]
                jobs = list(
                    (
                        await session.scalars(
                            select(ComputeJob).order_by(ComputeJob.created_at)
                        )
                    ).all()
                )
                for index, job in enumerate(jobs, start=1):
                    run, replayed = await request_controlled_dispatch(
                        session,
                        job,
                        operator=operator,
                        raw_key=f"phase57-baseline-dispatch-{index}",
                    )
                    if replayed:
                        raise RuntimeError("Phase 5.7 baseline dispatch unexpectedly replayed")
                    run_ids.append(run.id)

        await _dispatch_until_idle(dispatcher)
        processed = await coordinator.claim_and_process_once(
            worker_id="phase57-baseline-coordinator",
            batch_size=10,
            lease_seconds=120,
        )
        if processed != 2:
            raise RuntimeError(f"Expected two coordinated Runs, received {processed}")
        pending = adapter.pending_execution_ids()
        if len(pending) != 2:
            raise RuntimeError("Controlled executor did not accept exactly two Runs")

        for index, external_id in enumerate(pending, start=1):
            started, completed = await adapter.execute_self_test(external_id)
            await _process_callback(
                factory,
                callback_worker,
                started,
                worker_id=f"phase57-baseline-callback-{index}",
            )
            artifact_id = await _process_callback(
                factory,
                callback_worker,
                completed,
                worker_id=f"phase57-baseline-callback-{index}",
            )
            artifact_ids.append(artifact_id)

        await _dispatch_until_idle(dispatcher)
        async with factory() as session:
            jobs = int(await session.scalar(select(func.count(ComputeJob.id))) or 0)
            runs = int(await session.scalar(select(func.count(ComputeRun.id))) or 0)
            artifacts = int(
                await session.scalar(
                    select(func.count(Artifact.id)).where(
                        Artifact.release_status == "quarantined"
                    )
                )
                or 0
            )
            succeeded_jobs = int(
                await session.scalar(
                    select(func.count(ComputeJob.id)).where(
                        ComputeJob.status == "succeeded"
                    )
                )
                or 0
            )
            succeeded_runs = int(
                await session.scalar(
                    select(func.count(ComputeRun.id)).where(
                        ComputeRun.status == "succeeded"
                    )
                )
                or 0
            )
            context = await get_phase4_context(session)
            chain = (
                await session.execute(
                    text(
                        "SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"
                    ),
                    {"space_id": context.space_id},
                )
            ).mappings().one()
            if (jobs, runs, artifacts, succeeded_jobs, succeeded_runs) != (2, 2, 2, 2, 2):
                raise RuntimeError("Phase 5.7 baseline terminal counts are invalid")
            if chain["is_valid"] is not True:
                raise RuntimeError("Phase 5.7 baseline audit chain is invalid")
        return {
            "ready": True,
            "jobs": jobs,
            "runs": runs,
            "artifacts": artifacts,
            "run_ids": [str(item) for item in run_ids],
            "artifact_ids": [str(item) for item in artifact_ids],
            "audit_chain_valid": True,
            "hard_isolation": False,
            "preflight": preflight,
        }
    finally:
        for run_id in run_ids:
            adapter.cleanup(run_id)
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--dataset-asset", required=True, type=Path)
    parser.add_argument("--model-asset", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--executor-site-packages", required=True, type=Path)
    parser.add_argument("--minio-endpoint", default="127.0.0.1:9000")
    parser.add_argument("--minio-access-key", default="medtrust")
    parser.add_argument("--minio-secret-key", default="medtrust_dev_only")
    parser.add_argument(
        "--minio-quarantine-bucket",
        default="medtrust-phase56-quarantined-results",
    )
    args = parser.parse_args()
    executor_site = args.executor_site_packages.resolve(strict=True)
    if str(executor_site) not in sys.path:
        sys.path.append(str(executor_site))
    try:
        import torch  # noqa: F401
    except ImportError as exc:
        raise SystemExit("Controlled PyTorch runtime is unavailable") from exc
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
