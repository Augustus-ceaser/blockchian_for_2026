from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from app.core.config import Settings
from app.modules.role_assistant import pydantic_runtime
from app.modules.role_assistant.query_service import (
    AssistantQueryContext,
    AssistantQueryResult,
)
from app.modules.role_assistant.schemas import AssistantResource, AssistantToolTrace


def _context(role: str = "data_requester") -> AssistantQueryContext:
    return cast(
        AssistantQueryContext,
        SimpleNamespace(role=role, space_id="space", actor=object(), session=object()),
    )


def _settings(**values: Any) -> Settings:
    defaults: dict[str, Any] = {
        "app_env": "test",
        "role_assistant_provider": "openai",
        "role_assistant_openai_enabled": True,
        "role_assistant_openai_api_key": "test-key",
        "role_assistant_openai_model": "gpt-4.1-mini",
        "role_assistant_openai_base_url": "https://api.openai.com/v1",
    }
    defaults.update(values)
    return Settings(**defaults)


def _tool_result(name: str) -> tuple[AssistantQueryResult, AssistantToolTrace]:
    source = f"medtrust.test.{name}"
    result = AssistantQueryResult(
        label=name,
        unit="项",
        source=source,
        total=1,
        items=[
            AssistantResource(
                key=f"{name}:1",
                kind="data" if "data" in name else "model",
                label=name,
                title=f"{name} result",
                subtitle="v1",
                path=f"/{name}/1",
            )
        ],
    )
    trace = AssistantToolTrace(
        tool=name,
        label=name,
        status="success",
        result_count=1,
        source=source,
    )
    return result, trace


def test_deepseek_model_uses_responses_provider_v1_without_reasoning() -> None:
    model = pydantic_runtime.build_role_assistant_model(
        _settings(
            role_assistant_provider="deepseek",
            role_assistant_openai_model="deepseek-v4-demo",
            role_assistant_openai_base_url="https://api.deepseek.com",
        )
    )

    assert model is not None
    assert model.base_url == "https://api.deepseek.com/v1/"
    assert model.settings["openai_reasoning_effort"] == "none"
    assert model.settings["parallel_tool_calls"] is False
    assert model.settings["openai_store"] is False


def test_openai_model_keeps_existing_v1_base_url() -> None:
    model = pydantic_runtime.build_role_assistant_model(
        _settings(role_assistant_openai_base_url="https://api.openai.com/v1")
    )

    assert model is not None
    assert model.base_url == "https://api.openai.com/v1/"
    assert pydantic_runtime.build_role_assistant_model(
        _settings(role_assistant_openai_enabled=False)
    ) is None


def test_tools_are_role_scoped_identity_free_and_strictly_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0
    calls: list[tuple[str, str]] = []

    async def fake_execute_tool(*, name, context, message):
        nonlocal active, max_active
        assert context.role == "data_requester"
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        calls.append((name, message))
        active -= 1
        return _tool_result(name)

    monkeypatch.setattr(pydantic_runtime, "execute_tool", fake_execute_tool)
    model = TestModel(
        call_tools=["search_data_products", "search_model_products"]
    )
    outcome = asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context(),
            message="我想找骨折数据和模型",
            settings=_settings(),
            model=model,
        )
    )

    assert outcome is not None
    assert [trace.tool for trace in outcome.traces] == [
        "search_data_products",
        "search_model_products",
    ]
    assert [result.label for result in outcome.results] == [
        "search_data_products",
        "search_model_products",
    ]
    assert calls == [
        ("search_data_products", "我想找骨折数据和模型"),
        ("search_model_products", "我想找骨折数据和模型"),
    ]
    assert max_active == 1

    definitions = model.last_model_request_parameters.function_tools
    assert "get_lifecycle_status" not in {tool.name for tool in definitions}
    for definition in definitions:
        assert definition.sequential is True
        properties = definition.parameters_json_schema["properties"]
        assert set(properties) == {"query", "key"}
        assert not (
            {"role", "space_id", "organization_id", "session"} & set(properties)
        )


def test_model_selected_key_must_come_from_a_previous_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    model_requests = 0

    def orchestrate(_messages, _info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        if model_requests == 1:
            return ModelResponse(parts=[ToolCallPart("search_model_products", {"query": "PathMNIST"})])
        if model_requests == 2:
            return ModelResponse(parts=[ToolCallPart("get_product_details", {"key": "search_model_products:1"})])
        return ModelResponse(parts=[TextPart("done")])

    async def fake_execute_tool(*, name, context, message):
        calls.append((name, message))
        return _tool_result(name)

    monkeypatch.setattr(pydantic_runtime, "execute_tool", fake_execute_tool)
    outcome = asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context(),
            message="查找 PathMNIST 模型并查看详情",
            settings=_settings(),
            model=FunctionModel(orchestrate),
        )
    )
    assert outcome is not None
    assert calls[0][1] == "查找 PathMNIST 模型并查看详情\n[工具查询：PathMNIST]"
    assert "[工具选择资源：search_model_products result（search_model_products:1）]" in calls[1][1]


