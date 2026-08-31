from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Awaitable, Callable, Literal

from app.modules.role_assistant.planner import PublicRole, RoleAssistantPlan
from app.modules.role_assistant.query_service import (
    AssistantQueryContext,
    AssistantQueryResult,
    check_data_model_compatibility,
    get_contract_status,
    get_execution_lineage,
    get_execution_status,
    get_lifecycle_status,
    get_product_details,
    get_request_status,
    get_result_status,
    read_compatibility_status,
    search_data_services,
    search_data_products,
    search_model_products,
)
from app.modules.role_assistant.schemas import AssistantToolTrace, ToolRiskClass


logger = logging.getLogger(__name__)


AssistantToolName = Literal[
    "search_data_products",
    "search_data_services",
    "search_model_products",
    "get_request_status",
    "get_contract_status",
    "get_execution_status",
    "get_result_status",
    "get_lifecycle_status",
    "read_compatibility_status",
    "get_product_details",
    "check_data_model_compatibility",
    "get_execution_lineage",
]
ToolHandler = Callable[
    [AssistantQueryContext, str], Awaitable[AssistantQueryResult]
]
ToolExposure = Literal["agent", "manual"]


@dataclass(frozen=True)
class AssistantToolDefinition:
    name: AssistantToolName
    label: str
    allowed_roles: frozenset[PublicRole]
    risk_class: ToolRiskClass
    read_only: bool
    idempotent: bool
    requires_confirmation: bool
    exposure: ToolExposure
    handler: ToolHandler

    def __post_init__(self) -> None:
        if self.risk_class == "read" and not self.read_only:
            raise ValueError("read tools must be declared read_only")
        if self.risk_class == "commit" and self.exposure == "agent":
            raise ValueError("commit tools cannot be exposed to the model")


ALL_PUBLIC_ROLES: frozenset[PublicRole] = frozenset(
    {"space_operator", "data_provider", "model_provider", "data_requester"}
)
PROVIDER_AND_OPERATOR_ROLES: frozenset[PublicRole] = frozenset(
    {"space_operator", "data_provider", "model_provider"}
)


TOOL_REGISTRY: dict[AssistantToolName, AssistantToolDefinition] = {
    "search_data_products": AssistantToolDefinition(
        name="search_data_products",
        label="数据目录",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=search_data_products,
    ),
    "search_data_services": AssistantToolDefinition(
        name="search_data_services",
        label="数据服务",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=search_data_services,
    ),
    "search_model_products": AssistantToolDefinition(
        name="search_model_products",
        label="模型目录",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=search_model_products,
    ),
    "get_request_status": AssistantToolDefinition(
        name="get_request_status",
        label="服务申请",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=get_request_status,
    ),
    "get_contract_status": AssistantToolDefinition(
        name="get_contract_status",
        label="数字合约",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=get_contract_status,
    ),
    "get_execution_status": AssistantToolDefinition(
        name="get_execution_status",
        label="执行进度",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=get_execution_status,
    ),
    "get_result_status": AssistantToolDefinition(
        name="get_result_status",
        label="结果记录",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=get_result_status,
    ),
    "get_lifecycle_status": AssistantToolDefinition(
        name="get_lifecycle_status",
        label="生命周期事项",
        allowed_roles=PROVIDER_AND_OPERATOR_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=get_lifecycle_status,
    ),
    "read_compatibility_status": AssistantToolDefinition(
        name="read_compatibility_status",
        label="兼容性状态",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=read_compatibility_status,
    ),
    "get_product_details": AssistantToolDefinition(
        name="get_product_details",
        label="产品详情",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=get_product_details,
    ),
    "check_data_model_compatibility": AssistantToolDefinition(
        name="check_data_model_compatibility",
        label="数据—模型适配证据",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=check_data_model_compatibility,
    ),
    "get_execution_lineage": AssistantToolDefinition(
        name="get_execution_lineage",
        label="执行血缘",
        allowed_roles=ALL_PUBLIC_ROLES,
        risk_class="read",
        read_only=True,
        idempotent=True,
        requires_confirmation=False,
        exposure="agent",
        handler=get_execution_lineage,
    ),
}


