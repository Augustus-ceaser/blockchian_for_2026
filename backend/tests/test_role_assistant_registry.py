from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.routes import role_assistant as role_assistant_route
from app.core.config import Settings
from app.db.session import get_db_session
from app.main import create_app
from app.modules.role_assistant.planner import build_local_plan
from app.modules.role_assistant.query_service import (
    AssistantQueryContext,
    AssistantQueryResult,
    MAX_SEARCH_TERMS,
    _escape_like,
    _explicit_product_references,
    _pair_is_referenced,
    _product_search_scope,
    _product_path,
    _terms,
    is_count_question,
)
from app.modules.role_assistant.registry import (
    TOOL_REGISTRY,
    execute_tool,
    tools_for_plan,
)
from app.modules.role_assistant.schemas import AssistantResource, RoleAssistantQueryResponse


def test_registry_exposes_cross_catalog_search_but_keeps_lifecycle_role_scoped() -> None:
    hospital_model_plan = build_local_plan(
        role="data_provider",
        message="查找医院可用的模型",
    )
    model_data_plan = build_local_plan(
        role="model_provider",
        message="查找适配的数据产品",
    )

    assert tools_for_plan(
        role="data_provider", plan=hospital_model_plan, message="查找医院可用的模型"
    ) == ["search_model_products"]
    assert tools_for_plan(
        role="model_provider", plan=model_data_plan, message="查找适配的数据产品"
    ) == ["search_data_products"]
    assert tools_for_plan(
        role="data_requester",
        plan=build_local_plan(role="data_requester", message="查看我的兼容性状态"),
        message="查看我的兼容性状态",
    ) == ["read_compatibility_status"]
    assert "data_requester" not in TOOL_REGISTRY["get_lifecycle_status"].allowed_roles
    service_plan = build_local_plan(
        role="data_requester",
        message="查找第三方数据服务的健康状态和申请条件",
    )
    assert tools_for_plan(
        role="data_requester",
        plan=service_plan,
        message="查找第三方数据服务的健康状态和申请条件",
    ) == ["search_data_services"]
    assert TOOL_REGISTRY["search_data_services"].allowed_roles == {
        "space_operator",
        "data_provider",
        "model_provider",
        "data_requester",
    }
    assert TOOL_REGISTRY["search_data_services"].read_only is True
    assert "invoke_data_service" not in TOOL_REGISTRY
    evidence_plan = build_local_plan(
        role="data_requester",
        message="查看 PathMNIST 数据和 ResNet-18 模型的适配证据",
    )
    assert tools_for_plan(
        role="data_requester",
        plan=evidence_plan,
        message="查看 PathMNIST 数据和 ResNet-18 模型的适配证据",
    ) == ["check_data_model_compatibility"]
    assert tools_for_plan(
        role="data_requester",
        plan=build_local_plan(role="data_requester", message="查看我的申请兼容性状态"),
        message="查看我的申请兼容性状态",
    ) == ["read_compatibility_status"]
    assert tools_for_plan(
        role="data_provider",
        plan=build_local_plan(role="data_provider", message="查看申请到结果的执行证据链"),
        message="查看申请到结果的执行证据链",
    ) == ["get_execution_lineage"]
    assert tools_for_plan(
        role="model_provider",
        plan=build_local_plan(role="model_provider", message="查看模型产品版本详情"),
        message="查看模型产品版本详情",
    ) == ["get_product_details"]
    for tool_name in (
        "get_product_details",
        "check_data_model_compatibility",
        "get_execution_lineage",
    ):
        assert TOOL_REGISTRY[tool_name].allowed_roles == {
            "space_operator",
            "data_provider",
            "model_provider",
            "data_requester",
        }


def test_dispatcher_rejects_unregistered_role_before_calling_handler() -> None:
    context = SimpleNamespace(role="data_requester")
    with pytest.raises(PermissionError):
        asyncio.run(
            execute_tool(
                name="get_lifecycle_status",
                context=context,
                message="查看生命周期事项",
            )
        )


def test_registry_exposes_only_read_tools_and_rolls_back_direct_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert all(
        definition.risk_class == "read"
        and definition.read_only
        and definition.idempotent
        and not definition.requires_confirmation
        and definition.exposure == "agent"
        for definition in TOOL_REGISTRY.values()
    )
    asyncio.run(_assert_direct_sql_is_rolled_back(monkeypatch))


async def _assert_direct_sql_is_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE assistant_write_marker (id INTEGER)"))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def unsafe_handler(
        context: AssistantQueryContext, _message: str
    ) -> AssistantQueryResult:
        await context.session.execute(
            text("INSERT INTO assistant_write_marker (id) VALUES (1)")
        )
        return AssistantQueryResult(
            label="unsafe",
            unit="项",
            source="medtrust.test",
            total=1,
            items=[],
        )

    original = TOOL_REGISTRY["search_data_products"]
    monkeypatch.setitem(
        TOOL_REGISTRY,
        "search_data_products",
        replace(original, handler=unsafe_handler),
    )
    async with factory() as session:
        context = SimpleNamespace(role="data_requester", session=session)
        result, trace = await execute_tool(
            name="search_data_products",
            context=context,
            message="test",
        )
        assert result is not None
        assert trace.status == "success"
        count = await session.scalar(text("SELECT count(*) FROM assistant_write_marker"))
        assert count == 0
    await engine.dispose()


