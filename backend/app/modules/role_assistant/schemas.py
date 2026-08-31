from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.role_assistant.planner import PlanIntent, PlanSource, ResourceKind


ToolStatus = Literal["success", "empty", "error"]
ToolRiskClass = Literal["read", "propose", "commit"]


class AssistantResource(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    kind: ResourceKind
    label: str
    title: str
    subtitle: str
    status: str | None = None
    path: str


class AssistantCountMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    count: int = Field(ge=0)
    unit: str


class AssistantToolTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    label: str
    status: ToolStatus
    result_count: int = Field(ge=0)
    source: str
    risk_class: ToolRiskClass = "read"
    authorization_result: Literal["allowed", "denied"] = "allowed"
    requires_confirmation: bool = False
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None


class AssistantCompatibilityEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    relation_id: str | None = None
    data_name: str | None = None
    data_version: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    status: str
    status_label: str
    evidence_level: str = "none"
    evidence_type: str | None = None
    outcome: str | None = None
    evidence_note: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list, max_length=20)
    warning_reasons: list[str] = Field(default_factory=list, max_length=20)
    transformation_requirements: list[str] = Field(default_factory=list, max_length=20)
    assessed_at: str | None = None
    path: str | None = None


class AssistantLineageNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    number: str | None = None
    status: str
    complete: bool
    state: Literal["completed", "active", "pending", "blocked"]
    responsible_role: str | None = None


class AssistantExecutionLineage(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    application_number: str
    scenario_name: str
    status: str
    completed_nodes: int = Field(ge=0)
    total_nodes: int = Field(ge=0)
    next_role: str | None = None
    next_action: str | None = None
    path: str
    nodes: list[AssistantLineageNode] = Field(default_factory=list, max_length=16)


class RoleAssistantQueryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["medtrust.role-assistant-query/v1"] = (
        "medtrust.role-assistant-query/v1"
    )
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    context_applied: bool = False
    runtime: Literal["legacy", "pydantic_ai"] = "legacy"
    retrieval_mode: Literal["structured", "lexical", "hybrid"] = "lexical"
    plan_source: PlanSource
    intent: PlanIntent
    answer: str
    route_hint: str | None = None
    results: list[AssistantResource] = Field(default_factory=list, max_length=20)
    metrics: list[AssistantCountMetric] = Field(default_factory=list, max_length=8)
    demand_result: dict[str, Any] | None = None
    compatibility_evidence: list[AssistantCompatibilityEvidence] = Field(
        default_factory=list, max_length=20
    )
    lineage: list[AssistantExecutionLineage] = Field(default_factory=list, max_length=5)
    tool_trace: list[AssistantToolTrace] = Field(default_factory=list, max_length=8)
    source_of_truth: Literal["medtrust_platform"] = "medtrust_platform"
    read_only: Literal[True] = True
