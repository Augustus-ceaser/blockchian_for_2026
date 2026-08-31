from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal, cast
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings

PublicRole = Literal[
    "space_operator",
    "data_provider",
    "model_provider",
    "data_requester",
]
ResourceKind = Literal[
    "contract",
    "data",
    "service",
    "model",
    "application",
    "execution",
    "result",
    "lifecycle",
    "workflow",
]
PlanIntent = Literal[
    "analyze_research_demand",
    "search_resources",
    "open_workflow",
]
PlanSource = Literal["openai", "deepseek", "local"]

PUBLIC_ROLES = frozenset(
    {"space_operator", "data_provider", "model_provider", "data_requester"}
)
ROLE_ALLOWED_RESOURCE_KINDS: dict[str, tuple[ResourceKind, ...]] = {
    "space_operator": (
        "contract",
        "data",
        "service",
        "model",
        "application",
        "execution",
        "result",
        "lifecycle",
        "workflow",
    ),
    "data_provider": (
        "contract",
        "data",
        "service",
        "model",
        "application",
        "execution",
        "result",
        "lifecycle",
        "workflow",
    ),
    "model_provider": (
        "contract",
        "data",
        "service",
        "model",
        "application",
        "execution",
        "result",
        "lifecycle",
        "workflow",
    ),
    "data_requester": (
        "contract",
        "data",
        "service",
        "model",
        "application",
        "execution",
        "result",
        "workflow",
    ),
}

_DEFAULT_RESOURCE_KIND: dict[str, ResourceKind] = {
    "space_operator": "application",
    "data_provider": "data",
    "model_provider": "model",
    "data_requester": "application",
}
PUBLIC_DATA_CATALOG_ROUTE = "/external-catalog/datasets"
PUBLIC_MODEL_CATALOG_ROUTE = "/external-catalog/models"
_DATA_RESOURCE_PATTERN = re.compile(
    r"数据集|数据产品|数据目录|数据库|资料库|公共库|数据资源|数据资产|数据|病历|影像|病理|"
    r"dataset|database|data",
    re.IGNORECASE,
)
_MODEL_RESOURCE_PATTERN = re.compile(r"模型|算法|model", re.IGNORECASE)
_PUBLIC_SCOPE_PATTERN = re.compile(r"公共|公开|开放|候选", re.IGNORECASE)
_PRODUCT_REVIEW_PATTERN = re.compile(
    r"待审|待办|待处理|未处理|待上架|待发布|待下架|待归档|"
    r"(?:数据|模型)(?:产品)?.{0,8}(?:审核|审批)|"
    r"(?:审核|审批).{0,8}(?:数据|模型)(?:产品)?|"
    r"(?:上架|发布|下架|归档)(?:审核|审批|申请)",
    re.IGNORECASE,
)
_SERVICE_RESOURCE_PATTERN = re.compile(
    r"第三方(?:数据|API|接口)?服务|数据服务|API\s*服务|接口服务|服务能力|"
    r"service\s*(?:catalog|status|health|availability)",
    re.IGNORECASE,
)
_RESOURCE_PATTERNS: tuple[tuple[ResourceKind, re.Pattern[str]], ...] = (
    ("contract", re.compile(r"合约|合同|contract", re.IGNORECASE)),
    ("service", _SERVICE_RESOURCE_PATTERN),
    ("data", _DATA_RESOURCE_PATTERN),
    ("model", _MODEL_RESOURCE_PATTERN),
    ("application", re.compile(r"需求|申请|审批|待办|application", re.IGNORECASE)),
    ("execution", re.compile(r"执行|计算|运行|任务|就绪|进度|execution", re.IGNORECASE)),
    ("result", re.compile(r"结果|报告|下载|result", re.IGNORECASE)),
    (
        "lifecycle",
        re.compile(
            r"生命周期|待上架|待发布|待下架|待归档|"
            r"(?:上架|下架|发布|归档|版本)(?:申请|审核|审批|事项|待办|管理|流程|状态)|"
            r"lifecycle",
            re.IGNORECASE,
        ),
    ),
    ("workflow", re.compile(r"流程|链路|下一步|workflow", re.IGNORECASE)),
)
_RESEARCH_TASK_PATTERN = re.compile(
    r"(?:构建|建立|训练|开发|做).{0,24}模型|"
    r"(?:我想|希望|计划|准备|需要).{0,40}(?:判断|识别|分类|分级|检测|分割|预测|验证)|"
    r"预测|风险|科研|研究|验证.+模型|临床效果|人群|结局",
    re.IGNORECASE,
)
_LOOKUP_PATTERN = re.compile(
    r"查找|搜索|查看|定位|我的|编号|合约|合同|进度|结果|下载|待办|审批",
    re.IGNORECASE,
)
_APPLICATION_REFERENCE_PATTERN = re.compile(
    r"(?:授权|许可|交付|计算|服务)申请|我的.{0,24}申请|"
    r"申请(?:单|编号|记录|状态|进度|审核|审批)|需求单|"
    r"\b(?:SAR|APP)-[A-Z0-9-]+\b",
    re.IGNORECASE,
)
_APPLICATION_STATUS_LOOKUP_PATTERN = re.compile(
    r"我的|有哪些|多少|状态|进度|到哪一步|进行到哪|待审|待批|待办|"
    r"已批准|已拒绝|查询|查找|查看|编号|\b(?:SAR|APP)-[A-Z0-9-]+\b",
    re.IGNORECASE,
)
_WORKFLOW_PATTERN = re.compile(r"怎么|如何|流程|下一步|该做什么|怎样", re.IGNORECASE)
_INTENT_BY_TOOL: dict[str, PlanIntent] = {
    "analyze_research_demand": "analyze_research_demand",
    "search_resources": "search_resources",
    "open_workflow": "open_workflow",
}
_TOOL_BY_INTENT: dict[PlanIntent, str] = {
    intent: tool_name for tool_name, intent in _INTENT_BY_TOOL.items()
}
_MAX_RESPONSE_BYTES = 1024 * 1024
_SENSITIVE_IDENTIFIER_PATTERNS = (
    re.compile(r"(?:姓名|患者姓名)\s*[:：]?\s*[\u4e00-\u9fff]{2,6}"),
    re.compile(r"(?:病历号|住院号|门诊号|身份证号)\s*[:：]?\s*[A-Za-z0-9-]{4,}"),
    re.compile(r"(?:邮箱|电子邮箱|email)\s*[:：]?\s*[^\s@]+@[^\s@]+\.[^\s@]+", re.I),
    re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
)


