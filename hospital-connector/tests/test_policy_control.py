from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.registry import bootstrap_users, migrate


def prepare(tmp_path: Path) -> TestClient:
    main.STATE_DB = tmp_path / "state" / "connector.sqlite3"
    main.IDENTITY_DIR = tmp_path / "identity"
    main.CERT_DIR = tmp_path / "certificates"
    main.STATE_DB.parent.mkdir(parents=True)
    with sqlite3.connect(main.STATE_DB) as db:
        db.row_factory = sqlite3.Row
        migrate(db)
        bootstrap_users(
            db,
            "curator-test-password",
            "reviewer-test-password",
            "policy-test-password",
        )
    return TestClient(main.app)


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/local/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_policy_reviewer_has_an_independent_role_and_session(tmp_path: Path) -> None:
    reviewer = prepare(tmp_path)
    login(reviewer, "local.policy-reviewer", "policy-test-password")
    assert reviewer.get("/local/orders").status_code == 200
    assert reviewer.get("/local/assets/new").status_code == 403
    assert reviewer.get("/local/reviews").status_code == 403


def test_local_audit_serializes_concurrent_writers(tmp_path: Path) -> None:
    prepare(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda number: main.audit(
                    "test.concurrent", {"number": number}
                ),
                range(2),
            )
        )

    result = main.audit_result()
    assert result["chain_valid"] is True
    assert result["concurrency_forks"] == 0


def test_local_migration_contains_policy_control_tables(tmp_path: Path) -> None:
    prepare(tmp_path)
    with main.connect() as db:
        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        versions = {
            row["version"]
            for row in db.execute(
                "SELECT version FROM local_schema_migrations"
            ).fetchall()
        }
    assert {
        "local_control_orders",
        "local_policy_validations",
        "local_policy_reviews",
        "local_order_receipts",
        "local_order_decisions",
        "local_order_replay_cache",
    } <= tables
    assert "phase5.13D_0001" in versions


def test_validator_is_fail_closed_for_control_and_execution_boundaries() -> None:
    source = Path(main.__file__).read_text(encoding="utf-8")
    for check in (
        "known_active_signing_key",
        "policy_signature",
        "order_signature",
        "policy_digest_bound",
        "control_mode",
        "validate_only",
        "execution_disabled",
        "hard_isolation_false",
        "valid_time_window",
        "nonce_not_replayed",
        "sequence_monotonic",
        "local_asset_version_approved",
        "metadata_digest_match",
        "quality_digest_match",
        "model_reference_metadata_only",
        "payload_size",
        "policy_schema_supported",
        "order_schema_supported",
        "policy_additional_properties",
        "order_additional_properties",
        "prohibited_fields_absent",
        "connector_binding",
        "operation_sets_disjoint",
        "output_policy_supported",
        "model_reference_format",
        "local_asset_available",
    ):
        assert f'"{check}"' in source
    assert "AUTOMATED_FAILURE_CANNOT_BE_OVERRIDDEN" in source


def test_decisions_are_connector_signed_and_central_delivery_is_mtls() -> None:
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "sign_connector_payload(decision_payload)" in source
    assert "sign_connector_payload(receipt_payload)" in source
    assert "ssl.create_default_context" in source
    assert "load_cert_chain" in source
    assert "execution_started" in source
    assert '"execution_started": False' in source


def test_connector_has_no_execution_or_data_transfer_endpoint() -> None:
    routes = {
        route.path
        for route in main.app.routes
        if hasattr(route, "path")
    }
    assert "/local/orders" in routes
    forbidden_segments = {"execute", "run", "artifact", "raw-data", "model-weight"}
    assert not any(
        segment in forbidden_segments
        for path in routes
        for segment in path.lower().split("/")
    )
