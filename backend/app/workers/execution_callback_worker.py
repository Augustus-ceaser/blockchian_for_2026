from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path

from app.core.config import get_settings
from app.db.session import close_database, session_factory
from app.execution.callback_processor import ExecutionCallbackWorker
from app.execution.quarantine import MinioQuarantineArtifactWriter
from app.modules.callback_inbox import claim_callback_batch, reclaim_expired_callbacks

__all__ = ["ExecutionCallbackWorker"]

logger = logging.getLogger("medtrust.execution_callback_worker")


async def _run() -> None:
    settings = get_settings()
    workspace_root = Path(
        os.getenv(
            "MEDTRUST_LOCAL_EXECUTOR_WORKSPACE",
            str(Path(__file__).resolve().parents[3] / ".runtime/pathmnist-demo-workspaces"),
        )
    )
    worker = ExecutionCallbackWorker(
        session_factory,
        artifact_writer=MinioQuarantineArtifactWriter(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            bucket_name=settings.minio_quarantine_bucket,
            workspace_root=workspace_root,
        ),
    )
    worker_id = f"callback:{socket.gethostname()}:{os.getpid()}"[:96]
    try:
        while True:
            async with session_factory() as session:
                async with session.begin():
                    reclaimed = await reclaim_expired_callbacks(
                        session, worker_id=worker_id, batch_size=50, lease_seconds=60
                    )
                    fresh = await claim_callback_batch(
                        session,
                        worker_id=worker_id,
                        batch_size=max(0, 50 - len(reclaimed)),
                        lease_seconds=60,
                    )
                    entry_ids = [row.id for row in [*reclaimed, *fresh]]
            for entry_id in entry_ids:
                try:
                    await worker.process_one(entry_id=entry_id, worker_id=worker_id)
                except Exception as exc:
                    logger.warning("callback processing deferred or rejected: %s", type(exc).__name__)
            if not entry_ids:
                await asyncio.sleep(1)
    finally:
        await close_database()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
