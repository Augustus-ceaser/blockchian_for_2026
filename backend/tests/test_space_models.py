import asyncio
from datetime import datetime, timezone

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole


def test_organization_can_join_multiple_spaces_with_contextual_roles() -> None:
    asyncio.run(_create_multi_space_participation())


def test_duplicate_participant_in_same_space_is_rejected() -> None:
    asyncio.run(_reject_duplicate_participant())


def test_invalid_space_participant_role_is_rejected() -> None:
    asyncio.run(_reject_invalid_role())


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


async def seed_identity(session):
    user = User(
        identity_issuer="demo-local",
        identity_subject="space-admin-001",
        display_name="空间治理演示用户",
        status="active",
        is_demo=True,
    )
    operator = Organization(
        legal_name="数字病理空间运营机构（演示）",
        display_name="空间运营机构（演示）",
        organization_type="operator",
        verification_status="verified",
        status="active",
        is_demo=True,
    )
    provider = Organization(
        legal_name="数字病理数据提供机构（演示）",
        display_name="数据提供机构（演示）",
        organization_type="hospital",
        verification_status="verified",
        status="active",
        is_demo=True,
    )
    session.add_all([user, operator, provider])
    await session.flush()
    operator.created_by = user.id
    provider.created_by = user.id
    return user, operator, provider


def make_space(*, code: str, name: str, operator: Organization, created_by) -> Space:
    return Space(
        code=code,
        name=name,
        space_type="industry",
        operator_organization=operator,
        status="active",
        ruleset_version="rules-v1",
        classification_scheme_version="medical-classification-v1",
        default_retention_policy={"retention_days": 90},
        is_demo=True,
        created_by=created_by,
    )


async def _create_multi_space_participation() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, operator, provider = await seed_identity(session)
        pathology_space = make_space(
            code="PATHOLOGY-AI-DEMO",
            name="数字病理 AI 协作空间（演示）",
            operator=operator,
            created_by=user.id,
        )
        research_space = make_space(
            code="PATHOLOGY-RWE-DEMO",
            name="数字病理真实世界研究空间（演示）",
            operator=operator,
            created_by=user.id,
        )
        session.add_all([pathology_space, research_space])
        await session.flush()

        first_participation = SpaceParticipant(
            space=pathology_space,
            organization=provider,
            admission_status="admitted",
            ruleset_accepted_version="rules-v1",
            admitted_at=datetime.now(timezone.utc),
            created_by=user.id,
        )
        first_participation.roles.extend(
            [
                SpaceParticipantRole(role_code="provider", granted_by=user.id),
                SpaceParticipantRole(role_code="consumer", granted_by=user.id),
            ]
        )
        second_participation = SpaceParticipant(
            space=research_space,
            organization=provider,
            admission_status="admitted",
            ruleset_accepted_version="rules-v1",
            admitted_at=datetime.now(timezone.utc),
            created_by=user.id,
        )
        second_participation.roles.append(
            SpaceParticipantRole(role_code="consumer", granted_by=user.id)
        )
        session.add_all([first_participation, second_participation])
        await session.commit()

    async with session_factory() as session:
        participations = list(
            (
                await session.scalars(
                    select(SpaceParticipant)
                    .where(SpaceParticipant.organization_id == provider.id)
                    .options(
                        selectinload(SpaceParticipant.space),
                        selectinload(SpaceParticipant.roles),
                    )
                    .order_by(SpaceParticipant.space_id)
                )
            ).all()
        )

        assert len(participations) == 2
        roles_by_space = {
            participation.space.code: {role.role_code for role in participation.roles}
            for participation in participations
        }
        assert roles_by_space["PATHOLOGY-AI-DEMO"] == {"provider", "consumer"}
        assert roles_by_space["PATHOLOGY-RWE-DEMO"] == {"consumer"}

    await engine.dispose()


async def _reject_duplicate_participant() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, operator, provider = await seed_identity(session)
        space = make_space(
            code="DUPLICATE-PARTICIPANT-DEMO",
            name="重复参与关系测试空间（演示）",
            operator=operator,
            created_by=user.id,
        )
        session.add(space)
        await session.flush()
        session.add_all(
            [
                SpaceParticipant(
                    space_id=space.id,
                    organization_id=provider.id,
                    admission_status="applied",
                    created_by=user.id,
                ),
                SpaceParticipant(
                    space_id=space.id,
                    organization_id=provider.id,
                    admission_status="reviewing",
                    created_by=user.id,
                ),
            ]
        )

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            raise AssertionError("duplicate space participation was accepted")

    await engine.dispose()


async def _reject_invalid_role() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, operator, provider = await seed_identity(session)
        space = make_space(
            code="INVALID-ROLE-DEMO",
            name="非法空间角色测试空间（演示）",
            operator=operator,
            created_by=user.id,
        )
        participant = SpaceParticipant(
            space=space,
            organization=provider,
            admission_status="admitted",
            ruleset_accepted_version="rules-v1",
            admitted_at=datetime.now(timezone.utc),
            created_by=user.id,
        )
        participant.roles.append(
            SpaceParticipantRole(role_code="global_admin", granted_by=user.id)
        )
        session.add(participant)

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            raise AssertionError("invalid space participant role was accepted")

    await engine.dispose()
