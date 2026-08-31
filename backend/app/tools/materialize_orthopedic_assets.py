from __future__ import annotations

import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.demo.phase4 import PHASE4_SPACE_CODE
from app.modules.external_catalog.orthopedic_materialization import (
    materialize_local_orthopedic_assets,
)
from app.modules.spaces.models import Space


async def _run() -> dict[str, object]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                space_id = await session.scalar(
                    select(Space.id).where(Space.code == PHASE4_SPACE_CODE)
                )
                if space_id is None:
                    raise RuntimeError("Phase 4 demo space must be initialized first")
                result = await materialize_local_orthopedic_assets(
                    session,
                    space_id=space_id,
                )
        return {
            "dataset_record_id": str(result.dataset_record_id),
            "dataset_version_id": str(result.dataset_version_id),
            "dataset_outcome": result.dataset_outcome,
            "model_record_id": str(result.model_record_id),
            "model_version_id": str(result.model_version_id),
            "model_outcome": result.model_outcome,
            "manifest_digest": result.manifest_digest,
            "dataset_archive_sha256": result.dataset_archive_sha256,
            "model_weights_sha256": result.model_weights_sha256,
            "application_eligible": False,
            "executor_registered": False,
            "can_execute": False,
        }
    finally:
        await engine.dispose()


def main() -> None:
    print(json.dumps(asyncio.run(_run()), sort_keys=True))


if __name__ == "__main__":
    main()
