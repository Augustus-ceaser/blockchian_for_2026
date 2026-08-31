from __future__ import annotations

import logging
from typing import cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.demo.phase4 import get_phase4_context
from app.modules.applications.demand_assistant import recommend_research_demand
from app.modules.catalog_search.capabilities import (
    detect_catalog_retrieval_capabilities,
)
from app.modules.marketplace.services import MarketplaceServiceError, require_actor
from app.modules.role_assistant.planner import (
    PUBLIC_ROLES,
    PublicRole,
    build_local_plan,
    plan_role_assistant,
)
from app.modules.role_assistant.pydantic_runtime import run_pydantic_role_assistant
from app.modules.role_assistant.query_service import (
    AssistantQueryContext,
    AssistantQueryResult,
    is_count_question,
)
from app.modules.role_assistant.registry import execute_tool, tools_for_plan
from app.modules.role_assistant.schemas import (
    AssistantCompatibilityEvidence,
    AssistantCountMetric,
    AssistantExecutionLineage,
    AssistantResource,
    AssistantToolTrace,
    RoleAssistantQueryResponse,
)
from app.modules.role_assistant.state import (
    AgentConversationAccessError,
    AgentTurnState,
    begin_agent_turn,
    complete_agent_turn,
    supports_persistent_agent_state,
)


logger = logging.getLogger(__name__)


def _format_count_answer(results: list[AssistantQueryResult]) -> str:
    if len(results) == 1:
        result = results[0]
        if result.source == "medtrust.external_dataset_catalog":
            return (
                f"当前空间共有 {result.total:,} 条已同步的公共候选数据集目录记录；"
                "这些是目录元数据，已发布数据产品需另行统计。"
            )
        if result.source == "medtrust.external_model_catalog":
            return f"当前空间共有 {result.total:,} 条已同步的公共候选模型目录记录。"
    summary = "；".join(
        f"{result.label} {result.total:,} {result.unit}" for result in results
    )
    return f"已实时读取当前账号可见资源：{summary}。" if summary else ""


async def _query_context(
    *,
    role: str,
    session: AsyncSession,
) -> AssistantQueryContext:
    if role not in PUBLIC_ROLES:
        raise HTTPException(status_code=403, detail="当前账号不能使用角色助手")
    context = await get_phase4_context(session)
    actor = context.actors[role]
    try:
        await require_actor(
            session,
            space_id=context.space_id,
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            role_code=role,
        )
    except MarketplaceServiceError as exc:
        raise HTTPException(status_code=403, detail="当前账号无有效空间权限") from exc
    return AssistantQueryContext(
        role=cast(PublicRole, role),
        space_id=context.space_id,
        actor=actor,
        session=session,
    )