_KIND_TO_TOOL: dict[str, AssistantToolName] = {
    "data": "search_data_products",
    "service": "search_data_services",
    "model": "search_model_products",
    "application": "get_request_status",
    "contract": "get_contract_status",
    "execution": "get_execution_status",
    "result": "get_result_status",
    "lifecycle": "get_lifecycle_status",
    "workflow": "get_request_status",
}


def tools_for_plan(
    *,
    role: PublicRole,
    plan: RoleAssistantPlan,
    message: str,
) -> list[AssistantToolName]:
    exact_tools: list[AssistantToolName] = []
    if re.search(r"血缘|证据链|从申请到结果|执行链路|全过程|追溯", message, re.IGNORECASE):
        exact_tools.append("get_execution_lineage")
    if re.search(
        r"(?:数据|数据集|数据产品).{0,32}(?:模型|算法).{0,24}(?:适配|兼容|证据)|"
        r"(?:模型|算法).{0,32}(?:数据|数据集|数据产品).{0,24}(?:适配|兼容|证据)|"
        r"适配证据|证据矩阵|数据模型证据",
        message,
        re.IGNORECASE,
    ):
        exact_tools.append("check_data_model_compatibility")
    if exact_tools:
        return list(dict.fromkeys(exact_tools))
    if "兼容" in message:
        return ["read_compatibility_status"]
    if re.search(r"(?:产品|数据|模型).{0,12}(?:详情|版本|质量|许可|来源)", message):
        return ["get_product_details"]
    selected: list[AssistantToolName] = []
    for kind in plan.resource_kinds:
        name = _KIND_TO_TOOL[kind]
        definition = TOOL_REGISTRY[name]
        if (
            role in definition.allowed_roles
            and definition.exposure == "agent"
            and definition.risk_class != "commit"
            and name not in selected
        ):
            selected.append(name)
    if not selected:
        selected.append("get_request_status")
    return selected[:8]


async def execute_tool(
    *,
    name: AssistantToolName,
    context: AssistantQueryContext,
    message: str,
) -> tuple[AssistantQueryResult | None, AssistantToolTrace]:
    definition = TOOL_REGISTRY[name]
    if (
        context.role not in definition.allowed_roles
        or definition.exposure != "agent"
        or definition.risk_class == "commit"
    ):
        raise PermissionError(f"role {context.role} cannot invoke {name}")
    started = perf_counter()
    transaction = None
    before_new = set(context.session.new)
    before_dirty = set(context.session.dirty)
    before_deleted = set(context.session.deleted)
    try:
        transaction = await context.session.begin_nested()
        result = await definition.handler(context, message)
        attempted_write = (
            bool(set(context.session.new) - before_new)
            or bool(set(context.session.dirty) - before_dirty)
            or bool(set(context.session.deleted) - before_deleted)
        )
        if attempted_write:
            raise RuntimeError("read-only tool attempted to mutate ORM state")
    except Exception as exc:
        # Do not expose database, transport, or object-boundary details to the client.
        logger.warning(
            "Role assistant tool failed: tool=%s error_type=%s",
            name,
            type(exc).__name__,
        )
        return None, AssistantToolTrace(
            tool=name,
            label=definition.label,
            status="error",
            result_count=0,
            source="medtrust.platform_query",
            risk_class=definition.risk_class,
            authorization_result="allowed",
            requires_confirmation=definition.requires_confirmation,
            duration_ms=max(0, int((perf_counter() - started) * 1000)),
            error_code="TOOL_EXECUTION_FAILED",
        )
    finally:
        if transaction is not None and transaction.is_active:
            await transaction.rollback()
    return result, AssistantToolTrace(
        tool=name,
        label=definition.label,
        status="success" if result.total else "empty",
        result_count=result.total,
        source=result.source,
        risk_class=definition.risk_class,
        authorization_result="allowed",
        requires_confirmation=definition.requires_confirmation,
        duration_ms=max(0, int((perf_counter() - started) * 1000)),
    )
