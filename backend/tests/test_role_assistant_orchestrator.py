from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.core.config import Settings
from app.modules.role_assistant import orchestrator
from app.modules.role_assistant.planner import RoleAssistantPlan
from app.modules.role_assistant.query_service import AssistantQueryResult, _terms
from app.modules.role_assistant.schemas import (
    AssistantCompatibilityEvidence,
    AssistantExecutionLineage,
    AssistantLineageNode,
    AssistantToolTrace,
)


def _plan(message: str) -> RoleAssistantPlan:
    return RoleAssistantPlan(
        source="local",
        intent="search_resources",
        resource_kinds=["application"],
        query=message,
        route_hint="/applications",
    )


def test_generic_authorization_status_question_does_not_become_a_false_search_filter() -> None:
    assert _terms("我有哪些数据和模型授权申请，它们现在到哪一步了？") == []
    assert _terms("查找 SAR-0A4D838E86C8 授权申请") == ["sar-0a4d838e86c8"]


def test_request_status_response_keeps_only_requests_and_routes_to_my_applications(monkeypatch) -> None:
    message = "我有哪些数据和模型授权申请，它们现在到哪一步了？"

    async def fake_execute(**_kwargs):
        result = AssistantQueryResult(
            label="授权申请",
            unit="项",
            source="medtrust.service_requests",
            total=2,
            items=[],
        )
        return result, AssistantToolTrace(
            tool="get_request_status",
            label="服务申请",
            status="success",
            result_count=2,
            source=result.source,
        )

    monkeypatch.setattr(orchestrator, "execute_tool", fake_execute)

    response = asyncio.run(
        orchestrator._execute_role_assistant_query(
            context=SimpleNamespace(role="data_requester", session=object()),
            role="data_requester",
            message=message,
            settings=Settings(
                app_env="test",
                role_assistant_openai_enabled=False,
                role_assistant_runtime="legacy",
            ),
        )
    )

    assert response.answer == "已读取当前账号可见的 2 项授权申请；可在“我的申请”查看详情和下一步。"
    assert response.route_hint == "/applications"
    assert [trace.tool for trace in response.tool_trace] == ["get_request_status"]


def test_public_dataset_count_answer_uses_catalog_record_semantics() -> None:
    result = AssistantQueryResult(
        label="公共候选数据集",
        unit="条",
        source="medtrust.external_dataset_catalog",
        total=982,
        items=[],
    )

    assert orchestrator._format_count_answer([result]) == (
        "当前空间共有 982 条已同步的公共候选数据集目录记录；"
        "这些是目录元数据，已发布数据产品需另行统计。"
    )


def test_not_assessed_pair_is_explained_without_claiming_compatibility(monkeypatch) -> None:
    message = "查看 PathMNIST 数据和 ResNet-18 模型的适配证据"

    async def fake_context(**_kwargs):
        return SimpleNamespace(role="data_requester")

    async def fake_plan(**_kwargs):
        return _plan(message)

    async def fake_execute(**_kwargs):
        result = AssistantQueryResult(
            label="数据—模型适配证据",
            unit="组",
            source="medtrust.dataset_model_evidence",
            total=0,
            items=[],
            compatibility_evidence=[
                AssistantCompatibilityEvidence(
                    status="not_assessed",
                    status_label="尚未评估",
                    evidence_note="本次查询未触发检查。",
                )
            ],
        )
        return result, AssistantToolTrace(
            tool="check_data_model_compatibility",
            label="数据—模型适配证据",
            status="empty",
            result_count=0,
            source=result.source,
        )

    monkeypatch.setattr(orchestrator, "_query_context", fake_context)
    monkeypatch.setattr(orchestrator, "plan_role_assistant", fake_plan)
    monkeypatch.setattr(
        orchestrator,
        "tools_for_plan",
        lambda **_kwargs: ["check_data_model_compatibility"],
    )
    monkeypatch.setattr(orchestrator, "execute_tool", fake_execute)

    response = asyncio.run(
        orchestrator.query_role_assistant(
            role="data_requester",
            message=message,
            settings=Settings(app_env="test", role_assistant_openai_enabled=False),
            session=object(),
        )
    )

    assert response.compatibility_evidence[0].status == "not_assessed"
    assert "不代表兼容或不兼容" in response.answer
    assert "未找到匹配记录" not in response.answer


def test_lineage_payload_is_answered_even_when_generic_results_are_empty(monkeypatch) -> None:
    message = "查看这个申请从申请到结果的执行证据链"

    async def fake_context(**_kwargs):
        return SimpleNamespace(role="data_requester")

    async def fake_plan(**_kwargs):
        return _plan(message)

    async def fake_execute(**_kwargs):
        lineage = AssistantExecutionLineage(
            application_id="application-1",
            application_number="APP-2026-001",
            scenario_name="结直肠病理分类验证",
            status="active",
            completed_nodes=1,
            total_nodes=2,
            path="/applications/application-1",
            nodes=[
                AssistantLineageNode(
                    key="application",
                    label="计算申请",
                    number="APP-2026-001",
                    status="approved",
                    complete=True,
                    state="completed",
                ),
                AssistantLineageNode(
                    key="contract",
                    label="数字合约",
                    status="not_created",
                    complete=False,
                    state="pending",
                ),
            ],
        )
        result = AssistantQueryResult(
            label="执行血缘",
            unit="条",
            source="medtrust.execution_lineage_projection",
            total=1,
            items=[],
            lineage=[lineage],
        )
        return result, AssistantToolTrace(
            tool="get_execution_lineage",
            label="执行血缘",
            status="success",
            result_count=1,
            source=result.source,
        )

    monkeypatch.setattr(orchestrator, "_query_context", fake_context)
    monkeypatch.setattr(orchestrator, "plan_role_assistant", fake_plan)
    monkeypatch.setattr(
        orchestrator,
        "tools_for_plan",
        lambda **_kwargs: ["get_execution_lineage"],
    )
    monkeypatch.setattr(orchestrator, "execute_tool", fake_execute)

    response = asyncio.run(
        orchestrator.query_role_assistant(
            role="data_requester",
            message=message,
            settings=Settings(app_env="test", role_assistant_openai_enabled=False),
            session=object(),
        )
    )

    assert response.results == []
    assert response.lineage[0].application_number == "APP-2026-001"
    assert "已定位 1 条" in response.answer
    assert "未找到匹配记录" not in response.answer