def contains_sensitive_identifier(message: str) -> bool:
    return any(pattern.search(message) for pattern in _SENSITIVE_IDENTIFIER_PATTERNS)


def is_application_status_lookup(message: str) -> bool:
    """Recognize status lookups without letting product nouns expand catalog scope."""

    return bool(
        _APPLICATION_REFERENCE_PATTERN.search(message)
        and _APPLICATION_STATUS_LOOKUP_PATTERN.search(message)
    )


class RoleAssistantPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: PlanSource
    intent: PlanIntent
    resource_kinds: list[ResourceKind] = Field(min_length=1, max_length=9)
    query: str = Field(min_length=1, max_length=2000)
    route_hint: str | None = None
    read_only: Literal[True] = True


def _route_for(role: str, kind: ResourceKind) -> str:
    if kind == "service":
        return "/data-catalog"
    if kind == "data":
        return "/data-products" if role in {"space_operator", "data_provider"} else "/data-catalog"
    if kind == "model":
        return (
            "/model-products"
            if role in {"space_operator", "model_provider"}
            else "/model-catalog"
        )
    return {
        "contract": "/contracts",
        "application": "/applications",
        "execution": "/execution",
        "result": "/results",
        "lifecycle": "/lifecycle",
        "workflow": "/workflow",
    }[kind]


def _route_for_query(role: str, kind: ResourceKind, query: str) -> str:
    if (
        kind == "data"
        and _PUBLIC_SCOPE_PATTERN.search(query)
        and _DATA_RESOURCE_PATTERN.search(query)
    ):
        return PUBLIC_DATA_CATALOG_ROUTE
    if (
        kind == "model"
        and _PUBLIC_SCOPE_PATTERN.search(query)
        and _MODEL_RESOURCE_PATTERN.search(query)
    ):
        return PUBLIC_MODEL_CATALOG_ROUTE
    if role == "space_operator" and kind == "data":
        return (
            "/data-products"
            if _PRODUCT_REVIEW_PATTERN.search(query)
            else "/data-catalog"
        )
    if role == "space_operator" and kind == "model":
        return (
            "/model-products"
            if _PRODUCT_REVIEW_PATTERN.search(query)
            else "/model-catalog"
        )
    return _route_for(role, kind)


