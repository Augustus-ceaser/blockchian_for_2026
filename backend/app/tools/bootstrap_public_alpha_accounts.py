from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.modules.identity.public_alpha import (
    ensure_public_alpha_accounts,
    public_alpha_account_status,
)


async def _run(*, status_only: bool) -> dict[str, object]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            if status_only:
                return await public_alpha_account_status(session)
            async with session.begin():
                result = await ensure_public_alpha_accounts(
                    session,
                    passwords=settings.demo_passwords,
                    min_password_length=settings.password_min_length,
                )
            return {
                "created": result.created,
                "username": "operator.demo",
                "user_id": str(result.operator_id),
                "space_id": str(result.space_id),
                "invitation_only": True,
                "synthetic_or_public": True,
                "non_clinical": True,
                "hard_isolation": False,
            }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize Public Alpha invitation accounts")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(status_only=args.status)), sort_keys=True))


if __name__ == "__main__":
    main()
