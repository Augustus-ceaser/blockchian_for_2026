from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.policy_control.models import (
    ControlReadinessSnapshot,
    ExecutionOrder,
    ExecutionOrderConsumptionReceipt,
    PolicyBundleVersion,
)


ROOT = Path(__file__).resolve().parents[2]


def rendered_constraints(table) -> str:
    return " ".join(
        str(item.sqltext)
        for item in table.constraints
        if hasattr(item, "sqltext")
    )


def test_fixed_authorization_modes_are_database_constrained() -> None:
    readiness = rendered_constraints(ControlReadinessSnapshot.__table__)
    policy = rendered_constraints(PolicyBundleVersion.__table__)
    order = rendered_constraints(ExecutionOrder.__table__)
    for value in (
        "FIXED_REFERENCE_EXECUTION",
        "EXECUTE_FIXED_REFERENCE_TASK",
        "PATHMNIST_REFERENCE_V1",
    ):
        assert value in readiness
        assert value in order
    assert "FIXED_REFERENCE_ONLY" in policy
    assert "max_execution_count=1" in policy
    assert "consumed_count <= max_execution_count" in order
    consumption = ExecutionOrderConsumptionReceipt.__table__
    assert consumption.c.execution_order_id.unique is True
    assert consumption.c.authorization_snapshot_id.unique is True
    assert consumption.c.task_manifest_id.unique is True
    assert consumption.c.runtime_session_id.unique is True
    assert consumption.c.reference_execution_id.unique is True


def test_readiness_sources_are_server_derived_and_frontend_cannot_send_digests() -> None:
    route = (
        ROOT / "backend/app/api/routes/policy_control.py"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "backend/app/modules/policy_control/services.py"
    ).read_text(encoding="utf-8")
    assert "source_executor_status_event_digest" not in (
        route.split("class CompileRequest", 1)[1]
        .split("class OrderRequest", 1)[0]
    )
    assert "source_asset_metadata_digest" not in (
        route.split("class CompileRequest", 1)[1]
        .split("class OrderRequest", 1)[0]
    )
    assert "get_verified_executor_readiness_source" in service
    assert "FIXED_TASK_DEFINITION" in service
    assert "MEDTRUST_FIXED_REFERENCE_AUTHORIZATION_TTL_SECONDS" in service
    assert (
        "MEDTRUST_FIXED_REFERENCE_AUTHORIZATION_SAFETY_MARGIN_SECONDS"
        in service
    )
    assert "fixed_reference_minimum_validity_seconds" in service
    fixed_compiler = service.split(
        "async def compile_fixed_execution_policy", 1
    )[1].split("async def compile_policy", 1)[0]
    assert (
        '"model_reference_digest": readiness.source_model_reference_digest'
        in fixed_compiler
    )


def test_fixed_receipt_and_decision_are_strict_and_central_override_is_false() -> None:
    service = (
        ROOT / "backend/app/modules/policy_control/services.py"
    ).read_text(encoding="utf-8")
    assert "phase5.13E-2C-R1/connector-receipt/v1" in service
    assert "phase5.13E-2C-R1/connector-decision/v1" in service
    assert 'set(payload) != expected_fields' in service
    assert '"central_override": False' in service
    assert '"execution_started": False' in service
    assert '"hard_isolation": False' in service


def test_openapi_exposes_readiness_without_formal_execution_endpoint() -> None:
    with TestClient(create_app(Settings(app_env="test"))) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/policy-control/readiness" in paths
    assert "/api/v1/policy-control/readiness/{readiness_id}" in paths
    policy_paths = "\n".join(path for path in paths if "/policy-control/" in path)
    for forbidden in ("/execute", "/start", "/run", "/artifacts"):
        assert forbidden not in policy_paths


def test_phase_migration_freezes_readiness_and_signed_fixed_orders() -> None:
    migration = (
        ROOT
        / "backend/alembic/versions/"
        "20260729_0056_phase513e2cr1_policy_order_readiness.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "20260729_0056"' in migration
    assert 'down_revision = "20260729_0055"' in migration
    assert "guard_execution_readiness_snapshot_immutable" in migration
    assert "guard_fixed_execution_order_signed" in migration
    assert "source_executor_status_event_id" in migration
    assert "source_asset_version_id" in migration

    consumption_migration = (
        ROOT
        / "backend/alembic/versions/"
        "20260729_0057_phase513e2cr1_execution_consumption.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "20260729_0057"' in consumption_migration
    assert 'down_revision = "20260729_0056"' in consumption_migration
    assert "execution_order_consumption_receipts" in consumption_migration
    assert "guard_execution_consumption_receipt_immutable" in (
        consumption_migration
    )


def test_consumption_ingress_is_signed_idempotent_and_precedes_dispatch() -> None:
    service = (
        ROOT / "backend/app/modules/policy_control/services.py"
    ).read_text(encoding="utf-8")
    route = (
        ROOT / "backend/app/api/routes/policy_control.py"
    ).read_text(encoding="utf-8")
    connector = (
        ROOT / "hospital-connector/app/main.py"
    ).read_text(encoding="utf-8")

    assert "async def accept_execution_consumption" in service
    assert "EXECUTION_CONSUMPTION_IDEMPOTENCY_CONFLICT" in service
    assert "execution_order.consumed" in service
    assert "/orders/{order_id}/consumption" in route
    assert "start_authorized_fixed_reference_execution" in connector
    assert "deliver_signed_message" in connector
    assert "dispatch_authorized_fixed_reference_execution" in connector
    assert connector.index("deliver_signed_message", connector.index(
        "def start_approved_execution"
    )) < connector.index(
        "dispatch_authorized_fixed_reference_execution",
        connector.index("def start_approved_execution"),
    )