def _allowed_routes(role: str, kinds: list[ResourceKind]) -> set[str]:
    routes = {_route_for(role, kind) for kind in kinds}
    if "data" in kinds:
        routes.add(PUBLIC_DATA_CATALOG_ROUTE)
        if role == "space_operator":
            routes.add("/data-catalog")
    if "model" in kinds:
        routes.add(PUBLIC_MODEL_CATALOG_ROUTE)
        if role == "space_operator":
            routes.add("/model-catalog")
    return routes


def _normalize_catalog_route(
    *,
    role: str,
    kinds: list[ResourceKind],
    query: str,
    route_hint: str | None,
) -> str:
    route_families: tuple[tuple[ResourceKind, re.Pattern[str], set[str]], ...] = (
        (
            "data",
            _DATA_RESOURCE_PATTERN,
            {PUBLIC_DATA_CATALOG_ROUTE, "/data-catalog", "/data-products"},
        ),
        (
            "model",
            _MODEL_RESOURCE_PATTERN,
            {PUBLIC_MODEL_CATALOG_ROUTE, "/model-catalog", "/model-products"},
        ),
    )
    for kind, pattern, routes in route_families:
        if (
            kind in kinds
            and pattern.search(query)
            and (route_hint is None or route_hint in routes)
        ):
            return _route_for_query(role, kind, query)
    return route_hint or _route_for(role, kinds[0])


def build_local_plan(*, role: str, message: str) -> RoleAssistantPlan:
    if role not in PUBLIC_ROLES:
        raise ValueError("unsupported role assistant identity")
    query = message.strip()
    if not query:
        raise ValueError("assistant message is empty")

    if (
        role == "data_requester"
        and len(query) >= 10
        and _RESEARCH_TASK_PATTERN.search(query)
        and not _LOOKUP_PATTERN.search(query)
    ):
        return RoleAssistantPlan(
            source="local",
            intent="analyze_research_demand",
            resource_kinds=["data", "model", "application", "workflow"],
            query=query,
            route_hint="/applications/new",
        )

    if is_application_status_lookup(query):
        return RoleAssistantPlan(
            source="local",
            intent="search_resources",
            resource_kinds=["application"],
            query=query,
            route_hint="/applications",
        )

    allowed = ROLE_ALLOWED_RESOURCE_KINDS[role]
    matched = (
        ["service"]
        if "service" in allowed and _SERVICE_RESOURCE_PATTERN.search(query)
        else [
            kind
            for kind, pattern in _RESOURCE_PATTERNS
            if kind in allowed and pattern.search(query)
        ]
    )
    if not matched:
        matched = [_DEFAULT_RESOURCE_KIND[role]]
    intent: PlanIntent = "open_workflow" if _WORKFLOW_PATTERN.search(query) else "search_resources"
    if intent == "open_workflow" and "workflow" in allowed and "workflow" not in matched:
        matched.append("workflow")
    route_kind = "workflow" if intent == "open_workflow" and "workflow" in matched else matched[0]
    return RoleAssistantPlan(
        source="local",
        intent=intent,
        resource_kinds=matched,
        query=query,
        route_hint=_route_for_query(role, route_kind, query),
    )


def _function_tool(
    *,
    name: str,
    description: str,
    query_description: str,
    allowed_kinds: tuple[ResourceKind, ...],
    route_hints: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": query_description,
                    "minLength": 1,
                    "maxLength": 2000,
                },
                "resource_kinds": {
                    "type": "array",
                    "description": "Only the platform resource categories that must be queried.",
                    "items": {"type": "string", "enum": list(allowed_kinds)},
                    "minItems": 1,
                    "maxItems": len(allowed_kinds),
                },
                "route_hint": {
                    "type": ["string", "null"],
                    "description": (
                        "An optional allowlisted in-product route, never an external URL."
                    ),
                    "enum": [*route_hints, None],
                },
            },
            "required": ["query", "resource_kinds", "route_hint"],
            "additionalProperties": False,
        },
    }


