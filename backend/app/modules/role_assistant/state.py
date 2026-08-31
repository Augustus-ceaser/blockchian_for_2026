from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.role_assistant.models import (
    AgentConversation,
    AgentRunStep,
    AgentTurn,
)
from app.modules.role_assistant.query_service import AssistantQueryContext
from app.modules.role_assistant.schemas import (
    AssistantResource,
    AssistantToolTrace,
    RoleAssistantQueryResponse,
)


class AgentConversationAccessError(ValueError):
    pass


@dataclass(frozen=True)
class AgentTurnState:
    conversation: AgentConversation
    turn: AgentTurn
    resolved_message: str
    context_applied: bool
    context_refs: list[dict[str, str]]
    started_clock: float


_TYPED_REFERENCES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("data", re.compile(r"(?:这个|该|上述)(?:数据|数据集|数据产品)|它的数据")),
    ("model", re.compile(r"(?:这个|该|上述)(?:模型|算法)|它的模型")),
    ("contract", re.compile(r"(?:这个|该|上述)(?:合约|合同)")),
    ("application", re.compile(r"(?:这个|该|上述)(?:申请|需求)")),
    ("execution", re.compile(r"(?:这个|该|上述)(?:执行|任务|作业)")),
    ("result", re.compile(r"(?:这个|该|上述)(?:结果|输出)")),
)
_GENERIC_REFERENCE = re.compile(r"(?:这个|该项|上述|它)(?:怎么|是否|的|现在|还|可以|能)")


def supports_persistent_agent_state(
    *, context: AssistantQueryContext, session: object
) -> bool:
    return (
        isinstance(session, AsyncSession)
        and getattr(context, "space_id", None) is not None
        and getattr(context, "actor", None) is not None
        and getattr(context.actor, "organization_id", None) is not None
        and getattr(context.actor, "user_id", None) is not None
    )