def test_filtered_count_keeps_medical_terms_and_search_is_bounded() -> None:
    assert is_count_question("有多少公共鼻咽癌病理模型") is True
    terms = _terms("有多少公共鼻咽癌病理模型")
    assert "nasopharyngeal" in terms
    assert "histopathology" in terms
    assert _terms("现在有多少公共数据集") == []
    assert _terms("我现在空间里面有多少个公共数据库") == []
    assert _terms("帮我看看公共资料库有多少条记录") == []
    assert _terms("平台目前同步了多少条公共候选数据集") == []
    assert _terms("空间内开放数据资源总数是多少") == []
    assert _terms("平台现在有多少公共候选数据集和公共候选模型") == []
    assert _terms("查看医院参与申请的执行血缘") == []
    assert _terms("查找第三方数据服务的当前状态和申请条件") == []
    assert "鼻咽癌" in _terms("查找鼻咽癌病理第三方数据服务")
    assert len(_terms(" ".join(f"term{index}" for index in range(50)))) == MAX_SEARCH_TERMS
    assert _escape_like(r"100%_match\\") == r"100\%\_match\\\\"


@pytest.mark.parametrize(
    ("role", "provider_role", "query", "expected_scope"),
    [
        ("space_operator", "data_provider", "我现在有多少数据资源", "published"),
        ("space_operator", "model_provider", "平台里有多少模型资源", "published"),
        ("space_operator", "data_provider", "当前有多少待审核数据产品", "pending"),
        ("space_operator", "model_provider", "模型审核", "pending"),
        ("data_provider", "data_provider", "查看我的数据产品", "owned"),
        ("model_provider", "model_provider", "查看我的模型产品", "owned"),
        ("data_requester", "data_provider", "查看数据产品", "published"),
    ],
)
def test_product_search_scope_matches_the_navigation_destination(
    role: str,
    provider_role: str,
    query: str,
    expected_scope: str,
) -> None:
    assert _product_search_scope(
        role=role,
        provider_role=provider_role,
        query=query,
    ) == expected_scope


def test_product_paths_never_open_another_provider_management_page() -> None:
    actor_id = uuid4()
    other_id = uuid4()
    version_id = uuid4()
    assert _product_path(
        "data_provider",
        "data",
        version_id,
        provider_organization_id=other_id,
        actor_organization_id=actor_id,
    ) == "/data-catalog"
    assert _product_path(
        "model_provider",
        "model",
        version_id,
        provider_organization_id=other_id,
        actor_organization_id=actor_id,
    ) == "/model-catalog"
    assert _product_path(
        "data_requester",
        "data",
        version_id,
        provider_organization_id=other_id,
        actor_organization_id=actor_id,
    ) == f"/data-products/{version_id}"


def test_product_details_prefer_the_explicit_display_name() -> None:
    exact = AssistantResource(
        key="data:exact",
        kind="data",
        label="数据产品详情",
        title="结直肠组织病理分类数据产品（公开验证）",
        subtitle="v1.0",
        path="/data-products/exact",
    )
    unrelated = AssistantResource(
        key="data:other",
        kind="data",
        label="数据产品详情",
        title="ACDC-LungHP",
        subtitle="v1.0",
        path="/data-products/other",
    )
    assert _explicit_product_references(
        [unrelated, exact],
        "查看结直肠组织病理分类数据产品（公开验证）的版本、质量、许可和来源详情",
    ) == [exact]
    assert _explicit_product_references(
        [unrelated, exact],
        "查看病理数据产品详情",
    ) == []


def test_version_pair_reference_requires_both_data_and_model_identity() -> None:
    row = {
        "data_name": "结直肠组织病理分类数据产品（公开验证）",
        "data_code": "PATHMNIST-COLORECTAL-PUBLIC-V1",
        "model_name": "PathMNIST ResNet-18病理分类模型",
        "model_code": "PATHMNIST-RESNET18-V1",
    }
    assert _pair_is_referenced(
        "查看结直肠组织病理分类数据产品（公开验证）与 PathMNIST ResNet-18病理分类模型的适配证据",
        **row,
    ) is True
    assert _pair_is_referenced(
        "查看 PathMNIST 数据与 ResNet18 模型的适配证据",
        **row,
    ) is True
    assert _pair_is_referenced(
        "查看结直肠组织病理分类数据产品（公开验证）与 ZZZ-UNKNOWN-MODEL 的适配证据",
        **row,
    ) is False


def test_query_route_is_typed_and_rejects_sensitive_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_session():
        yield object()

    async def fake_query_role_assistant(**kwargs: object) -> RoleAssistantQueryResponse:
        return RoleAssistantQueryResponse(
            plan_source="local",
            intent="search_resources",
            answer="已实时读取当前账号可见资源：公共候选数据集 12 条。",
            tool_trace=[
                {
                    "tool": "search_data_products",
                    "label": "数据目录",
                    "status": "success",
                    "result_count": 12,
                    "source": "medtrust.external_dataset_catalog",
                }
            ],
        )

    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db_session] = fake_session
    monkeypatch.setattr(
        role_assistant_route,
        "query_role_assistant",
        fake_query_role_assistant,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/role-assistant/query",
            headers={"X-Demo-Identity": "data_requester"},
            json={"message": "现在有多少公共数据集"},
        )
        assert response.status_code == 200
        assert response.json()["source_of_truth"] == "medtrust_platform"
        assert response.json()["tool_trace"][0]["result_count"] == 12

        blocked = client.post(
            "/api/v1/role-assistant/query",
            headers={"X-Demo-Identity": "data_requester"},
            json={"message": "病历号 ABCD-1234，查一下申请"},
        )
        assert blocked.status_code == 422
        assert "ABCD-1234" not in blocked.text
