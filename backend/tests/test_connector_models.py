import asyncio
from datetime import datetime, timezone

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.identity.models import Organization, User
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole


def test_organization_can_register_multiple_connectors_and_reuse_physical_id_across_spaces() -> None:
    asyncio.run(_create_connector_registrations())


def test_duplicate_external_connector_id_in_same_space_is_rejected() -> None:
    asyncio.run(_reject_duplicate_external_id())


def test_duplicate_connector_name_for_same_owner_in_space_is_rejected() -> None:
    asyncio.run(_reject_duplicate_name())


def test_duplicate_capability_version_is_rejected() -> None:
    asyncio.run(_reject_duplicate_capability())


def test_invalid_connector_status_is_rejected() -> None:
    asyncio.run(_reject_invalid_status())


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


async def seed_connector_context(session):
    user = User(
        identity_issuer="demo-local",
        identity_subject="connector-admin-001",
        display_name="连接器演示管理员",
        status="active",
        is_demo=True,
    )
    operator = Organization(
        legal_name="连接器测试空间运营机构（演示）",
        display_name="连接器测试运营机构（演示）",
        organization_type="operator",
        verification_status="verified",
        status="active",
        is_demo=True,
    )
    owner = Organization(
        legal_name="数字病理节点所属机构（演示）",
        display_name="数字病理节点机构（演示）",
        organization_type="hospital",
        verification_status="verified",
        status="active",
        is_demo=True,
    )
    session.add_all([user, operator, owner])
    await session.flush()
    operator.created_by = user.id
    owner.created_by = user.id

    first_space = Space(
        code="CONNECTOR-PATHOLOGY-DEMO",
        name="数字病理节点测试空间（演示）",
        space_type="industry",
        operator_organization=operator,
        status="active",
        ruleset_version="rules-v1",
        classification_scheme_version="classification-v1",
        default_retention_policy={"retention_days": 90},
        is_demo=True,
        created_by=user.id,
    )
    second_space = Space(
        code="CONNECTOR-RESEARCH-DEMO",
        name="多中心研究节点测试空间（演示）",
        space_type="industry",
        operator_organization=operator,
        status="active",
        ruleset_version="rules-v1",
        classification_scheme_version="classification-v1",
        default_retention_policy={"retention_days": 90},
        is_demo=True,
        created_by=user.id,
    )
    session.add_all([first_space, second_space])
    await session.flush()

    for space in (first_space, second_space):
        participant = SpaceParticipant(
            space=space,
            organization=owner,
            admission_status="admitted",
            ruleset_accepted_version="rules-v1",
            admitted_at=datetime.now(timezone.utc),
            created_by=user.id,
        )
        participant.roles.append(
            SpaceParticipantRole(role_code="provider", granted_by=user.id)
        )
        session.add(participant)
    await session.flush()
    return user, owner, first_space, second_space


def make_connector(
    *,
    space: Space,
    owner: Organization,
    created_by,
    external_id: str,
    name: str,
) -> Connector:
    return Connector(
        space=space,
        owner_organization=owner,
        external_connector_id=external_id,
        name=name,
        verification_status="verified",
        runtime_status="online",
        endpoint_metadata={"protocol": "https", "endpoint_ref": "demo-node"},
        certificate_fingerprint="demo-fingerprint",
        last_heartbeat_at=datetime.now(timezone.utc),
        is_demo=True,
        created_by=created_by,
    )


async def _create_connector_registrations() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, owner, first_space, second_space = await seed_connector_context(session)
        pathology_connector = make_connector(
            space=first_space,
            owner=owner,
            created_by=user.id,
            external_id="physical-node-001",
            name="数字病理数据连接器（演示）",
        )
        clinical_connector = make_connector(
            space=first_space,
            owner=owner,
            created_by=user.id,
            external_id="physical-node-002",
            name="临床变量连接器（演示）",
        )
        cross_space_registration = make_connector(
            space=second_space,
            owner=owner,
            created_by=user.id,
            external_id="physical-node-001",
            name="数字病理研究连接器（演示）",
        )
        pathology_connector.capabilities.extend(
            [
                ConnectorCapability(
                    capability_code="product_publish",
                    capability_version="1.0",
                    status="verified",
                    parameters={"formats": ["DICOM-WSI"]},
                    verified_at=datetime.now(timezone.utc),
                ),
                ConnectorCapability(
                    capability_code="policy_execute",
                    capability_version="1.0",
                    status="declared",
                    parameters={"mode": "demo"},
                ),
            ]
        )
        session.add_all(
            [pathology_connector, clinical_connector, cross_space_registration]
        )
        await session.commit()

    async with session_factory() as session:
        connectors = list(
            (
                await session.scalars(
                    select(Connector)
                    .where(Connector.owner_organization_id == owner.id)
                    .options(selectinload(Connector.capabilities))
                )
            ).all()
        )
        assert len(connectors) == 3
        physical_node_registrations = [
            connector
            for connector in connectors
            if connector.external_connector_id == "physical-node-001"
        ]
        assert {connector.space_id for connector in physical_node_registrations} == {
            first_space.id,
            second_space.id,
        }
        stored_pathology = next(
            connector
            for connector in connectors
            if connector.name == "数字病理数据连接器（演示）"
        )
        assert {capability.capability_code for capability in stored_pathology.capabilities} == {
            "product_publish",
            "policy_execute",
        }

    await engine.dispose()


async def _reject_duplicate_external_id() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, owner, first_space, _ = await seed_connector_context(session)
        session.add_all(
            [
                make_connector(
                    space=first_space,
                    owner=owner,
                    created_by=user.id,
                    external_id="duplicate-node",
                    name="重复节点一（演示）",
                ),
                make_connector(
                    space=first_space,
                    owner=owner,
                    created_by=user.id,
                    external_id="duplicate-node",
                    name="重复节点二（演示）",
                ),
            ]
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            raise AssertionError("duplicate connector external ID was accepted in one space")

    await engine.dispose()


async def _reject_duplicate_name() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, owner, first_space, _ = await seed_connector_context(session)
        session.add_all(
            [
                make_connector(
                    space=first_space,
                    owner=owner,
                    created_by=user.id,
                    external_id="named-node-001",
                    name="同名节点（演示）",
                ),
                make_connector(
                    space=first_space,
                    owner=owner,
                    created_by=user.id,
                    external_id="named-node-002",
                    name="同名节点（演示）",
                ),
            ]
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            raise AssertionError("duplicate connector name was accepted for one owner and space")

    await engine.dispose()


async def _reject_duplicate_capability() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, owner, first_space, _ = await seed_connector_context(session)
        connector = make_connector(
            space=first_space,
            owner=owner,
            created_by=user.id,
            external_id="duplicate-capability-node",
            name="重复能力节点（演示）",
        )
        connector.capabilities.extend(
            [
                ConnectorCapability(
                    capability_code="product_publish",
                    capability_version="1.0",
                    status="declared",
                ),
                ConnectorCapability(
                    capability_code="product_publish",
                    capability_version="1.0",
                    status="verified",
                ),
            ]
        )
        session.add(connector)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            raise AssertionError("duplicate connector capability version was accepted")

    await engine.dispose()


async def _reject_invalid_status() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user, owner, first_space, _ = await seed_connector_context(session)
        connector = make_connector(
            space=first_space,
            owner=owner,
            created_by=user.id,
            external_id="invalid-status-node",
            name="非法状态节点（演示）",
        )
        connector.runtime_status = "trusted"
        session.add(connector)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            raise AssertionError("invalid connector runtime status was accepted")

    await engine.dispose()