def _tools_for_role(role: str) -> list[dict[str, Any]]:
    allowed_kinds = ROLE_ALLOWED_RESOURCE_KINDS[role]
    route_hints = sorted(_allowed_routes(role, list(allowed_kinds)))
    tools = [
        _function_tool(
            name="search_resources",
            description=(
                "Plan a read-only search of MedTrust Space resources visible to the current role. "
                "Do not return resource records or claim that a resource exists."
            ),
            query_description=(
                "A concise search expression derived only from the user's request. Preserve every "
                "identifier exactly. You may normalize disease, organ, modality, and task terms to "
                "the English catalog vocabulary, but never add a guessed resource title or ID."
            ),
            allowed_kinds=allowed_kinds,
            route_hints=route_hints,
        ),
        _function_tool(
            name="open_workflow",
            description=(
                "Plan read-only navigation to a MedTrust Space workflow or next-step page. "
                "Do not save, submit, approve, sign, execute, or download anything."
            ),
            query_description=(
                "A concise workflow search expression derived only from the user's request. "
                "Preserve every identifier exactly and never add a guessed resource title or ID."
            ),
            allowed_kinds=allowed_kinds,
            route_hints=route_hints,
        ),
    ]
    if role == "data_requester":
        tools.insert(
            0,
            _function_tool(
                name="analyze_research_demand",
                description=(
                    "Route a research task to the existing governed demand analyzer, which will "
                    "structure the demand and query published platform data and model catalogs."
                ),
                query_description="The user's original research-demand text without rewriting.",
                allowed_kinds=("data", "model", "application", "workflow"),
                route_hints=["/applications/new"],
            ),
        )
    return tools


def _responses_payload(
    *,
    role: str,
    message: str,
    intent: PlanIntent,
    settings: Settings,
) -> dict[str, Any]:
    selected_tool_name = _TOOL_BY_INTENT[intent]
    selected_tools = [
        tool for tool in _tools_for_role(role) if tool["name"] == selected_tool_name
    ]
    if len(selected_tools) != 1:
        raise ValueError("role assistant tool policy is inconsistent")
    payload: dict[str, Any] = {
        "model": settings.role_assistant_openai_model,
        "store": False,
        "parallel_tool_calls": False,
        "tool_choice": {"type": "function", "name": selected_tool_name},
        "max_output_tokens": 400,
        "tools": selected_tools,
        "instructions": (
            "You are a read-only intent planner for MedTrust Space. Select exactly one provided "
            "function tool. Choose only resource_kinds and route_hint values present in that "
            "tool's schema. Never invent, summarize, or claim the existence of a dataset, model, "
            "contract, application, execution, result, or approval. Never plan a mutation, save, "
            "submission, approval, signature, execution, or download. The platform will perform "
            "the authorized resource lookup after this planning step. For resource searches, the "
            "query may normalize medical terms to concise catalog vocabulary, but it must preserve "
            "the user's meaning and every identifier, and it must not introduce a resource name. "
            "Service availability must be read from the platform service tool, never inferred "
            "from a product name."
        ),
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": message}],
            }
        ],
    }
    if settings.role_assistant_provider == "deepseek":
        # DeepSeek's Responses API enables thinking by default for current models.
        # Required function selection is only supported when thinking is disabled.
        payload["reasoning"] = {"effort": "none"}
        payload["temperature"] = 0
    return payload


def _request_openai(
    *,
    role: str,
    message: str,
    intent: PlanIntent,
    settings: Settings,
) -> dict[str, Any]:
    payload = _responses_payload(
        role=role,
        message=message,
        intent=intent,
        settings=settings,
    )
    api_key = settings.role_assistant_openai_api_key.get_secret_value().strip()
    request = Request(
        f"{settings.role_assistant_openai_base_url.rstrip('/')}/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(
        request,
        timeout=settings.role_assistant_openai_timeout_seconds,
    ) as response:
        if getattr(response, "status", 200) != 200:
            raise ValueError("OpenAI Responses request was not successful")
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("OpenAI Responses payload is too large")
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("OpenAI Responses payload must be an object")
    return document