async def _execute_role_assistant_query(
    *,
    context: AssistantQueryContext,
    role: str,
    message: str,
    planning_message: str | None = None,
    settings: Settings,
) -> RoleAssistantQueryResponse:
    user_message = planning_message or message
    retrieval_mode = (
        await detect_catalog_retrieval_capabilities(
            session=getattr(context, "session", None),
            semantic_enabled=settings.role_assistant_semantic_search_enabled,
        )
    ).mode
    if settings.role_assistant_runtime == "pydantic_ai":
        # Avoid making two remote model calls. The local plan remains the
        # deterministic coverage floor while PydanticAI may add relevant tools.
        plan = build_local_plan(role=role, message=user_message)
    else:
        plan = await plan_role_assistant(
            role=role, message=user_message, settings=settings
        )
    runtime = "legacy"
    response_plan_source = plan.source

    if plan.intent == "analyze_research_demand" and role == "data_requester":
        # Use the same governed internal + external pair projection as the
        # dedicated demand-assistant endpoint. External records remain
        # compare-only unless their profile explicitly passes later hard gates.
        from app.api.routes.applications import (
            _application_options_payload,
            _assistant_pair_catalog_payload,
        )

        options = await _application_options_payload(context.session, context.space_id)
        pair_catalog = await _assistant_pair_catalog_payload(
            context.session,
            context.space_id,
        )
        try:
            demand = recommend_research_demand(
                user_message,
                data_products=options["data_products"],
                model_products=options["model_products"],
                pair_data_products=[
                    *options["data_products"],
                    *pair_catalog["data_products"],
                ],
                pair_model_products=[
                    *options["model_products"],
                    *pair_catalog["model_products"],
                ],
                pair_relations=pair_catalog["pair_relations"],
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="请用一句完整的话描述研究人群、任务和目标。",
            ) from exc
        demand_document = demand if isinstance(demand, dict) else demand.model_dump(mode="json")
        pair_count = len(demand_document.get("pair_candidates", []))
        candidate_count = (
            len(demand_document.get("data_recommendations", []))
            + len(demand_document.get("model_recommendations", []))
            + pair_count
        )
        status = str(demand_document.get("status") or "needs_clarification")
        answer = (
            "已完成需求拆解，并从当前可申请目录中找到数据与模型候选。"
            if status == "ready"
            else "已完成需求拆解，并从真实目录中找到可比较的数据—模型组合；请根据硬门状态补齐申请或执行条件。"
            if pair_count
            else "已完成需求拆解；请根据澄清项或目录缺口补充信息。"
        )
        return RoleAssistantQueryResponse(
            runtime=runtime,
            retrieval_mode=retrieval_mode,
            plan_source=plan.source,
            intent=plan.intent,
            answer=answer,
            route_hint=plan.route_hint,
            demand_result=demand_document,
            tool_trace=[
                AssistantToolTrace(
                    tool="analyze_research_demand",
                    label="需求理解与目录匹配",
                    status="success" if candidate_count else "empty",
                    result_count=candidate_count,
                    source="medtrust.governed_pair_catalog",
                )
            ],
        )

    query_results: list[AssistantQueryResult] = []
    traces: list[AssistantToolTrace] = []
    deterministic_tools = tools_for_plan(
        role=context.role,
        plan=plan,
        message=user_message,
    )
    if settings.role_assistant_runtime == "pydantic_ai":
        agent_tools = set(deterministic_tools)
        if (
            not is_count_question(user_message)
            and agent_tools & {"search_data_products", "search_model_products"}
        ):
            agent_tools.add("get_product_details")
        agent_run = await run_pydantic_role_assistant(
            context=context,
            message=message,
            settings=settings,
            request_limit=settings.role_assistant_request_limit,
            tool_calls_limit=settings.role_assistant_tool_call_limit,
            allowed_tools=frozenset(agent_tools),
        )
        if agent_run is not None:
            query_results.extend(agent_run.results)
            traces.extend(agent_run.traces)
            runtime = "pydantic_ai"
            response_plan_source = agent_run.source

    called_tools = {trace.tool for trace in traces}
    for tool_name in deterministic_tools:
        if tool_name in called_tools:
            continue
        result, trace = await execute_tool(
            name=tool_name,
            context=context,
            message=message,
        )
        traces.append(trace)
        if result is not None:
            query_results.append(result)

    compatibility_evidence: list[AssistantCompatibilityEvidence] = [
        item
        for result in query_results
        for item in result.compatibility_evidence
    ][:20]
    lineage: list[AssistantExecutionLineage] = [
        item for result in query_results for item in result.lineage
    ][:5]

    if is_count_question(user_message):
        metrics = [
            AssistantCountMetric(label=result.label, count=result.total, unit=result.unit)
            for result in query_results
        ]
        if metrics:
            answer = _format_count_answer(query_results)
        elif traces and all(trace.status == "error" for trace in traces):
            answer = "当前暂时无法读取相关资源数量，请稍后重试。"
        else:
            answer = "当前接口没有返回可确认的完整数量。"
        return RoleAssistantQueryResponse(
            runtime=runtime,
            retrieval_mode=retrieval_mode,
            plan_source=response_plan_source,
            intent=plan.intent,
            answer=answer,
            route_hint=plan.route_hint,
            metrics=metrics,
            compatibility_evidence=compatibility_evidence,
            lineage=lineage,
            tool_trace=traces,
        )

    compatibility_requested = any(
        result.source == "medtrust.dataset_model_evidence" for result in query_results
    )
    lineage_requested = any(
        result.source == "medtrust.execution_lineage_projection"
        for result in query_results
    )
    if compatibility_requested or lineage_requested:
        summaries: list[str] = []
        if compatibility_requested:
            assessed = [
                item for item in compatibility_evidence if item.status != "not_assessed"
            ]
            summaries.append(
                f"已读取 {len(assessed)} 组平台已保存的数据—模型适配证据，本次没有触发新检查。"
                if assessed
                else "当前可见范围内尚无已保存的版本配对证据；状态为“尚未评估”，不代表兼容或不兼容。"
            )
        if lineage_requested:
            summaries.append(
                f"已定位 {len(lineage)} 条当前账号可见的执行血缘。"
                if lineage
                else "当前账号可见范围内没有可展示的执行血缘。"
            )
        results = [item for result in query_results for item in result.items][:20]
        return RoleAssistantQueryResponse(
            runtime=runtime,
            retrieval_mode=retrieval_mode,
            plan_source=response_plan_source,
            intent=plan.intent,
            answer=" ".join(summaries),
            route_hint=plan.route_hint,
            results=results,
            compatibility_evidence=compatibility_evidence,
            lineage=lineage,
            tool_trace=traces,
        )

    if len(query_results) == 1 and query_results[0].source == "medtrust.service_requests":
        request_result = query_results[0]
        results = request_result.items[:20]
        answer = (
            f"已读取当前账号可见的 {request_result.total} 项{request_result.label}；"
            "可在“我的申请”查看详情和下一步。"
            if request_result.total
            else f"当前账号可见范围内没有{request_result.label}。"
        )
        return RoleAssistantQueryResponse(
            runtime=runtime,
            retrieval_mode=retrieval_mode,
            plan_source=response_plan_source,
            intent=plan.intent,
            answer=answer,
            route_hint="/applications",
            results=results,
            tool_trace=traces,
        )

    combined: list[AssistantResource] = []
    seen: set[str] = set()
    for result in query_results:
        for item in result.items:
            if item.key in seen:
                continue
            seen.add(item.key)
            combined.append(item)
            if len(combined) == 20:
                break
        if len(combined) == 20:
            break
    if combined:
        total = sum(result.total for result in query_results)
        if total > len(combined):
            answer = (
                f"在当前账号可见范围内共命中 {total} 项相关记录，"
                f"当前展示前 {len(combined)} 项。"
            )
        else:
            answer = f"在当前账号可见范围内找到 {len(combined)} 项相关记录。"
    elif traces and all(trace.status == "error" for trace in traces):
        answer = "当前暂时无法读取相关业务记录，请稍后重试。"
    else:
        answer = "在当前账号可见范围内未找到匹配记录。你可以换用编号、产品名、疾病或器官名称再次搜索。"
    return RoleAssistantQueryResponse(
        runtime=runtime,
        retrieval_mode=retrieval_mode,
        plan_source=response_plan_source,
        intent=plan.intent,
        answer=answer,
        route_hint=plan.route_hint,
        results=combined,
        tool_trace=traces,
    )


