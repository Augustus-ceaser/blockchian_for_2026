from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import Agent, RunContext, Tool, UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.models.openai import (
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.config import Settings
from app.modules.role_assistant.planner import contains_sensitive_identifier
from app.modules.role_assistant.query_service import (
    AssistantQueryContext,
    AssistantQueryResult,
    is_count_question,
)
from app.modules.role_assistant.registry import (
    TOOL_REGISTRY,
    AssistantToolName,
    execute_tool,
)
from app.modules.role_assistant.schemas import AssistantToolTrace


logger = logging.getLogger(__name__)

MAX_MODEL_REQUESTS = 4
MAX_TOOL_CALLS = 8

_INSTRUCTIONS = (
    "You are the read-only tool orchestrator for MedTrust Space. "
    "Use one or more provided tools whenever the request can be answered from platform data. "
    "The server has already bound the authenticated role, space, organization, and database "
    "session; never ask for or invent them. Never claim that a resource exists based on model "
    "knowledge. Never save, submit, approve, sign, execute, download, or mutate anything. "
    "After the necessary tools return, stop calling tools. The server, not your prose, will "
    "assemble the user-visible answer from the structured tool results. Tool arguments may only "
    "contain query text or a resource key copied from the user request or a previous tool result."
)

_TOOL_DESCRIPTIONS: dict[AssistantToolName, str] = {
    "search_data_products": (
        "Search visible data products and public candidate dataset catalog metadata."
    ),
    "search_data_services": (
        "Search published data-service capabilities and read platform-computed availability."
    ),
    "search_model_products": "Search model products visible to the authenticated role.",
    "get_request_status": "Read visible controlled-compute and authorization request status.",
    "get_contract_status": "Read visible digital-contract status.",
    "get_execution_status": "Read visible execution and compute progress.",
    "get_result_status": "Read visible result and download-package status.",
    "get_lifecycle_status": "Read visible product lifecycle and review status.",
    "read_compatibility_status": "Read compatibility status saved for visible applications.",
    "get_product_details": "Read details for an explicitly referenced data or model product.",
    "check_data_model_compatibility": (
        "Read saved compatibility evidence for an explicitly referenced data-model pair."
    ),
    "get_execution_lineage": "Read the visible evidence chain from application to result.",
}


@dataclass(frozen=True)
class PydanticAssistantRun:
    source: Literal["openai", "deepseek"]
    results: tuple[AssistantQueryResult, ...]
    traces: tuple[AssistantToolTrace, ...]


@dataclass
class _AssistantAgentDeps:
    context: AssistantQueryContext
    message: str
    results: list[AssistantQueryResult] = field(default_factory=list)
    traces: list[AssistantToolTrace] = field(default_factory=list)
    called_tools: set[AssistantToolName] = field(default_factory=set)
    tool_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _provider_base_url(settings: Settings) -> str:
    base_url = settings.role_assistant_openai_base_url.rstrip("/")
    if settings.role_assistant_provider == "deepseek":
        return f"{base_url}/v1"
    return base_url


def build_role_assistant_model(settings: Settings) -> OpenAIResponsesModel | None:
    if not settings.role_assistant_openai_enabled:
        return None
    api_key = settings.role_assistant_openai_api_key.get_secret_value().strip()
    if not api_key:
        return None

    model_settings: OpenAIResponsesModelSettings = {
        "openai_store": False,
        "parallel_tool_calls": False,
        "timeout": settings.role_assistant_openai_timeout_seconds,
    }
    if settings.role_assistant_provider == "deepseek":
        model_settings.update(
            openai_reasoning_effort="none",
            temperature=0,
        )

    provider = OpenAIProvider(
        base_url=_provider_base_url(settings),
        api_key=api_key,
    )
    return OpenAIResponsesModel(
        settings.role_assistant_openai_model,
        provider=provider,
        settings=model_settings,
    )


def _result_payload(
    result: AssistantQueryResult | None,
    trace: AssistantToolTrace,
) -> dict[str, Any]:
    if result is None:
        return {
            "status": "error",
            "label": trace.label,
            "source": trace.source,
        }
    return {
        "status": trace.status,
        "label": result.label,
        "total": result.total,
        "unit": result.unit,
        "source": result.source,
        "items": [
            {
                "key": item.key,
                "kind": item.kind,
                "title": item.title,
                "status": item.status,
            }
            for item in result.items
        ],
        "compatibility_evidence": [
            {
                "data_name": item.data_name,
                "model_name": item.model_name,
                "status": item.status,
                "status_label": item.status_label,
            }
            for item in result.compatibility_evidence
        ],
        "lineage": [
            {
                "application_number": item.application_number,
                "status": item.status,
                "completed_nodes": item.completed_nodes,
                "total_nodes": item.total_nodes,
            }
            for item in result.lineage
        ],
    }


def _tool_for(name: AssistantToolName) -> Tool[_AssistantAgentDeps]:
    async def invoke(
        ctx: RunContext[_AssistantAgentDeps],
        query: Annotated[str | None, Field(max_length=256)] = None,
        key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        deps = ctx.deps
        async with deps.tool_lock:
            if name in deps.called_tools:
                return {"status": "duplicate", "tool": name}
            deps.called_tools.add(name)
            tool_message = deps.message
            known_items = [item for result in deps.results for item in result.items]
            selectors = () if is_count_question(deps.message) else (key, query)
            for selector in selectors:
                candidate = (selector or "").strip()
                if not candidate:
                    continue
                matched_item = next(
                    (
                        item
                        for item in known_items
                        if candidate.casefold()
                        in {item.key.casefold(), item.title.casefold()}
                    ),
                    None,
                )
                if matched_item is not None:
                    tool_message = (
                        f"{deps.message}\n[工具选择资源：{matched_item.title}（{matched_item.key}）]"
                    )
                    break
                if candidate.casefold() in deps.message.casefold():
                    tool_message = f"{deps.message}\n[工具查询：{candidate}]"
                    break
            result, trace = await execute_tool(
                name=name,
                context=deps.context,
                message=tool_message,
            )
            deps.traces.append(trace)
            if result is not None:
                deps.results.append(result)
            return _result_payload(result, trace)

    return Tool(
        invoke,
        takes_ctx=True,
        name=name,
        description=_TOOL_DESCRIPTIONS[name],
        sequential=True,
        max_retries=0,
    )


def _tools_for_context(
    context: AssistantQueryContext,
    allowed_tools: frozenset[AssistantToolName] | None = None,
) -> list[Tool[_AssistantAgentDeps]]:
    return [
        _tool_for(name)
        for name, definition in TOOL_REGISTRY.items()
        if context.role in definition.allowed_roles
        and (allowed_tools is None or name in allowed_tools)
    ]


async def run_pydantic_role_assistant(
    *,
    context: AssistantQueryContext,
    message: str,
    settings: Settings,
    model: Model | None = None,
    request_limit: int = MAX_MODEL_REQUESTS,
    tool_calls_limit: int = MAX_TOOL_CALLS,
    allowed_tools: frozenset[AssistantToolName] | None = None,
) -> PydanticAssistantRun | None:
    query = message.strip()
    if not query or len(query) > 2000 or contains_sensitive_identifier(query):
        return None

    selected_model = model or build_role_assistant_model(settings)
    if selected_model is None:
        return None

    tools = _tools_for_context(context, allowed_tools)
    if not tools:
        return None

    deps = _AssistantAgentDeps(context=context, message=query)
    agent = Agent(
        selected_model,
        deps_type=_AssistantAgentDeps,
        instructions=_INSTRUCTIONS,
        tools=tools,
        retries=0,
        max_concurrency=1,
    )
    limits = UsageLimits(
        request_limit=max(1, min(request_limit, MAX_MODEL_REQUESTS)),
        tool_calls_limit=max(1, min(tool_calls_limit, MAX_TOOL_CALLS)),
    )
    try:
        await agent.run(query, deps=deps, usage_limits=limits)
    except Exception as exc:
        logger.warning(
            "Pydantic role assistant runtime failed: provider=%s error_type=%s",
            settings.role_assistant_provider,
            type(exc).__name__,
        )
        return None

    if not deps.traces:
        return None
    return PydanticAssistantRun(
        source=settings.role_assistant_provider,
        results=tuple(deps.results),
        traces=tuple(deps.traces),
    )
