from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.modules.role_assistant import planner


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "role_assistant_provider": "openai",
        "role_assistant_openai_enabled": True,
        "role_assistant_openai_api_key": "test-secret-key",
        "role_assistant_openai_api_key_file": None,
        "role_assistant_openai_model": "gpt-4.1-mini",
        "role_assistant_openai_base_url": "https://api.openai.com/v1",
    }
    values.update(overrides)
    return Settings(**values)


def _function_response(
    *,
    name: str = "search_resources",
    arguments: dict[str, object] | None = None,
) -> bytes:
    payload = {
        "output": [
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps(
                    arguments
                    or {
                        "query": "骨折项目合约",
                        "resource_kinds": ["contract"],
                        "route_hint": "/contracts",
                    },
                    ensure_ascii=False,
                ),
            }
        ]
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class _FakeResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _: int = -1) -> bytes:
        return self.payload


def test_local_planner_is_deterministic_role_scoped_and_read_only() -> None:
    first = planner.build_local_plan(
        role="data_provider",
        message="帮我找骨折项目相关合约",
    )
    second = planner.build_local_plan(
        role="data_provider",
        message="帮我找骨折项目相关合约",
    )

    assert first == second
    assert first.model_dump(mode="json") == {
        "source": "local",
        "intent": "search_resources",
        "resource_kinds": ["contract"],
        "query": "帮我找骨折项目相关合约",
        "route_hint": "/contracts",
        "read_only": True,
    }
    assert "model" in planner.ROLE_ALLOWED_RESOURCE_KINDS["data_provider"]
    assert "data" in planner.ROLE_ALLOWED_RESOURCE_KINDS["model_provider"]
    assert "lifecycle" not in planner.ROLE_ALLOWED_RESOURCE_KINDS["data_requester"]


@pytest.mark.parametrize(
    "message",
    [
        "我现在空间里面有多少个公共数据库",
        "当前空间有几个公开数据库",
        "这里一共有多少条公共数据目录",
        "帮我看看公共资料库有多少条记录",
        "平台目前同步了多少条公共候选数据集",
        "空间内开放数据资源总数是多少",
    ],
)
def test_public_dataset_catalog_synonyms_use_data_tool_and_catalog_route(
    message: str,
) -> None:
    plan = planner.build_local_plan(role="space_operator", message=message)

    assert plan.intent == "search_resources"
    assert plan.resource_kinds == ["data"]
    assert plan.route_hint == "/external-catalog/datasets"


@pytest.mark.parametrize(
    ("message", "expected_route"),
    [
        ("我现在有多少数据资源", "/data-catalog"),
        ("平台里有多少模型资源", "/model-catalog"),
        ("当前有多少待审核数据产品", "/data-products"),
        ("查看待上架模型产品", "/model-products"),
        ("数据审核", "/data-products"),
        ("模型审核", "/model-products"),
        ("有没有要审核的数据", "/data-products"),
        ("有没有要审核的模型", "/model-products"),
        ("公共模型目录有多少条", "/external-catalog/models"),
    ],
)
def test_operator_catalog_navigation_distinguishes_resources_from_reviews(
    message: str,
    expected_route: str,
) -> None:
    plan = planner.build_local_plan(role="space_operator", message=message)

    assert plan.route_hint == expected_route


@pytest.mark.parametrize(
    ("message", "expected_kind"),
    [
        ("已发布数据产品有多少", "data"),
        ("已上架模型产品数量", "model"),
    ],
)
def test_publication_status_is_a_catalog_filter_not_a_lifecycle_tool(
    message: str,
    expected_kind: str,
) -> None:
    plan = planner.build_local_plan(role="space_operator", message=message)

    assert plan.resource_kinds == [expected_kind]


def test_database_infrastructure_phrase_is_not_routed_to_public_catalog() -> None:
    plan = planner.build_local_plan(
        role="space_operator",
        message="查看数据库连接和执行状态",
    )

    assert plan.route_hint != "/external-catalog/datasets"


@pytest.mark.parametrize(
    "role",
    ["space_operator", "data_provider", "model_provider", "data_requester"],
)
def test_third_party_data_service_queries_use_the_service_resource(role: str) -> None:
    plan = planner.build_local_plan(
        role=role,
        message="帮我找可以申请的第三方数据服务，并说明当前可用状态",
    )

    assert plan.intent == "search_resources"
    assert plan.resource_kinds == ["service"]
    assert plan.route_hint == "/data-catalog"
    assert plan.read_only is True


def test_requester_research_task_is_planned_for_existing_demand_analyzer() -> None:
    plan = planner.build_local_plan(
        role="data_requester",
        message="我想构建一个骨折患者住院风险预测模型",
    )

    assert plan.intent == "analyze_research_demand"
    assert plan.resource_kinds == ["data", "model", "application", "workflow"]
    assert plan.route_hint == "/applications/new"
    assert plan.read_only is True

    search = planner.build_local_plan(
        role="data_requester",
        message="查看我的风险预测申请进度",
    )
    assert search.intent == "search_resources"
    assert search.resource_kinds == ["application"]
    assert search.route_hint == "/applications"

    short = planner.build_local_plan(
        role="data_requester",
        message="我想做模型",
    )
    assert short.intent == "search_resources"
    assert short.resource_kinds == ["model"]


