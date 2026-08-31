from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.policy_control.models import (
    ConnectorOrderDecision,
    ConnectorOrderReceipt,
    ControlReadinessSnapshot,
    ExecutionOrder,
    PolicyBundle,
    PolicyBundleVersion,
    PolicySigningKey,
)

ROOT = Path(__file__).resolve().parents[2]


def rendered_constraints(table) -> str:
    return " ".join(
        str(item.sqltext) for item in table.constraints if hasattr(item, "sqltext")
    )


def test_policy_tables_keep_control_only_constraints() -> None:
    order_constraints = rendered_constraints(ExecutionOrder.__table__)
    policy_constraints = rendered_constraints(PolicyBundleVersion.__table__)
    readiness_constraints = rendered_constraints(ControlReadinessSnapshot.__table__)
    assert "CONTROL_VALIDATION_ONLY" in order_constraints
    assert "VALIDATE_POLICY_ONLY" in order_constraints
    assert "NOT execution_authorized" in policy_constraints
    assert "CONTROL_POLICY_VALIDATION" in readiness_constraints
    assert "NOT execution_authorized" in readiness_constraints
    assert "FIXED_REFERENCE_EXECUTION" in readiness_constraints
    assert "EXECUTE_FIXED_REFERENCE_TASK" in readiness_constraints
    assert "PATHMNIST_REFERENCE_V1" in readiness_constraints
    assert "NOT hard_isolation" in readiness_constraints


def test_private_signing_material_is_not_centrally_modeled() -> None:
    columns = {column.name for column in PolicySigningKey.__table__.columns}
    assert "private_key" not in columns
    assert "private_key_material" not in columns
    assert {"public_key_material", "public_key_fingerprint", "algorithm"} <= columns


def test_receipt_and_decision_are_separate_immutable_records() -> None:
    assert ConnectorOrderReceipt.__tablename__ == "connector_order_receipts"
    assert ConnectorOrderDecision.__tablename__ == "connector_order_decisions"
    assert "decision" not in {column.name for column in ExecutionOrder.__table__.columns}
    assert "connector_id" in {column.name for column in PolicyBundle.__table__.columns}


def test_openapi_exposes_policy_control_without_execution_surface() -> None:
    with TestClient(create_app(Settings(app_env="test"))) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/policy-control/policies/compile" in paths
    assert "/api/v1/policy-control/orders" in paths
    assert (
        "/api/v1/policy-control/ingress/connectors/{connector_id}/orders/available"
        in paths
    )
    policy_paths = "\n".join(path for path in paths if "/policy-control/" in path)
    for forbidden in ("/execute", "/start", "/run", "/artifacts"):
        assert forbidden not in policy_paths


def test_policy_ingress_has_mtls_certificate_and_active_connector_guards() -> None:
    route = (ROOT / "backend/app/api/routes/policy_control.py").read_text(
        encoding="utf-8"
    )
    assert 'Header(alias="X-Client-Certificate")' in route
    assert "certificate.fingerprint_sha256 != fingerprint" not in route
    assert 'connector.status != "active"' in route
    assert 'certificate.status != "active"' in route
    assert "_verified_policy_connector" in route


def test_global_auth_only_exempts_the_dedicated_policy_ingress() -> None:
    source = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    assert 'f"{current_settings.api_v1_prefix}/policy-control/ingress/"' in source
    assert 'f"{current_settings.api_v1_prefix}/policy-control/"' not in source


def test_migration_is_single_increment_and_adds_immutable_guards() -> None:
    migration = (
        ROOT
        / "backend/alembic/versions/20260729_0052_phase513d_signed_policy_control.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "20260729_0052"' in migration
    assert 'down_revision = "20260729_0051"' in migration
    assert "guard_signed_policy_immutability" in migration
    assert '"policy_bundle_versions", "policy_revocations"' in migration
    assert '"connector_order_receipts", "connector_order_decisions"' in migration
