from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.demo.phase4 import DemoActor
from app.modules.identity.models import Organization, User
from app.modules.role_assistant.models import (
    AgentConversation,
    AgentRunStep,
    AgentTurn,
)
from app.modules.role_assistant.query_service import AssistantQueryContext
from app.modules.role_assistant.schemas import RoleAssistantQueryResponse
from app.modules.role_assistant.state import (
    AgentConversationAccessError,
    begin_agent_turn,
    complete_agent_turn,
)
from app.modules.spaces.models import Space


def _engine():
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


async def _seed_context(session) -> AssistantQueryContext:
    user = User(
        identity_issuer="agent-state-test",
        identity_subject="requester-001",
        display_name="Agent State Requester",
        status="active",
        is_demo=True,
    )
    session.add(user)
    await session.flush()
    organization = Organization(
        legal_name="Agent State Research Organization",
        display_name="Agent State Research",
        organization_type="research_institute",
        verification_status="verified",
        status="active",
        is_demo=True,
        created_by=user.id,
    )
    session.add(organization)
    await session.flush()
    space = Space(
        code="AGENT-STATE-TEST",
        name="Agent State Test Space",
        space_type="industry",
        operator_organization_id=organization.id,
        status="active",
        ruleset_version="rules-v1",
        classification_scheme_version="medical-v1",
        default_retention_policy={"retention_days": 30},
        is_demo=True,
        created_by=user.id,
    )
    session.add(space)
    await session.flush()
    actor = DemoActor(
        role="data_requester",
        organization_id=organization.id,
        user_id=user.id,
        organization_name=organization.display_name,
        user_name=user.display_name,
    )
    return AssistantQueryContext(
        role="data_requester",
        space_id=space.id,
        actor=actor,
        session=session,
    )


def test_agent_state_persists_redacted_trace_and_resolves_entity_reference() -> None:
    asyncio.run(_state_round_trip())


async def _state_round_trip() -> None:
    engine = _engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        context = await _seed_context(session)
        first = await begin_agent_turn(
            session=session,
            context=context,
            message="查找 PathMNIST ResNet-18 模型",
            conversation_id=None,
        )
        response = RoleAssistantQueryResponse(
            plan_source="local",
            intent="search_resources",
            answer="找到一个真实模型产品。",
            results=[
                {
                    "key": "model:version-001",
                    "kind": "model",
                    "label": "模型产品",
                    "title": "PathMNIST ResNet-18病理分类模型",
                    "subtitle": "v1.0",
                    "path": "/model-products/version-001",
                }
            ],
            tool_trace=[
                {
                    "tool": "search_model_products",
                    "label": "模型目录",
                    "status": "success",
                    "result_count": 1,
                    "source": "medtrust.published_model_catalog",
                    "duration_ms": 4,
                }
            ],
        )
        await complete_agent_turn(
            session=session,
            state=first,
            response=response,
            provider="deepseek",
            model_name="deepseek-v4-flash",
        )
        await session.commit()
        conversation_id = first.conversation.id

        second = await begin_agent_turn(
            session=session,
            context=context,
            message="这个模型是否兼容刚才的数据？",
            conversation_id=conversation_id,
        )
        assert second.context_applied is True
        assert "PathMNIST ResNet-18病理分类模型" in second.resolved_message
        await session.rollback()

        conversation = await session.get(AgentConversation, conversation_id)
        turns = list(
            await session.scalars(
                select(AgentTurn).where(
                    AgentTurn.conversation_id == conversation_id
                )
            )
        )
        steps = list(await session.scalars(select(AgentRunStep)))
        assert conversation is not None
        assert conversation.entity_context["model"]["key"] == "model:version-001"
        assert len(turns) == 1
        assert turns[0].input_length == len("查找 PathMNIST ResNet-18 模型")
        assert turns[0].status == "completed"
        assert len(steps) == 1
        assert steps[0].resource_refs == ["model:version-001"]
        assert steps[0].risk_class == "read"

        other_context = SimpleNamespace(
            role="data_requester",
            space_id=context.space_id,
            actor=SimpleNamespace(
                organization_id=context.actor.organization_id,
                user_id=uuid4(),
            ),
            session=session,
        )
        with pytest.raises(AgentConversationAccessError):
            await begin_agent_turn(
                session=session,
                context=other_context,
                message="继续",
                conversation_id=conversation_id,
            )

    column_names = {
        column.name
        for table in (AgentConversation.__table__, AgentTurn.__table__, AgentRunStep.__table__)
        for column in table.columns
    }
    assert "prompt" not in " ".join(column_names)
    assert "raw_response" not in column_names
    assert "reasoning" not in column_names
    await engine.dispose()


def test_demand_recommendations_become_safe_entity_context() -> None:
    from app.modules.role_assistant.state import _updated_entity_context

    response = RoleAssistantQueryResponse(
        plan_source="local",
        intent="analyze_research_demand",
        answer="已推荐。",
        demand_result={
            "data_recommendations": [
                {"version_id": "data-v1", "name": "结直肠病理数据"}
            ],
            "model_recommendations": [
                {"version_id": "model-v1", "name": "ResNet-18 模型"}
            ],
        },
    )
    context = _updated_entity_context({}, response)
    assert context["data"]["key"] == "data:data-v1"
    assert context["model"]["key"] == "model:model-v1"
    assert context["last"]["title"] == "ResNet-18 模型"
