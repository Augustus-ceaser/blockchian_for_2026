import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def test_space_write_on_migrated_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    asyncio.run(_write_space_graph(TEST_DATABASE_URL))


async def _write_space_graph(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"

    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            suffix = uuid4().hex
            user = User(
                identity_issuer="integration-test",
                identity_subject=f"space-user-{suffix}",
                display_name="Space PostgreSQL 集成测试用户",
                status="active",
                is_demo=True,
            )
            operator = Organization(
                legal_name=f"Space PostgreSQL 集成测试运营机构 {suffix}",
                display_name="Space PostgreSQL 集成测试运营机构",
                organization_type="operator",
                verification_status="verified",
                status="active",
                is_demo=True,
            )
            session.add_all([user, operator])
            await session.flush()
            operator.created_by = user.id

            space = Space(
                code=f"SPACE-INTEGRATION-{suffix}",
                name="Space PostgreSQL 集成测试空间",
                space_type="industry",
                operator_organization_id=operator.id,
                status="active",
                ruleset_version="rules-v1",
                classification_scheme_version="classification-v1",
                default_retention_policy={"retention_days": 90},
                is_demo=True,
                created_by=user.id,
            )
            participant = SpaceParticipant(
                space=space,
                organization=operator,
                admission_status="admitted",
                ruleset_accepted_version="rules-v1",
                created_by=user.id,
            )
            participant.roles.append(
                SpaceParticipantRole(role_code="operator", granted_by=user.id)
            )
            session.add(participant)
            await session.flush()

            stored = await session.scalar(
                select(SpaceParticipant)
                .where(SpaceParticipant.id == participant.id)
                .options(selectinload(SpaceParticipant.roles))
            )
            assert stored is not None
            assert stored.space_id == space.id
            assert [role.role_code for role in stored.roles] == ["operator"]
        finally:
            await session.close()
            await transaction.rollback()

    await engine.dispose()
