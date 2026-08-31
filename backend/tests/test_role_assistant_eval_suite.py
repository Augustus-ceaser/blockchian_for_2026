from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from app.modules.role_assistant.planner import (
    PublicRole,
    build_local_plan,
    contains_sensitive_identifier,
)
from app.modules.role_assistant.registry import TOOL_REGISTRY, tools_for_plan


CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "role_assistant_eval_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_fixed_agent_evaluation_case(case: dict[str, object]) -> None:
    role = cast(PublicRole, case["role"])
    message = str(case["message"])
    sensitive = contains_sensitive_identifier(message)
    assert sensitive is case["sensitive"]
    if sensitive:
        # The API rejects this before any planner or tool is invoked.
        assert case["expected_tools"] == []
        return
    plan = build_local_plan(role=role, message=message)
    tools = (
        []
        if plan.intent == "analyze_research_demand"
        else tools_for_plan(role=role, plan=plan, message=message)
    )
    assert plan.intent == case["expected_intent"]
    assert tools == case["expected_tools"]
    assert all(
        TOOL_REGISTRY[name].risk_class == "read"
        and TOOL_REGISTRY[name].exposure == "agent"
        for name in tools
    )