async def _begin_optional_state(
    *,
    context: AssistantQueryContext,
    session: AsyncSession,
    message: str,
    conversation_id: UUID | None,
    enabled: bool,
) -> AgentTurnState | None:
    if not enabled or not supports_persistent_agent_state(
        context=context, session=session
    ):
        return None
    try:
        return await begin_agent_turn(
            session=session,
            context=context,
            message=message,
            conversation_id=conversation_id,
        )
    except AgentConversationAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        # Agent state is an auxiliary capability. A deployment that has not yet
        # applied the new migration must retain the legacy stateless query path.
        logger.warning(
            "Role assistant state unavailable: error_type=%s",
            type(exc).__name__,
        )
        await session.rollback()
        return None


async def query_role_assistant(
    *,
    role: str,
    message: str,
    settings: Settings,
    session: AsyncSession,
    conversation_id: UUID | None = None,
) -> RoleAssistantQueryResponse:
    context = await _query_context(role=role, session=session)
    state = await _begin_optional_state(
        context=context,
        session=session,
        message=message,
        conversation_id=conversation_id,
        enabled=settings.role_assistant_state_enabled,
    )
    resolved_message = state.resolved_message if state is not None else message
    response = await _execute_role_assistant_query(
        context=context,
        role=role,
        message=resolved_message,
        planning_message=message,
        settings=settings,
    )
    if state is None:
        return response

    try:
        await complete_agent_turn(
            session=session,
            state=state,
            response=response,
            provider=(
                settings.role_assistant_provider
                if response.plan_source in {"openai", "deepseek"}
                else None
            ),
            model_name=(
                settings.role_assistant_openai_model
                if response.plan_source in {"openai", "deepseek"}
                else None
            ),
        )
        await session.commit()
    except SQLAlchemyError as exc:
        logger.warning(
            "Role assistant trace persistence failed: error_type=%s",
            type(exc).__name__,
        )
        await session.rollback()
        return response

    return response.model_copy(
        update={
            "conversation_id": state.conversation.id,
            "turn_id": state.turn.id,
            "context_applied": state.context_applied,
        }
    )
