from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.demo import ensure_pathmnist_demo_baseline


async def _run(database_url: str, run_limit: int) -> dict[str, object]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                baseline = await ensure_pathmnist_demo_baseline(
                    session, run_limit=run_limit
                )
        return {
            "ready": True,
            "created": baseline.created,
            "space_id": str(baseline.space_id),
            "contract_id": str(baseline.contract_id),
            "revision_id": str(baseline.revision_id),
            "contract_object_id": str(baseline.contract_object_id),
            "run_limit": baseline.run_limit,
            "demo": True,
            "hard_isolation": False,
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--run-limit", type=int, default=20)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(_run(args.database_url, args.run_limit)),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

