from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.demo.phase4 import ensure_phase4_demo_initial
from app.modules.identity.local_auth import (
    ROLE_BY_SUBJECT,
    USERNAME_BY_ROLE,
    ensure_local_demo_credentials,
)
from app.modules.identity.models import User


async def _run(workspace: Path) -> dict[str, object]:
    settings = get_settings()
    passwords = settings.demo_passwords
    if not all(passwords.values()):
        raise RuntimeError("all invitation account password files must be configured")
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await ensure_phase4_demo_initial(session, workspace=workspace)
                await ensure_local_demo_credentials(
                    session,
                    passwords=passwords,
                    min_password_length=settings.password_min_length,
                )
                operator_subject = next(
                    subject
                    for subject, role in ROLE_BY_SUBJECT.items()
                    if role == "space_operator"
                )
                operator = await session.scalar(
                    select(User).where(
                        User.identity_issuer == "medtrust-demo",
                        User.identity_subject == operator_subject,
                    )
                )
                if operator is None:
                    raise RuntimeError("space operator was not initialized")
        return {
            "created": True,
            "username": USERNAME_BY_ROLE["space_operator"],
            "user_id": str(operator.id),
            "synthetic_only": True,
            "hard_isolation": False,
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize the invitation-only Synthetic Public Alpha graph"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("/var/lib/medtrust/data/bootstrap"),
    )
    args = parser.parse_args()
    result = asyncio.run(_run(args.workspace.resolve()))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