def _safe_entity_ref(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    key = str(value.get("key") or "")[:160]
    title = str(value.get("title") or "")[:240]
    path = str(value.get("path") or "")[:256]
    kind = str(value.get("kind") or "")[:32]
    if not key or not title or not kind:
        return None
    return {"key": key, "title": title, "path": path, "kind": kind}


def _resolve_message(
    message: str, entity_context: dict[str, Any]
) -> tuple[str, list[dict[str, str]]]:
    refs: list[dict[str, str]] = []
    for kind, pattern in _TYPED_REFERENCES:
        if not pattern.search(message):
            continue
        ref = _safe_entity_ref(entity_context.get(kind))
        if ref is not None:
            refs.append(ref)
    if not refs and _GENERIC_REFERENCE.search(message):
        ref = _safe_entity_ref(entity_context.get("last"))
        if ref is not None:
            refs.append(ref)
    if not refs:
        return message, []
    context_text = "；".join(
        f"{ref['kind']}={ref['title']}（{ref['key']}）" for ref in refs
    )
    return f"{message}\n[会话资源引用：{context_text}]", refs


async def begin_agent_turn(
    *,
    session: AsyncSession,
    context: AssistantQueryContext,
    message: str,
    conversation_id: UUID | None,
) -> AgentTurnState:
    now = datetime.now(timezone.utc)
    if conversation_id is None:
        conversation = AgentConversation(
            space_id=context.space_id,
            actor_organization_id=context.actor.organization_id,
            actor_user_id=context.actor.user_id,
            role_code=context.role,
            status="active",
            entity_context={},
            turn_count=0,
            last_seen_at=now,
        )
        session.add(conversation)
        await session.flush()
    else:
        conversation = await session.scalar(
            select(AgentConversation)
            .where(
                AgentConversation.id == conversation_id,
                AgentConversation.space_id == context.space_id,
                AgentConversation.actor_organization_id
                == context.actor.organization_id,
                AgentConversation.actor_user_id == context.actor.user_id,
                AgentConversation.role_code == context.role,
                AgentConversation.status == "active",
            )
            .with_for_update()
        )
        if conversation is None:
            raise AgentConversationAccessError("会话不存在或当前账号无权继续")

    resolved_message, context_refs = _resolve_message(
        message, dict(conversation.entity_context or {})
    )
    conversation.turn_count += 1
    conversation.last_seen_at = now
    conversation.updated_at = now
    turn = AgentTurn(
        conversation_id=conversation.id,
        sequence_no=conversation.turn_count,
        input_length=len(message),
        context_applied=bool(context_refs),
        context_refs=context_refs,
        status="running",
        started_at=now,
    )
    session.add(turn)
    await session.flush()
    return AgentTurnState(
        conversation=conversation,
        turn=turn,
        resolved_message=resolved_message,
        context_applied=bool(context_refs),
        context_refs=context_refs,
        started_clock=perf_counter(),
    )


def _trace_resource_refs(
    trace: AssistantToolTrace, response: RoleAssistantQueryResponse
) -> list[str]:
    kind_by_tool = {
        "search_data_products": "data",
        "search_data_services": "service",
        "search_model_products": "model",
        "get_request_status": "application",
        "get_contract_status": "contract",
        "get_execution_status": "execution",
        "get_result_status": "result",
        "get_lifecycle_status": "lifecycle",
    }
    expected_kind = kind_by_tool.get(trace.tool)
    refs = [
        item.key
        for item in response.results
        if expected_kind is None or item.kind == expected_kind
    ]
    if trace.tool == "check_data_model_compatibility":
        refs.extend(
            item.relation_id
            for item in response.compatibility_evidence
            if item.relation_id
        )
    if trace.tool == "get_execution_lineage":
        refs.extend(item.application_id for item in response.lineage)
    return list(dict.fromkeys(refs))[:20]


def _entity_ref(item: AssistantResource) -> dict[str, str]:
    return {
        "key": item.key[:160],
        "title": item.title[:240],
        "path": item.path[:256],
        "kind": item.kind,
    }


def _updated_entity_context(
    previous: dict[str, Any], response: RoleAssistantQueryResponse
) -> dict[str, Any]:
    updated = dict(previous)
    for item in response.results:
        ref = _entity_ref(item)
        updated[item.kind] = ref
        updated["last"] = ref
    if response.demand_result:
        for field, kind in (
            ("data_recommendations", "data"),
            ("model_recommendations", "model"),
        ):
            candidates = response.demand_result.get(field)
            if not isinstance(candidates, list) or not candidates:
                continue
            candidate = candidates[0]
            if not isinstance(candidate, dict):
                continue
            version_id = str(candidate.get("version_id") or "")
            title = str(candidate.get("name") or "")
            if not version_id or not title:
                continue
            ref = {
                "key": f"{kind}:{version_id}"[:160],
                "title": title[:240],
                "path": f"/{kind}-products/{version_id}"[:256],
                "kind": kind,
            }
            updated[kind] = ref
            updated["last"] = ref
    if response.lineage:
        lineage = response.lineage[0]
        ref = {
            "key": f"application:{lineage.application_id}"[:160],
            "title": lineage.application_number[:240],
            "path": lineage.path[:256],
            "kind": "application",
        }
        updated["application"] = ref
        updated["last"] = ref
    return updated


async def complete_agent_turn(
    *,
    session: AsyncSession,
    state: AgentTurnState,
    response: RoleAssistantQueryResponse,
    provider: str | None,
    model_name: str | None,
) -> None:
    now = datetime.now(timezone.utc)
    turn = state.turn
    turn.intent = response.intent
    turn.plan_source = response.plan_source
    turn.provider = (provider or "")[:24] or None
    turn.model_name = (model_name or "")[:128] or None
    turn.status = "completed"
    turn.route_hint = response.route_hint
    turn.result_count = (
        len(response.results)
        + len(response.compatibility_evidence)
        + len(response.lineage)
    )
    turn.answer_length = len(response.answer)
    turn.completed_at = now
    turn.duration_ms = max(0, int((perf_counter() - state.started_clock) * 1000))

    for index, trace in enumerate(response.tool_trace, start=1):
        session.add(
            AgentRunStep(
                turn_id=turn.id,
                sequence_no=index,
                step_type="tool",
                tool_name=trace.tool,
                tool_label=trace.label,
                risk_class=trace.risk_class,
                authorization_result=trace.authorization_result,
                status=trace.status,
                result_count=trace.result_count,
                resource_refs=_trace_resource_refs(trace, response),
                source=trace.source,
                duration_ms=trace.duration_ms,
                error_code=trace.error_code,
            )
        )

    state.conversation.entity_context = _updated_entity_context(
        dict(state.conversation.entity_context or {}), response
    )
    state.conversation.last_seen_at = now
    state.conversation.updated_at = now
    await session.flush()


async def fail_agent_turn(
    *, session: AsyncSession, state: AgentTurnState, error_code: str
) -> None:
    now = datetime.now(timezone.utc)
    state.turn.status = "failed"
    state.turn.error_code = error_code[:64]
    state.turn.completed_at = now
    state.turn.duration_ms = max(
        0, int((perf_counter() - state.started_clock) * 1000)
    )
    state.conversation.last_seen_at = now
    state.conversation.updated_at = now
    await session.flush()
