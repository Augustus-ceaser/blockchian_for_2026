from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import main
from app.registry import (
    create_execution_authorization_snapshot_from_order,
    seed_public_fixture,
)
from tests.test_executor_readiness_attestation_v2 import (
    create_attestation,
    ready_objects,
)


def fixed_item(tmp_path, monkeypatch):
    db, executor, *_ = ready_objects(tmp_path)
    seed_public_fixture(
        db,
        connector_id=executor["connector_id"],
        canonical_digest=main.canonical_digest,
    )
    attestation = create_attestation(db, executor)
    proof = attestation["payload"]
    db.execute(
        "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    db.execute(
        "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
        ("central_connector_id", json.dumps(executor["connector_id"])),
    )
    asset = db.execute(
        """SELECT v.*,q.quality_digest,d.local_asset_key
             FROM local_asset_versions v
             JOIN local_asset_descriptors d ON d.id=v.asset_id
             JOIN local_data_quality_profiles q ON q.asset_version_id=v.id
            WHERE v.is_current=1"""
    ).fetchone()
    stamp = datetime.now(timezone.utc)
    expires = min(
        datetime.fromisoformat(proof["expires_at"]),
        stamp + timedelta(hours=1),
    )
    policy = {
        "schema_version": "phase5.13E-2C-R1/policy-bundle/v1",
        "connector_id": executor["connector_id"],
        "organization_id": str(uuid4()),
        "executor_mirror_id": str(uuid4()),
        "executor_id": executor["id"],
        "application_id": str(uuid4()),
        "application_snapshot_digest": main.digest(b"application"),
        "contract_id": str(uuid4()),
        "contract_revision_id": str(uuid4()),
        "contract_digest": main.digest(b"contract"),
        "control_readiness_id": str(uuid4()),
        "readiness_digest": main.digest(b"readiness"),
        "source_executor_status_event_id": str(uuid4()),
        "source_executor_status_event_digest": proof["payload_digest"],
        "source_attestation_expires_at": proof["expires_at"],
        "central_asset_record_id": str(uuid4()),
        "central_asset_version_id": str(uuid4()),
        "local_asset_key": asset["local_asset_key"],
        "local_asset_version_ref": asset["version_label"],
        "local_asset_metadata_digest": asset["metadata_digest"],
        "quality_digest": asset["quality_digest"],
        "model_product_version_id": str(uuid4()),
        "model_reference_digest": main.digest(b"model"),
        "model_materialization_status": "FIXED_REFERENCE_ONLY",
        "attested_image_manifest_id":
            proof["image_manifest"]["local_object_id"],
        "attested_image_manifest_digest":
            proof["image_manifest"]["manifest_digest"],
        "image_digest": proof["image_manifest"]["image_digest"],
        "attested_security_profile_id":
            proof["security_profile"]["local_object_id"],
        "security_profile_digest":
            proof["security_profile"]["profile_digest"],
        "attested_resource_policy_id":
            proof["resource_policy"]["local_object_id"],
        "resource_policy_digest": proof["resource_policy"]["policy_digest"],
        "attested_admission_check_id":
            proof["admission"]["local_object_id"],
        "admission_digest": proof["admission"]["admission_digest"],
        "capability_digest": proof["capability"]["digest"],
        "purpose_code": "FIXED_REFERENCE_AUTHORIZATION",
        "purpose_summary": "Synthetic fixed reference policy test.",
        "requested_action": "EXECUTE_FIXED_REFERENCE_TASK",
        "execution_scope": "FIXED_REFERENCE_ONLY",
        "task_type": "PATHMNIST_REFERENCE_V1",
        "max_execution_count": 1,
        "task_definition_digest":
            main.canonical_digest(main.FIXED_TASK_DEFINITION),
        "runtime_timeout_seconds": 900,
        "minimum_remaining_validity_seconds": 1200,
        "input_schema_digest": main.canonical_digest(main.FIXED_INPUT_SCHEMA),
        "output_schema_digest":
            main.canonical_digest(main.FIXED_OUTPUT_SCHEMA),
        "network_policy": {"network_mode": "none"},
        "filesystem_policy": {"input_readonly": True},
        "security_policy": {
            "rootless": True,
            "privileged": False,
            "docker_socket_access": False,
            "runtime_download": False,
            "arbitrary_code_execution_enabled": False,
            "user_supplied_code_enabled": False,
            "user_supplied_model_enabled": False,
            "data_transfer_enabled": False,
            "model_transfer_enabled": False,
            "artifact_auto_egress_enabled": False,
            "hard_isolation": False,
        },
        "output_policy": {
            "allowed_files": main.FIXED_OUTPUT_SCHEMA["allowed_files"],
            "auto_egress": False,
        },
        "review_policy": {
            "local_policy_reviewer_required": True,
            "central_override": False,
        },
        "execution_authorized": True,
        "hard_isolation": False,
        "issued_at": stamp.isoformat(),
        "not_before": (stamp - timedelta(seconds=5)).isoformat(),
        "expires_at": expires.isoformat(),
        "nonce": uuid4().hex + uuid4().hex,
        "signing_key_id": "policy-key-1",
    }
    policy_digest = main.canonical_digest(policy)
    order_id = str(uuid4())
    order = {
        "schema_version": "phase5.13E-2C-R1/execution-order/v1",
        "execution_order_id": order_id,
        "order_mode": "FIXED_REFERENCE_EXECUTION",
        "requested_action": "EXECUTE_FIXED_REFERENCE_TASK",
        "execution_scope": "FIXED_REFERENCE_ONLY",
        "task_type": "PATHMNIST_REFERENCE_V1",
        "max_execution_count": 1,
        "consumed_count": 0,
        "policy_bundle_id": str(uuid4()),
        "policy_bundle_version_id": str(uuid4()),
        "policy_payload_digest": policy_digest,
        "readiness_id": policy["control_readiness_id"],
        "readiness_digest": policy["readiness_digest"],
        "source_executor_status_event_id":
            policy["source_executor_status_event_id"],
        "source_executor_status_event_digest": proof["payload_digest"],
        "connector_id": executor["connector_id"],
        "executor_mirror_id": policy["executor_mirror_id"],
        "executor_id": executor["id"],
        "central_asset_version_id": policy["central_asset_version_id"],
        "local_asset_metadata_digest": asset["metadata_digest"],
        "quality_digest": asset["quality_digest"],
        "model_reference_digest": policy["model_reference_digest"],
        "attested_image_manifest_id":
            policy["attested_image_manifest_id"],
        "attested_image_manifest_digest":
            policy["attested_image_manifest_digest"],
        "image_digest": policy["image_digest"],
        "security_profile_digest": policy["security_profile_digest"],
        "resource_policy_digest": policy["resource_policy_digest"],
        "admission_digest": policy["admission_digest"],
        "capability_digest": policy["capability_digest"],
        "task_definition_digest": policy["task_definition_digest"],
        "input_schema_digest": policy["input_schema_digest"],
        "output_schema_digest": policy["output_schema_digest"],
        "connector_sequence": 1,
        "correlation_id": uuid4().hex,
        "issued_at": stamp.isoformat(),
        "not_before": (stamp - timedelta(seconds=5)).isoformat(),
        "expires_at": expires.isoformat(),
        "nonce": uuid4().hex + uuid4().hex,
        "signing_key_id": "policy-key-1",
        "execution_authorized": True,
        "hard_isolation": False,
    }
    public_key = "test-public-key"
    item = {
        "execution_order_id": order_id,
        "order_key": "ORD-FIXED-1",
        "order": order,
        "order_digest": main.canonical_digest(order),
        "order_signature": base64.b64encode(b"order-signature").decode(),
        "policy": policy,
        "policy_digest": policy_digest,
        "policy_signature": base64.b64encode(b"policy-signature").decode(),
        "signing_key": {
            "key_id": "policy-key-1",
            "algorithm": "Ed25519",
            "public_key_material": public_key,
            "fingerprint": main.digest(public_key.encode("ascii")),
            "status": "active",
        },
        "central_status": "available_for_connector",
    }
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    return db, item


