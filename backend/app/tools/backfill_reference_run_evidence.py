from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.demo.phase4 import get_phase4_context
from app.modules.dataset_model_evidence.reference_backfill import (
    backfill_reference_run_evidence,
)


async def _run(database_url: str, run_id: UUID) -> dict[str, object]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                context = await get_phase4_context(session)
                relation, executed, verified, created = (
                    await backfill_reference_run_evidence(
                        session,
                        actor=context.actors["space_operator"],
                        run_id=run_id,
                    )
                )
                return {
                    "relation_id": str(relation.id),
                    "relation_created": created,
                    "status": relation.current_status,
                    "strongest_evidence_level": relation.strongest_evidence_level,
                    "public_visible": relation.public_visible,
                    "executed_evidence_id": str(executed.id),
                    "verified_evidence_id": str(verified.id),
                    "backfill": True,
                    "new_execution": False,
                }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill evidence from one immutable historical reference run"
    )
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    database_url = args.database_url or get_settings().database_url
    print(json.dumps(asyncio.run(_run(database_url, args.run_id)), indent=2))


if __name__ == "__main__":
    main()