def test_count_query_ignores_model_supplied_filter_and_counts_original_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    model_requests = 0

    def orchestrate(_messages, _info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        if model_requests == 1:
            return ModelResponse(
                parts=[ToolCallPart("search_data_products", {"query": "公共数据库"})]
            )
        return ModelResponse(parts=[TextPart("done")])

    async def fake_execute_tool(*, name, context, message):
        calls.append((name, message))
        return _tool_result(name)

    monkeypatch.setattr(pydantic_runtime, "execute_tool", fake_execute_tool)
    outcome = asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context("space_operator"),
            message="我现在空间里面有多少个公共数据库",
            settings=_settings(),
            model=FunctionModel(orchestrate),
            allowed_tools=frozenset({"search_data_products"}),
        )
    )

    assert outcome is not None
    assert calls == [
        ("search_data_products", "我现在空间里面有多少个公共数据库")
    ]


def test_function_model_can_select_one_tool_and_outputs_are_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_requests = 0
    observed_tool_names: set[str] = set()

    def orchestrate(_messages, info: AgentInfo) -> ModelResponse:
        nonlocal model_requests, observed_tool_names
        model_requests += 1
        observed_tool_names = {tool.name for tool in info.function_tools}
        if model_requests == 1:
            return ModelResponse(
                parts=[ToolCallPart("get_contract_status", {})]
            )
        return ModelResponse(parts=[TextPart("ignored model prose")])

    async def fake_execute_tool(*, name, context, message):
        assert context.role == "data_provider"
        assert message == "查找合同 CT-2026-01"
        return _tool_result(name)

    monkeypatch.setattr(pydantic_runtime, "execute_tool", fake_execute_tool)
    outcome = asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context("data_provider"),
            message="查找合同 CT-2026-01",
            settings=_settings(),
            model=FunctionModel(orchestrate),
        )
    )

    assert outcome is not None
    assert outcome.traces[0].tool == "get_contract_status"
    assert outcome.results[0].source == "medtrust.test.get_contract_status"
    assert "get_lifecycle_status" in observed_tool_names
    assert model_requests == 2


def test_no_tool_model_failure_and_budget_exhaustion_return_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_execute_tool(*, name, context, message):
        calls.append(name)
        return _tool_result(name)

    monkeypatch.setattr(pydantic_runtime, "execute_tool", fake_execute_tool)

    no_tool = FunctionModel(
        lambda _messages, _info: ModelResponse(parts=[TextPart("answer from memory")])
    )
    assert asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context(),
            message="现在有多少公共数据集",
            settings=_settings(),
            model=no_tool,
        )
    ) is None

    def fail_model(_messages, _info):
        raise RuntimeError("offline model failure")

    assert asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context(),
            message="现在有多少公共数据集",
            settings=_settings(),
            model=FunctionModel(fail_model),
        )
    ) is None

    assert asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context(),
            message="查找数据和模型",
            settings=_settings(),
            model=TestModel(
                call_tools=["search_data_products", "search_model_products"]
            ),
            tool_calls_limit=1,
        )
    ) is None
    assert calls == []


def test_sensitive_message_never_reaches_model() -> None:
    model_called = False

    def model_function(_messages, _info):
        nonlocal model_called
        model_called = True
        return ModelResponse(parts=[TextPart("unexpected")])

    outcome = asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context(),
            message="病历号 ABCD-1234，查一下申请",
            settings=_settings(),
            model=FunctionModel(model_function),
        )
    )

    assert outcome is None
    assert model_called is False


def test_request_tool_scope_is_smaller_than_the_role_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_tool(*, name, context, message):
        return _tool_result(name)

    monkeypatch.setattr(pydantic_runtime, "execute_tool", fake_execute_tool)
    model = TestModel(call_tools=["get_product_details"])
    outcome = asyncio.run(
        pydantic_runtime.run_pydantic_role_assistant(
            context=_context(),
            message="查看这个模型详情",
            settings=_settings(),
            model=model,
            allowed_tools=frozenset({"get_product_details"}),
        )
    )
    assert outcome is not None
    assert {tool.name for tool in model.last_model_request_parameters.function_tools} == {
        "get_product_details"
    }