def test_fixed_order_validation_covers_complete_control_set(
    tmp_path, monkeypatch,
) -> None:
    db, item = fixed_item(tmp_path, monkeypatch)
    checks, failure = main.validate_fixed_execution_order(item, db)
    assert failure is None
    assert len(checks) >= 38
    assert all(check["passed"] for check in checks)
    assert {
        "status_v2_current",
        "local_asset_binding",
        "admission_binding",
        "fixed_task_definition",
        "local_review_without_central_override",
    } <= {check["code"] for check in checks}


def test_fixed_order_tamper_fails_before_local_review(
    tmp_path, monkeypatch,
) -> None:
    db, item = fixed_item(tmp_path, monkeypatch)
    item["policy"]["security_policy"]["runtime_download"] = True
    checks, failure = main.validate_fixed_execution_order(item, db)
    assert failure is not None
    assert not all(check["passed"] for check in checks)


def test_accepted_fixed_order_creates_signed_unconsumed_snapshot(
    tmp_path, monkeypatch,
) -> None:
    db, item = fixed_item(tmp_path, monkeypatch)
    checks, failure = main.validate_fixed_execution_order(item, db)
    assert failure is None
    order = item["order"]
    policy = item["policy"]
    receipt_id = str(uuid4())
    decision_id = str(uuid4())
    validation_digest = main.canonical_digest(
        {"checks": checks, "failure_code": None}
    )
    receipt_payload = {
        "schema_version": "phase5.13E-2C-R1/connector-receipt/v1",
        "receipt_id": receipt_id,
        "execution_order_id": order["execution_order_id"],
        "central_order_key": item["order_key"],
        "connector_sequence": 1,
        "order_digest": item["order_digest"],
        "policy_digest": item["policy_digest"],
        "source_executor_status_event_digest":
            order["source_executor_status_event_digest"],
        "validation_status": "passed",
        "automated_validation_digest": validation_digest,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "local_audit_head": main.digest(b"audit-head"),
        "execution_started": False,
        "hard_isolation": False,
    }
    receipt_digest = main.canonical_digest(receipt_payload)
    decided_at = datetime.now(timezone.utc).isoformat()
    decision_payload = {
        "schema_version": "phase5.13E-2C-R1/connector-decision/v1",
        "decision_id": decision_id,
        "execution_order_id": order["execution_order_id"],
        "receipt_id": receipt_id,
        "receipt_digest": receipt_digest,
        "policy_digest": item["policy_digest"],
        "order_digest": item["order_digest"],
        "source_executor_status_event_digest":
            order["source_executor_status_event_digest"],
        "automated_validation_digest": validation_digest,
        "reviewer_id": "local-policy-reviewer-1",
        "decision": "accepted",
        "reason_code": "ACCEPT_FIXED_REFERENCE_EXECUTION",
        "reason_text": "Fixed reference authorization reviewed.",
        "decided_at": decided_at,
        "local_audit_head": main.digest(b"audit-head"),
        "execution_started": False,
        "hard_isolation": False,
    }
    db.execute(
        """INSERT INTO local_control_orders
           (id,central_order_id,connector_sequence,order_payload,order_digest,
            order_signature,policy_payload,policy_digest,policy_signature,
            signing_key_id,signing_public_key,signing_key_fingerprint,
            central_status,local_status,received_at,expires_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            order["execution_order_id"], item["order_key"], 1,
            json.dumps(order), item["order_digest"], item["order_signature"],
            json.dumps(policy), item["policy_digest"], item["policy_signature"],
            "policy-key-1", "test-public-key",
            item["signing_key"]["fingerprint"], "accepted", "accepted",
            receipt_payload["received_at"], order["expires_at"],
        ),
    )
    db.execute(
        """INSERT INTO local_policy_validations VALUES(?,?,?,?,?,?)""",
        (
            str(uuid4()), order["execution_order_id"], "passed",
            json.dumps(checks), None, receipt_payload["received_at"],
        ),
    )
    db.execute(
        """INSERT INTO local_policy_reviews VALUES(?,?,?,?,?,?,?)""",
        (
            str(uuid4()), order["execution_order_id"],
            "local-policy-reviewer-1", "accepted",
            "ACCEPT_FIXED_REFERENCE_EXECUTION",
            "Fixed reference authorization reviewed.", decided_at,
        ),
    )
    db.execute(
        """INSERT INTO local_order_receipts
           VALUES(?,?,?,?,?,'delivered',?,?)""",
        (
            receipt_id, order["execution_order_id"],
            json.dumps(receipt_payload), receipt_digest, "receipt-signature",
            receipt_payload["received_at"], receipt_payload["received_at"],
        ),
    )
    db.execute(
        """INSERT INTO local_order_decisions
           VALUES(?,?,?,?,?,'delivered',?,?)""",
        (
            decision_id, order["execution_order_id"],
            json.dumps(decision_payload),
            main.canonical_digest(decision_payload), "decision-signature",
            decided_at, decided_at,
        ),
    )
    db.commit()
    result = create_execution_authorization_snapshot_from_order(
        db,
        local_order_id=order["execution_order_id"],
        canonical_digest=main.canonical_digest,
        signer=lambda payload: "signed-snapshot:" + main.canonical_digest(payload),
    )
    assert result["status"] == "validated"
    snapshot = db.execute(
        "SELECT * FROM local_execution_authorization_snapshots WHERE id=?",
        (result["id"],),
    ).fetchone()
    assert snapshot["status"] == "validated"
    assert snapshot["consumed_at"] is None
    assert snapshot["source_executor_status_event_digest"] == (
        order["source_executor_status_event_digest"]
    )
    assert snapshot["connector_signature"].startswith("signed-snapshot:")
    assert db.execute(
        "SELECT count(*) FROM local_authorized_task_manifests"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM local_authorized_reference_executions"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM local_authorized_execution_artifacts"
    ).fetchone()[0] == 0
    with pytest.raises(Exception, match="immutable"):
        db.execute(
            """UPDATE local_execution_authorization_snapshots
               SET capability_digest=? WHERE id=?""",
            (main.digest(b"tampered"), result["id"]),
        )
