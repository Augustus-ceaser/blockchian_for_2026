import asyncio
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def test_connector_write_on_migrated_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    asyncio.run(_write_connector_graph(TEST_DATABASE_URL))


async def _write_connector_graph(database_url: str) -> None:
    engine = create_async_engine(database_url)
    assert engine.dialect.name == "postgresql"

    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            suffix = uuid4().hex
            user = User(
                identity_issuer="integration-test",
                identity_subject=f"connector-user-{suffix}",
                display_name="Connector PostgreSQL 集成测试用户",
                status="active",
                is_demo=True,
            )
            organization = Organization(
                legal_name=f"Connector PostgreSQL 集成测试机构 {suffix}",
                display_name="Connector PostgreSQL 集成测试机构",
                organization_type="hospital",
                verification_status="verified",
                status="active",
                is_demo=True,
            )
            session.add_all([user, organization])
            await session.flush()
            organization.created_by = user.id

            space = Space(
                code=f"CONNECTOR-INTEGRATION-{suffix}",
                name="Connector PostgreSQL 集成测试空间",
                space_type="industry",
                operator_organization_id=organization.id,
                status="active",
                ruleset_version="rules-v1",
                classification_scheme_version="classification-v1",
                default_retention_policy={"retention_days": 90},
                is_demo=True,
                created_by=user.id,
            )
            connector = Connector(
                space=space,
                owner_organization=organization,
                external_connector_id=f"physical-node-{suffix}",
                name="Connector PostgreSQL 集成测试节点",
                verification_status="verified",
                runtime_status="online",
                endpoint_metadata={"protocol": "https"},
                is_demo=True,
                created_by=user.id,
            )
            connector.capabilities.append(
                ConnectorCapability(
                    capability_code="product_publish",
                    capability_version="1.0",
                    status="verified",
                    parameters={"mode": "integration-test"},
                    verified_at=datetime.now(timezone.utc),
                )
            )
            session.add(connector)
            await session.flush()

            stored = await session.scalar(
                select(Connector)
                .where(Connector.id == connector.id)
                .options(selectinload(Connector.capabilities))
            )
            assert stored is not None
            assert stored.space_id == space.id
            assert [item.capability_code for item in stored.capabilities] == [
                "product_publish"
            ]
        finally:
            await session.close()
            await transaction.rollback()

    await engine.dispose()
