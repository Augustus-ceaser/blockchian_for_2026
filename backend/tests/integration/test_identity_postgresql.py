import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.identity.models import Organization, OrganizationMember, User

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def test_identity_write_on_migrated_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    asyncio.run(_write_identity_graph(TEST_DATABASE_URL))


async def _write_identity_graph(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"

    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            suffix = uuid4().hex
            user = User(
                identity_issuer="integration-test",
                identity_subject=f"user-{suffix}",
                display_name="Identity PostgreSQL 集成测试用户",
                status="active",
                is_demo=True,
            )
            organization = Organization(
                legal_name=f"Identity PostgreSQL 集成测试机构 {suffix}",
                display_name="Identity PostgreSQL 集成测试机构",
                organization_type="hospital",
                verification_status="verified",
                status="active",
                is_demo=True,
            )
            session.add_all([user, organization])
            await session.flush()

            organization.created_by = user.id
            membership = OrganizationMember(
                organization_id=organization.id,
                user_id=user.id,
                status="active",
                created_by=user.id,
            )
            session.add(membership)
            await session.flush()

            stored = await session.scalar(
                select(OrganizationMember).where(
                    OrganizationMember.id == membership.id
                )
            )
            assert stored is not None
            assert stored.organization_id == organization.id
            assert stored.user_id == user.id
        finally:
            await session.close()
            await transaction.rollback()

    await engine.dispose()