def test_authorization_status_lookup_does_not_expand_into_catalog_searches() -> None:
    plan = planner.build_local_plan(
        role="data_requester",
        message="我有哪些数据和模型授权申请，它们现在到哪一步了？",
    )

    assert plan.intent == "search_resources"
    assert plan.resource_kinds == ["application"]
    assert plan.route_hint == "/applications"


def test_new_model_license_request_is_not_misclassified_as_status_lookup() -> None:
    plan = planner.build_local_plan(
        role="data_requester",
        message="我想申请一个模型使用许可",
    )

    assert plan.resource_kinds != ["application"]


@pytest.mark.parametrize(
    "message",
    [
        "我想用成人多部位骨骼X光判断是否存在骨折，模型要小并输出准确率和混淆矩阵",
        "我想对膝关节X光进行KL 0到4级骨关节炎分级，并比较基线模型和注意力模型",
    ],
)
def test_requester_task_language_without_build_model_phrase_uses_demand_analyzer(
    message: str,
) -> None:
    plan = planner.build_local_plan(role="data_requester", message=message)

    assert plan.intent == "analyze_research_demand"
    assert plan.resource_kinds == ["data", "model", "application", "workflow"]
    assert plan.route_hint == "/applications/new"


def test_openai_responses_call_uses_required_strict_tools_and_store_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, *, timeout: float):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(_function_response())

    monkeypatch.setattr(planner, "urlopen", fake_urlopen)
    settings = _settings(role_assistant_openai_timeout_seconds=7.5)

    result = asyncio.run(
        planner.plan_role_assistant(
            role="data_provider",
            message="帮我找骨折项目相关合约",
            settings=settings,
        )
    )

    request = captured["request"]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert request.get_header("Authorization") == "Bearer test-secret-key"
    assert captured["timeout"] == 7.5
    assert body["store"] is False
    assert body["tool_choice"] == {"type": "function", "name": "search_resources"}
    assert body["parallel_tool_calls"] is False
    assert body["model"] == "gpt-4.1-mini"
    assert "reasoning" not in body
    assert all(tool["strict"] is True for tool in body["tools"])
    assert all(
        tool["parameters"]["additionalProperties"] is False
        for tool in body["tools"]
    )
    assert result.source == "openai"
    assert result.query == "骨折项目合约"
    assert result.resource_kinds == ["contract"]


def test_deepseek_responses_provider_uses_configured_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, *, timeout: float):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(_function_response())

    monkeypatch.setattr(planner, "urlopen", fake_urlopen)
    result = asyncio.run(
        planner.plan_role_assistant(
            role="data_provider",
            message="帮我找骨折项目相关合约",
            settings=_settings(
                role_assistant_provider="deepseek",
                role_assistant_openai_model="deepseek-v4-flash",
                role_assistant_openai_base_url="https://api.deepseek.com",
            ),
        )
    )

    assert captured["request"].full_url == "https://api.deepseek.com/responses"
    body = json.loads(captured["request"].data.decode("utf-8"))
    assert body["reasoning"] == {"effort": "none"}
    assert body["temperature"] == 0
    assert body["tool_choice"] == {"type": "function", "name": "search_resources"}
    assert result.source == "deepseek"


def test_sensitive_identifiers_never_leave_the_local_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        planner,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("sensitive text must not call a remote API"),
    )
    result = asyncio.run(
        planner.plan_role_assistant(
            role="data_requester",
            message="病历号: ABCD-1234，帮我查看申请",
            settings=_settings(),
        )
    )

    assert result.source == "local"
    assert planner.contains_sensitive_identifier("手机号 13800138000") is True
    assert planner.contains_sensitive_identifier("邮箱: patient@example.com") is True
    assert planner.contains_sensitive_identifier("11010519491231002X") is True
    assert planner.contains_sensitive_identifier("我想研究骨折住院风险") is False


def test_openai_search_normalization_preserves_identifiers_and_demand_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(
        planner,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(
            _function_response(
                arguments={
                    "query": "骨折合约",
                    "resource_kinds": ["contract"],
                    "route_hint": "/contracts",
                }
            )
        ),
    )
    original = "查找合约 CTR-2026-001"
    dropped_identifier = asyncio.run(
        planner.plan_role_assistant(
            role="data_provider",
            message=original,
            settings=settings,
        )
    )
    assert dropped_identifier == planner.build_local_plan(
        role="data_provider",
        message=original,
    )

    demand = "我想构建一个骨折患者住院风险预测模型"
    monkeypatch.setattr(
        planner,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(
            _function_response(
                name="analyze_research_demand",
                arguments={
                    "query": "fracture inpatient risk prediction",
                    "resource_kinds": ["data", "model"],
                    "route_hint": "/applications/new",
                },
            )
        ),
    )
    analyzed = asyncio.run(
        planner.plan_role_assistant(
            role="data_requester",
            message=demand,
            settings=settings,
        )
    )
    assert analyzed.source == "openai"
    assert analyzed.intent == "analyze_research_demand"
    assert analyzed.query == demand