def _parse_openai_plan(
    document: dict[str, Any],
    *,
    role: str,
    message: str,
    source: Literal["openai", "deepseek"] = "openai",
) -> RoleAssistantPlan:
    output = document.get("output")
    if not isinstance(output, list):
        raise ValueError("OpenAI Responses output is missing")
    calls = [
        item
        for item in output
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    if len(calls) != 1:
        raise ValueError("OpenAI Responses must contain exactly one function call")

    call = calls[0]
    name = call.get("name")
    if not isinstance(name, str) or name not in _INTENT_BY_TOOL:
        raise ValueError("OpenAI Responses selected an unsupported function")
    intent = _INTENT_BY_TOOL[name]
    if intent == "analyze_research_demand" and role != "data_requester":
        raise ValueError("research demand analysis is requester-only")

    arguments_raw = call.get("arguments")
    if not isinstance(arguments_raw, str):
        raise ValueError("OpenAI function arguments are missing")
    arguments = json.loads(arguments_raw)
    if not isinstance(arguments, dict) or set(arguments) != {
        "query",
        "resource_kinds",
        "route_hint",
    }:
        raise ValueError("OpenAI function arguments do not match the strict schema")
    if not isinstance(arguments["query"], str) or not arguments["query"].strip():
        raise ValueError("OpenAI function query is invalid")
    normalized_query = arguments["query"].strip()
    if len(normalized_query) > 2000:
        raise ValueError("OpenAI function query is too long")
    identifiers = re.findall(r"(?i)\b(?=[A-Z0-9._-]*\d)[A-Z0-9][A-Z0-9._-]*\b", message)
    if any(identifier.casefold() not in normalized_query.casefold() for identifier in identifiers):
        raise ValueError("OpenAI function query dropped a user identifier")

    raw_kinds = arguments["resource_kinds"]
    if not isinstance(raw_kinds, list) or not raw_kinds or len(raw_kinds) > 8:
        raise ValueError("OpenAI resource kinds are invalid")
    if any(not isinstance(kind, str) for kind in raw_kinds):
        raise ValueError("OpenAI resource kinds are invalid")
    if len(set(raw_kinds)) != len(raw_kinds):
        raise ValueError("OpenAI resource kinds must be unique")
    allowed = set(ROLE_ALLOWED_RESOURCE_KINDS[role])
    if any(kind not in allowed for kind in raw_kinds):
        raise ValueError("OpenAI selected a resource kind outside the role boundary")
    kinds = cast(list[ResourceKind], list(raw_kinds))

    route_hint = arguments["route_hint"]
    if route_hint is not None and not isinstance(route_hint, str):
        raise ValueError("OpenAI route hint is invalid")
    if intent == "analyze_research_demand":
        kinds = ["data", "model", "application", "workflow"]
        route_hint = "/applications/new"
    else:
        if intent == "open_workflow" and "workflow" not in kinds:
            raise ValueError("workflow planning must include the workflow resource kind")
        allowed_routes = _allowed_routes(role, kinds)
        if route_hint is not None and route_hint not in allowed_routes:
            raise ValueError("OpenAI route hint is outside the selected resource kinds")
        route_hint = _normalize_catalog_route(
            role=role,
            kinds=kinds,
            query=message,
            route_hint=route_hint,
        )

    return RoleAssistantPlan(
        source=source,
        intent=intent,
        resource_kinds=kinds,
        query=message.strip() if intent == "analyze_research_demand" else normalized_query,
        route_hint=route_hint,
    )


async def plan_role_assistant(
    *,
    role: str,
    message: str,
    settings: Settings,
) -> RoleAssistantPlan:
    fallback = build_local_plan(role=role, message=message)
    if is_application_status_lookup(message):
        # Application state is a precise platform lookup. Keep it deterministic
        # so a remote planner cannot broaden "data/model authorization requests"
        # into unrelated catalog searches or a misleading marketplace route.
        return fallback
    if not settings.role_assistant_openai_enabled:
        return fallback
    if not settings.role_assistant_openai_api_key.get_secret_value().strip():
        return fallback
    if contains_sensitive_identifier(message):
        return fallback
    try:
        document = await asyncio.to_thread(
            _request_openai,
            role=role,
            message=message.strip(),
            intent=fallback.intent,
            settings=settings,
        )
        return _parse_openai_plan(
            document,
            role=role,
            message=message,
            source=settings.role_assistant_provider,
        )
    except Exception:
        # This optional planner must never make the local demo depend on a remote API.
        return fallback
