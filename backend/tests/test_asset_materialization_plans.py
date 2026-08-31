from types import SimpleNamespace

from app.main import create_app
from app.modules.asset_materialization.services import (
    MAX_TOTAL_BYTES,
    MIN_REMAINING_BYTES,
    _approval_blockers,
)


def _plan(**overrides):
    values = {
        "blocking_reasons": [],
        "license_snapshot": {"result": "pass"},
        "access_snapshot": {
            "result": "pass",
            "private_token_required": False,
            "gated": False,
        },
        "security_preflight": {
            "result": "pass",
            "redirects_bounded": True,
            "dns_rebinding_protected": True,
            "archive_traversal_blocked": True,
            "symlinks_forbidden": True,
            "executables_forbidden": True,
            "dynamic_import_forbidden": True,
            "native_extensions_forbidden": True,
            "dependencies_pinned": True,
            "integrity_metadata_complete": True,
            "pickle_allowed": False,
        },
        "model_plan": {
            "revision": "immutable-commit",
            "runtime_network": False,
            "trust_remote_code": False,
        },
        "transformation_plan": {"complete": True, "deterministic": True},
        "hardware_requirements": {
            "available": True,
            "disk_free_bytes": MIN_REMAINING_BYTES + 10_000,
        },
        "asset_file_allowlist": [{
            "path": "weights.safetensors",
            "bytes": 1,
            "sha256": "sha256:" + "a" * 64,
        }],
        "network_allowlist": ["https://huggingface.co/example/weights.safetensors"],
        "total_estimated_bytes": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_approval_gate_accepts_only_complete_static_plan():
    assert _approval_blockers(_plan()) == []


def test_gated_private_token_and_unknown_revision_block_approval():
    plan = _plan(
        access_snapshot={
            "result": "blocked",
            "private_token_required": True,
            "gated": True,
        },
        model_plan={"revision": "", "runtime_network": True, "trust_remote_code": True},
    )
    blockers = _approval_blockers(plan)
    assert "access evidence is not approved" in blockers
    assert "private access tokens are prohibited" in blockers
    assert "gated access is prohibited" in blockers
    assert "model revision is not immutable" in blockers
    assert "runtime network must be disabled" in blockers
    assert "remote code must be disabled" in blockers


def test_budget_and_remaining_disk_are_fail_closed():
    plan = _plan(
        total_estimated_bytes=MAX_TOTAL_BYTES + 1,
        hardware_requirements={
            "available": True,
            "disk_free_bytes": MAX_TOTAL_BYTES + MIN_REMAINING_BYTES,
        },
    )
    blockers = _approval_blockers(plan)
    assert "planned bytes exceed 50 GiB" in blockers
    assert "remaining disk would be below 100 GiB" in blockers


def test_openapi_exposes_plan_commands_but_no_materialize_or_execute_command():
    paths = create_app().openapi()["paths"]
    expected = {
        "/api/v1/materialization-plans",
        "/api/v1/materialization-plans/{plan_id}",
        "/api/v1/dataset-model-relations/{relation_id}/materialization-plans",
        "/api/v1/materialization-plans/{plan_id}/submit",
        "/api/v1/materialization-plans/{plan_id}/approve",
        "/api/v1/materialization-plans/{plan_id}/reject",
        "/api/v1/materialization-plans/{plan_id}/cancel",
    }
    assert expected <= set(paths)
    assert not any(
        suffix in path
        for path in paths
        for suffix in ("/download", "/materialize", "/execute")
        if "materialization" in path
    )
