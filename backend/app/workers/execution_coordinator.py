from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket

import yaml
from sqlalchemy import select

from app.db.session import close_database, session_factory
from app.execution import (
    DatasetRegistry,
    FakeExecutorAdapter,
    LocalBuiltInExecutorAdapter,
    ModelRegistry,
    PathMNISTAssetBinding,
)
from app.execution.coordinator import ExecutionCoordinatorService
from app.modules.callback_inbox import receive_execution_callback
from app.modules.connectors.models import Connector

logger = logging.getLogger("medtrust.execution_coordinator")


def _build_pathmnist_executor() -> LocalBuiltInExecutorAdapter:
    repository_root = Path(__file__).resolve().parents[3]
    dataset_manifest = json.loads(
        (repository_root / "registered_assets/pathmnist_v1/dataset_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    model_manifest = yaml.safe_load(
        (repository_root / "registered_assets/pathmnist_resnet18_v1/model_manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    smoke_plan = yaml.safe_load(
        (repository_root / "smoke_test_plans/pathmnist_resnet18_20.yaml").read_text(
            encoding="utf-8"
        )
    )
    models = ModelRegistry()
    datasets = DatasetRegistry()
    models.register(model_manifest)
    datasets.register(dataset_manifest)
    dataset_path = Path(
        os.getenv(
            "MEDTRUST_PATHMNIST_DATASET_PATH",
            r"D:\MedTrustAssets\pathmnist_smoke\data\pathmnist.npz",
        )
    )
    model_path = Path(
        os.getenv(
            "MEDTRUST_PATHMNIST_MODEL_PATH",
            r"D:\MedTrustAssets\pathmnist_smoke\model\resnet18_28_1.pth",
        )
    )
    binding = PathMNISTAssetBinding(
        dataset_path=dataset_path,
        model_path=model_path,
        dataset_digest=dataset_manifest["manifest_digest"],
        model_digest=model_manifest["model_digest"],
    )
    binding.validate()
    workspace_root = Path(
        os.getenv(
            "MEDTRUST_LOCAL_EXECUTOR_WORKSPACE",
            str(repository_root / ".runtime/pathmnist-demo-workspaces"),
        )
    )
    return LocalBuiltInExecutorAdapter(
        model_registry=models,
        dataset_registry=datasets,
        dataset_manifest_digest=dataset_manifest["manifest_digest"],
        workspace_root=workspace_root,
        pathmnist_asset_binding=binding,
        pathmnist_test_indices=tuple(int(value) for value in smoke_plan["test_indices"]),
    )


async def _receive_local_callbacks(adapter: LocalBuiltInExecutorAdapter) -> int:
    processed = 0
    for external_execution_id in adapter.pending_execution_ids():
        started, completed = await adapter.execute_self_test(external_execution_id)
        for envelope in (started, completed):
            async with session_factory() as session:
                async with session.begin():
                    await receive_execution_callback(session, envelope=envelope)
        processed += 1
    return processed


async def _heartbeat_local_demo_connectors() -> None:
    """Emit transient heartbeats only for verified online demo connectors."""

    async with session_factory() as session:
        async with session.begin():
            connectors = list(
                (
                    await session.scalars(
                        select(Connector).where(
                            Connector.is_demo.is_(True),
                            Connector.verification_status == "verified",
                            Connector.runtime_status == "online",
                        )
                    )
                ).all()
            )
            heartbeat_at = datetime.now(timezone.utc)
            for connector in connectors:
                connector.last_heartbeat_at = heartbeat_at


async def _run() -> None:
    local_pathmnist = (
        os.getenv("MEDTRUST_EXECUTION_COORDINATOR_PATHMNIST", "false").lower()
        == "true"
    )
    if local_pathmnist:
        executor = _build_pathmnist_executor()
        logger.warning(
            "using the allowlisted in-process PathMNIST demo executor; hard isolation is false"
        )
    elif os.getenv("MEDTRUST_EXECUTION_COORDINATOR_FAKE", "false").lower() == "true":
        executor = FakeExecutorAdapter()
    else:
        raise RuntimeError(
            "No registered ExecutorAdapter configured; set the explicit development-only "
            "MEDTRUST_EXECUTION_COORDINATOR_FAKE=true or "
            "MEDTRUST_EXECUTION_COORDINATOR_PATHMNIST=true switch"
        )
    service = ExecutionCoordinatorService(
        session_maker=session_factory, executor=executor
    )
    worker_id = f"coordinator:{socket.gethostname()}:{os.getpid()}"[:96]
    try:
        while True:
            if local_pathmnist:
                await _heartbeat_local_demo_connectors()
            processed = await service.claim_and_process_once(worker_id=worker_id)
            executed = (
                await _receive_local_callbacks(executor)
                if isinstance(executor, LocalBuiltInExecutorAdapter)
                else 0
            )
            if processed == 0 and executed == 0:
                await asyncio.sleep(1)
    finally:
        await close_database()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
