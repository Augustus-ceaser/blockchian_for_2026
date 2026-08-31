from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.connector_control.models import (
    HospitalExecutorMirror,
    HospitalExecutorStatusEvent,
)

ROOT = Path(__file__).resolve().parents[2]


def test_executor_mirror_is_control_only() -> None:
    columns = {column.name for column in HospitalExecutorMirror.__table__.columns}
    assert {
        "executor_instance_id", "certificate_fingerprint",
        "capability_manifest_digest", "runtime_digest", "image_digest",
        "execution_enabled", "hard_isolation",
    } <= columns
    for forbidden in (
        "command", "arguments", "input_path", "output_path", "model_weights",
        "patient_id", "job_id", "run_id", "artifact_id",
    ):
        assert forbidden not in columns
    constraints = " ".join(
        str(item.sqltext)
        for item in HospitalExecutorMirror.__table__.constraints
        if hasattr(item, "sqltext")
    )
    assert "NOT execution_enabled AND NOT hard_isolation" in constraints
    assert HospitalExecutorStatusEvent.__tablename__ == "hospital_executor_status_events"


def test_openapi_exposes_status_not_execution() -> None:
    with TestClient(create_app(Settings(app_env="test"))) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/connector-control/executors" in paths
    assert (
        "/api/v1/connector-control/ingress/connectors/{connector_id}/executors/status"
        in paths
    )
    executor_paths = "\n".join(path for path in paths if "executor" in path.lower())
    for forbidden in ("/execute", "/run", "/artifact", "/evidence-bundle"):
        assert forbidden not in executor_paths


def test_migration_chain_and_fixed_reference_executor_boundary() -> None:
    migration = (
        ROOT
        / "backend/alembic/versions/20260729_0053_phase513e1a_executor_control_mirror.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "20260729_0053"' in migration
    event_scope_migration = (
        ROOT
        / "backend/alembic/versions/20260729_0054_phase513e1a_executor_event_scope.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "20260729_0054"' in event_scope_migration
    assert 'down_revision = "20260729_0053"' in event_scope_migration
    assert '["mirror_id", "status_sequence"]' in event_scope_migration
    assert 'down_revision = "20260729_0052"' in migration
    compose = (ROOT / "compose.phase513e1a.yml").read_text(encoding="utf-8")
    assert "hospital-executor:" in compose
    assert "medtrust-space-coordinator@sha256:3c26323f" in compose
    assert "network_mode: none" in compose
    assert "read_only: true" in compose
    assert 'user: "10001:10001"' in compose
    assert "privileged: true" not in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "D:/MedTrustAssets/pathmnist_smoke/data:/assets/data:ro" in compose
    assert "D:/MedTrustAssets/pathmnist_smoke/model:/assets/model:ro" in compose
    assert "/var/run/docker.sock" not in compose
    assert "127.0.0.1:" in compose
    assert "CONNECTOR_LOCAL_ADMIN_PASSWORD" in compose


def test_status_validator_is_fail_closed() -> None:
    services = (
        ROOT / "backend/app/modules/connector_control/services.py"
    ).read_text(encoding="utf-8")
    for guard in (
        "EXECUTOR_STATUS_SCHEMA_INVALID",
        "EXECUTOR_STATUS_PROHIBITED_FIELD",
        "EXECUTOR_STATUS_CAPABILITY_FORBIDDEN",
        "EXECUTOR_STATUS_TIMESTAMP_OUT_OF_WINDOW",
        "EXECUTOR_STATUS_DIGEST_MISMATCH",
        "EXECUTOR_STATUS_SEQUENCE_NOT_INCREASING",
        "EXECUTOR_HEARTBEAT_SEQUENCE_DECREASED",
        "EXECUTOR_REVOKED",
    ):
        assert guard in services
