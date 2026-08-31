from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.modules.identity.local_auth import ensure_local_demo_credentials


async def _run(database_url: str | None) -> None:
    settings = get_settings()
    passwords = settings.demo_passwords
    if not all(passwords.values()):
        raise RuntimeError("All local demo passwords must be configured")

    engine = create_async_engine(database_url or settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await ensure_local_demo_credentials(
                    session,
                    passwords=passwords,
                    min_password_length=settings.password_min_length,
                )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rotate only the existing local demo account credentials"
    )
    parser.add_argument("--database-url")
    args = parser.parse_args()
    asyncio.run(_run(args.database_url))
    print("Local demo credential hashes were updated.")


if __name__ == "__main__":
    main()
