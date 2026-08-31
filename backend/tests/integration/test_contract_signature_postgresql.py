from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.contracts import ContractRevision, activate_contract_revision
from tests.test_contract_models import (
    _accept_required_bindings,
    _make_signed_revision,
    _system_audit_command,
)

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_contract_signature_and_activation_postgresql_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_signature_and_activation_guards(TEST_DATABASE_URL))


async def _assert_signature_and_activation_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            revision, _, signatures = await _make_signed_revision(
                session, number="CTR-SIGN-PG-001"
            )
            assert revision.status == "signed"
            assert len(signatures) == 2

            savepoint = await session.begin_nested()
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        text(
                            "UPDATE medtrust.contract_signatures "
                            "SET signature_value_ref='tampered' WHERE id=:id"
                        ),
                        {"id": signatures[0].id},
                    )
                assert "append-only" in str(caught.value.orig)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()

            savepoint = await session.begin_nested()
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        update(ContractRevision)
                        .where(ContractRevision.id == revision.id)
                        .values(
                            status="active",
                            activated_at=text("CURRENT_TIMESTAMP"),
                            row_version=ContractRevision.row_version + 1,
                        )
                    )
                assert "binding is not accepted" in str(caught.value.orig)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()

            await _accept_required_bindings(session, revision)
            await activate_contract_revision(
                session,
                revision,
                audit_command=_system_audit_command("contract-signature-pg-activate"),
            )
            assert revision.status == "active"

            savepoint = await session.begin_nested()
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        update(ContractRevision)
                        .where(ContractRevision.id == revision.id)
                        .values(summary="tampered active revision")
                    )
                assert "immutable" in str(caught.value.orig)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()