@pytest.mark.parametrize(
    ("message", "model_route", "expected_route"),
    [
        ("我现在有多少数据资源", "/data-products", "/data-catalog"),
        ("当前有多少待审核数据产品", "/data-catalog", "/data-products"),
        ("平台里有多少模型资源", "/model-products", "/model-catalog"),
        ("查看待上架模型产品", "/model-catalog", "/model-products"),
    ],
)
def test_remote_planner_cannot_replace_catalog_semantics_with_review_route(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    model_route: str,
    expected_route: str,
) -> None:
    kind = "data" if "数据" in message else "model"
    monkeypatch.setattr(
        planner,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(
            _function_response(
                arguments={
                    "query": message,
                    "resource_kinds": [kind],
                    "route_hint": model_route,
                }
            )
        ),
    )

    result = asyncio.run(
        planner.plan_role_assistant(
            role="space_operator",
            message=message,
            settings=_settings(),
        )
    )

    assert result.source == "openai"
    assert result.route_hint == expected_route


def test_unavailable_or_invalid_openai_output_falls_back_without_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    expected = planner.build_local_plan(
        role="data_requester",
        message="查看我的执行进度",
    )

    def unavailable(*_: object, **__: object):
        raise URLError("offline")

    monkeypatch.setattr(planner, "urlopen", unavailable)
    unavailable_result = asyncio.run(
        planner.plan_role_assistant(
            role="data_requester",
            message="查看我的执行进度",
            settings=settings,
        )
    )
    assert unavailable_result == expected

    monkeypatch.setattr(
        planner,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(
            _function_response(
                arguments={
                    "query": "查看我的执行进度",
                    "resource_kinds": ["lifecycle"],
                    "route_hint": "/lifecycle",
                }
            )
        ),
    )
    invalid_result = asyncio.run(
        planner.plan_role_assistant(
            role="data_requester",
            message="查看我的执行进度",
            settings=settings,
        )
    )
    assert invalid_result == expected

    monkeypatch.setattr(
        planner,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("missing API key must not make a request"),
    )
    no_key_result = asyncio.run(
        planner.plan_role_assistant(
            role="data_requester",
            message="查看我的执行进度",
            settings=_settings(role_assistant_openai_api_key=""),
        )
    )
    assert no_key_result == expected


def test_openai_settings_support_key_file_and_validate_transport(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "openai-key"
    key_file.write_text("file-secret\n", encoding="utf-8")
    settings = Settings(role_assistant_openai_api_key_file=key_file)
    assert settings.role_assistant_openai_api_key.get_secret_value() == "file-secret"
    assert "file-secret" not in repr(settings)

    with pytest.raises(ValidationError, match=r"absolute HTTP\(S\) URL"):
        Settings(role_assistant_openai_base_url="not-a-url")
    with pytest.raises(ValidationError, match="HTTPS or loopback HTTP"):
        Settings(role_assistant_openai_base_url="http://example.com/v1")
    with pytest.raises(ValidationError, match="role_assistant_openai_model"):
        Settings(role_assistant_openai_model="bad model name")
    with pytest.raises(ValidationError):
        Settings(role_assistant_openai_timeout_seconds=0.5)
    with pytest.raises(ValidationError, match="supported deepseek-v4 model"):
        Settings(
            role_assistant_provider="deepseek",
            role_assistant_openai_enabled=True,
            role_assistant_openai_model="gpt-4.1-mini",
            role_assistant_openai_base_url="https://api.deepseek.com",
        )
    with pytest.raises(ValidationError, match="requires https://api.deepseek.com"):
        Settings(
            role_assistant_provider="deepseek",
            role_assistant_openai_enabled=True,
            role_assistant_openai_model="deepseek-v4-flash",
            role_assistant_openai_base_url="https://api.openai.com/v1",
        )


def test_plan_route_is_registered_for_the_four_public_roles() -> None:
    with TestClient(
        create_app(Settings(app_env="test", role_assistant_openai_enabled=False))
    ) as client:
        for role in (
            "space_operator",
            "data_provider",
            "model_provider",
            "data_requester",
        ):
            response = client.post(
                "/api/v1/role-assistant/plan",
                headers={"X-Demo-Identity": role},
                json={"message": "查看我的执行进度"},
            )
            assert response.status_code == 200
            assert response.json()["source"] == "local"
            assert response.json()["read_only"] is True

        assert client.post(
            "/api/v1/role-assistant/plan",
            headers={"X-Demo-Identity": "catalog_curator"},
            json={"message": "查看我的执行进度"},
        ).status_code == 403
        assert client.post(
            "/api/v1/role-assistant/plan",
            json={"message": "查看我的执行进度"},
        ).status_code == 422
