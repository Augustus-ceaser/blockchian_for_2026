import asyncio
from datetime import datetime, timezone

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.identity.models import (
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    User,
)


def test_create_organization_user_membership_and_role() -> None:
    asyncio.run(_create_identity_graph())


def test_duplicate_organization_membership_is_rejected() -> None:
    asyncio.run(_reject_duplicate_membership())


def make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"medtrust": None}},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _create_identity_graph() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user = User(
            identity_issuer="demo-local",
            identity_subject="user-001",
            display_name="演示用户",
            email="demo.user@example.invalid",
            status="active",
            is_demo=True,
        )
        organization = Organization(
            legal_name="华南肿瘤协作机构（演示）",
            display_name="肿瘤协作机构（演示）",
            organization_type="hospital",
            verification_status="verified",
            status="active",
            is_demo=True,
        )
        session.add_all([user, organization])
        await session.flush()

        organization.created_by = user.id
        membership = OrganizationMember(
            organization=organization,
            user=user,
            status="active",
            valid_from=datetime.now(timezone.utc),
            created_by=user.id,
        )
        membership.roles.append(
            OrganizationMemberRole(
                role_code="provider_data_admin",
                granted_by=user.id,
            )
        )
        session.add(membership)
        await session.commit()

    async with session_factory() as session:
        stored_membership = await session.scalar(
            select(OrganizationMember).options(
                selectinload(OrganizationMember.organization),
                selectinload(OrganizationMember.user),
                selectinload(OrganizationMember.roles),
            )
        )

        assert stored_membership is not None
        assert stored_membership.organization.organization_type == "hospital"
        assert stored_membership.user.identity_subject == "user-001"
        assert [role.role_code for role in stored_membership.roles] == [
            "provider_data_admin"
        ]

    await engine.dispose()


async def _reject_duplicate_membership() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user = User(
            identity_issuer="demo-local",
            identity_subject="user-duplicate",
            display_name="重复关系测试用户",
            status="active",
            is_demo=True,
        )
        organization = Organization(
            legal_name="重复关系测试机构（演示）",
            display_name="重复关系测试机构（演示）",
            organization_type="research_institute",
            verification_status="verified",
            status="active",
            is_demo=True,
        )
        session.add_all([user, organization])
        await session.flush()

        first = OrganizationMember(
            organization_id=organization.id,
            user_id=user.id,
            status="active",
            created_by=user.id,
        )
        second = OrganizationMember(
            organization_id=organization.id,
            user_id=user.id,
            status="invited",
            created_by=user.id,
        )
        session.add_all([first, second])

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            raise AssertionError("duplicate organization membership was accepted")

    await engine.dispose()
