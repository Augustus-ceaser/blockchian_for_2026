import asyncio
import os
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.identity.models import LocalDemoCredential, User
from app.modules.identity.public_alpha import (
    ensure_public_alpha_accounts,
    public_alpha_account_status,
)

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]

PASSWORDS = {
    "operator.demo": "operator-integration-password",
    "hospital.demo": "hospital-integration-password",
    "model.demo": "model-integration-password",
    "requester.demo": "requester-integration-password",
    "catalog.curator.demo": "catalog-integration-password",
}


def test_public_alpha_bootstrap_is_atomic_and_idempotent() -> None:
    assert TEST_DATABASE_URL is not None
    asyncio.run(_assert_atomic_and_idempotent(TEST_DATABASE_URL))


async def _assert_atomic_and_idempotent(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    hash_calls = 0

    def fail_on_third_hash(*args: object, **kwargs: object) -> str:
        nonlocal hash_calls
        hash_calls += 1
        if hash_calls == 3:
            raise RuntimeError("forced bootstrap failure")
        from app.modules.identity.local_auth import _password_hash

        return _password_hash(*args, **kwargs)

    with patch(
        "app.modules.identity.public_alpha._password_hash",
        side_effect=fail_on_third_hash,
    ):
        with pytest.raises(RuntimeError, match="forced bootstrap failure"):
            async with factory.begin() as session:
                await ensure_public_alpha_accounts(
                    session,
                    passwords=PASSWORDS,
                    min_password_length=12,
                )

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0

    async with factory.begin() as session:
        first = await ensure_public_alpha_accounts(
            session,
            passwords=PASSWORDS,
            min_password_length=12,
        )
        assert first.created is True

    async with factory() as session:
        original_hash = await session.scalar(
            select(LocalDemoCredential.password_hash).where(
                LocalDemoCredential.username == "operator.demo"
            )
        )

    changed_passwords = {
        username: f"different-{password}"
        for username, password in PASSWORDS.items()
    }
    async with factory.begin() as session:
        second = await ensure_public_alpha_accounts(
            session,
            passwords=changed_passwords,
            min_password_length=12,
        )
        assert second.created is False

    async with factory() as session:
        status = await public_alpha_account_status(session)
        repeated_hash = await session.scalar(
            select(LocalDemoCredential.password_hash).where(
                LocalDemoCredential.username == "operator.demo"
            )
        )
        assert status["foundation_complete"] is True
        assert status["counts"] == status["expected_counts"]
        assert repeated_hash == original_hash

    await engine.dispose()
