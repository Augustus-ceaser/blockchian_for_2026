from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.connector_control.models import (
    ConnectorCapabilityManifest,
    ConnectorCertificate,
    ConnectorEnrollmentToken,
    ConnectorHeartbeat,
    ConnectorRegistrationRequest,
    HospitalConnector,
)
from app.modules.connector_control.services import sha256_text

ROOT = Path(__file__).resolve().parents[2]


def test_control_tables_are_parallel_to_legacy_connectors() -> None:
    assert HospitalConnector.__tablename__ == "hospital_connectors"
    assert ConnectorEnrollmentToken.__tablename__ == "connector_enrollment_tokens"
    assert ConnectorRegistrationRequest.__tablename__ == "connector_registration_requests"
    assert ConnectorCertificate.__tablename__ == "connector_certificates"
    assert ConnectorCapabilityManifest.__tablename__ == "connector_capability_manifests"
    assert ConnectorHeartbeat.__tablename__ == "connector_heartbeats"
    assert HospitalConnector.__tablename__ != "connectors"


def test_token_digest_does_not_retain_plaintext() -> None:
    raw = "alpha-token-that-must-never-be-persisted"
    encoded = sha256_text(raw)
    assert encoded.startswith("sha256:")
    assert raw not in encoded
    assert len(encoded) == 71


def test_alpha_manifest_keeps_transfer_and_execution_fail_closed_constraint() -> None:
    rendered = " ".join(str(item.sqltext) for item in ConnectorCapabilityManifest.__table__.constraints if hasattr(item, "sqltext"))
    for field in (
        "execution_enabled", "data_transfer_enabled", "model_transfer_enabled",
        "artifact_egress_enabled", "hard_isolation",
    ):
        assert field in rendered


def test_openapi_exposes_control_not_execution_endpoints() -> None:
    with TestClient(create_app(Settings(app_env="test"))) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/connector-control/enrollment-tokens" in paths
    assert "/api/v1/connector-control/bootstrap/registrations" in paths
    assert "/api/v1/connector-control/ingress/connectors/{connector_id}/heartbeat" in paths
    assert "/api/v1/connector-control/ingress/connectors/{connector_id}/rotate-certificate" in paths
    connector_paths = "\n".join(path for path in paths if "/connector-control/" in path)
    assert "execution-order" not in connector_paths
    assert "local-asset" not in connector_paths
    assert "artifact" not in connector_paths


def test_compose_is_loopback_and_storage_isolated() -> None:
    compose = (ROOT / "compose.hospital-connector-alpha.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:18600:18600" in compose
    assert "127.0.0.1:18443:8443" in compose
    assert "medtrust-space_postgres_data" not in compose
    assert "medtrust-space_minio_data" not in compose
    assert "CONNECTOR_DATA_ROOT" in compose
    for name in ("connector-ingress.conf", "connector-ingress-a1.conf"):
        ingress = (ROOT / "hospital-connector/nginx" / name).read_text(
            encoding="utf-8"
        )
        assert "ssl_verify_client on" in ingress
        assert "$ssl_client_escaped_cert" in ingress
        assert "$ssl_client_fingerprint" in ingress
        assert "$http_x_client_certificate_fingerprint" not in ingress


def test_connector_source_has_no_data_model_or_execution_surface() -> None:
    source = (ROOT / "hospital-connector/app/main.py").read_text(encoding="utf-8")
    for forbidden in (
        "ComputeJob(", "ComputeRun(", "ExecutionOrder(", "LocalAsset(",
        "Artifact(", "safetensors", "trust_remote_code",
    ):
        assert forbidden not in source
    assert '"execution_enabled": False' in source
    assert '"data_transfer_enabled": False' in source
    assert '"hard_isolation": False' in source
    assert "ssl.create_default_context" in source
    assert "connector.next.key.pem" in source


def test_revoked_connector_is_blocked_from_manifest_and_heartbeat() -> None:
    services = (ROOT / "backend/app/modules/connector_control/services.py").read_text(encoding="utf-8")
    assert services.count('raise ConnectorControlError("CONNECTOR_REVOKED")') >= 2
    assert 'event_type="connector.certificate.rotated"' in services
    assert 'current.status = "superseded"' in services


def test_private_material_is_not_logged_or_centrally_modeled() -> None:
    source = (ROOT / "hospital-connector/app/main.py").read_text(encoding="utf-8")
    assert '"private_key"' in source
    assert "Enrollment Token明文" not in source
    central_columns = {column.name for table in (
        HospitalConnector.__table__, ConnectorEnrollmentToken.__table__,
        ConnectorRegistrationRequest.__table__, ConnectorCertificate.__table__,
    ) for column in table.columns}
    assert "private_key" not in central_columns
    assert "token_plaintext" not in central_columns


def test_migration_is_single_increment_from_0049() -> None:
    migration = (ROOT / "backend/alembic/versions/20260729_0050_phase513b_connector_control.py").read_text(encoding="utf-8")
    assert 'revision = "20260729_0050"' in migration
    assert 'down_revision = "20260728_0049"' in migration
    assert "guard_connector_control_immutable" in migration
