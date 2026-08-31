from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from app.registry import (
    create_execution_image_manifest,
    create_executor_fixed_execution_readiness_attestation,
    create_executor_security_profile,
    evaluate_executor_admission,
    record_executor_heartbeat,
    transition_execution_image,
)
from tests.test_executor_control import approved_executor, heartbeat, image_payload


def ready_objects(tmp_path):
    db, executor = approved_executor(tmp_path)
    db.executescript(
        """CREATE TABLE IF NOT EXISTS audit (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
          detail_json TEXT NOT NULL, previous_digest TEXT,
          event_digest TEXT NOT NULL UNIQUE
        );"""
    )
    db.execute(
        """INSERT INTO audit(event_type,occurred_at,detail_json,previous_digest,event_digest)
           VALUES('fixture.ready',?,'{}',NULL,?)""",
        (datetime.now(timezone.utc).isoformat(), main.digest(b"audit-head")),
    )
    accepted = record_executor_heartbeat(
        db,
        executor_id=executor["id"],
        certificate_fingerprint=executor["fingerprint_sha256"],
        payload=heartbeat(executor),
        canonical_digest=main.canonical_digest,
    )
    assert accepted["heartbeat_sequence"] == 1
    create_executor_security_profile(
        db,
        executor_id=executor["id"],
        checked_by="local-admin",
        canonical_digest=main.canonical_digest,
    )
    image_id = create_execution_image_manifest(
        db,
        payload=image_payload(executor),
        canonical_digest=main.canonical_digest,
    )
    transition_execution_image(db, manifest_id=image_id, action="approve")
    admission = evaluate_executor_admission(
        db,
        executor_id=executor["id"],
        image_manifest_id=image_id,
        checked_by="local-admin",
        canonical_digest=main.canonical_digest,
    )
    db.commit()
    return db, executor, image_id, admission["id"]


def create_attestation(db, executor, **overrides):
    arguments = {
        "executor_id": executor["id"],
        "connector_id": executor["connector_id"],
        "connector_certificate_fingerprint": main.digest(b"connector-cert"),
        "signing_key_id": "connector-signing-key-00000001",
        "ttl_seconds": 3600,
        "canonical_digest": main.canonical_digest,
        "signer": lambda payload: "signed:" + payload["payload_digest"],
    }
    arguments.update(overrides)
    return create_executor_fixed_execution_readiness_attestation(db, **arguments)


def test_ready_attestation_uses_formal_local_objects(tmp_path) -> None:
    db, executor, image_id, admission_id = ready_objects(tmp_path)
    result = create_attestation(db, executor)
    payload = result["payload"]
    assert payload["readiness_result"] == (
        "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION"
    )
    assert payload["image_manifest"]["local_object_id"] == image_id
    assert payload["admission"]["local_object_id"] == admission_id
    assert payload["capability"]["supported_task_types"] == [
        "PATHMNIST_REFERENCE_V1"
    ]
    assert payload["capability"]["hard_isolation"] is False


def test_caller_cannot_override_local_digests(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    with pytest.raises(TypeError):
        create_attestation(
            db, executor, image_digest=main.digest(b"caller-controlled")
        )


@pytest.mark.parametrize(
    ("statement", "expected_reason"),
    [
        (
            "UPDATE local_executors SET status='paused' WHERE id=?",
            "EXECUTOR_NOT_ACTIVE",
        ),
        (
            "UPDATE local_executor_certificates SET status='revoked' WHERE executor_id=?",
            "EXECUTOR_CERTIFICATE_INVALID",
        ),
        (
            "UPDATE local_executors SET last_heartbeat_at=NULL WHERE id=?",
            "EXECUTOR_HEARTBEAT_MISSING",
        ),
        (
            "UPDATE local_executor_security_profiles SET status='invalid' WHERE executor_id=?",
            "SECURITY_PROFILE_INVALID",
        ),
        (
            "UPDATE local_execution_image_manifests SET status='revoked' WHERE id=(SELECT image_manifest_id FROM local_executor_admission_checks WHERE executor_id=? ORDER BY checked_at DESC LIMIT 1)",
            "IMAGE_MANIFEST_NOT_APPROVED",
        ),
        (
            "UPDATE local_execution_image_manifests SET signature_verified=0 WHERE id=(SELECT image_manifest_id FROM local_executor_admission_checks WHERE executor_id=? ORDER BY checked_at DESC LIMIT 1)",
            "IMAGE_SIGNATURE_STATUS_INVALID",
        ),
        (
            "UPDATE local_execution_image_manifests SET security_scan_status='failed' WHERE id=(SELECT image_manifest_id FROM local_executor_admission_checks WHERE executor_id=? ORDER BY checked_at DESC LIMIT 1)",
            "IMAGE_SCAN_STATUS_INVALID",
        ),
        (
            "UPDATE local_executor_admission_checks SET decision='rejected' WHERE executor_id=?",
            "ADMISSION_NOT_APPROVED",
        ),
    ],
)
def test_invalid_formal_object_generates_not_ready(
    tmp_path, statement: str, expected_reason: str,
) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    db.execute("DROP TRIGGER IF EXISTS trg_executor_security_profiles_immutable")
    db.execute("DROP TRIGGER IF EXISTS trg_executor_admissions_immutable")
    db.execute(statement, (executor["id"],))
    db.commit()
    result = create_attestation(db, executor)
    assert result["payload"]["readiness_result"] == "NOT_READY"
    assert result["payload"]["readiness_reason"] == expected_reason


def test_expired_admission_is_not_ready(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    db.execute("DROP TRIGGER trg_executor_admissions_immutable")
    row = db.execute(
        "SELECT id,policy_snapshot FROM local_executor_admission_checks"
    ).fetchone()
    snapshot = json.loads(row["policy_snapshot"])
    snapshot["valid_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    db.execute(
        "UPDATE local_executor_admission_checks SET policy_snapshot=? WHERE id=?",
        (json.dumps(snapshot, sort_keys=True), row["id"]),
    )
    db.commit()
    result = create_attestation(db, executor)
    assert result["payload"]["readiness_reason"] == "ADMISSION_EXPIRED"


def test_admission_digest_binding_mismatch_is_not_ready(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    db.execute("DROP TRIGGER trg_executor_admissions_immutable")
    row = db.execute(
        "SELECT id,policy_snapshot FROM local_executor_admission_checks"
    ).fetchone()
    snapshot = json.loads(row["policy_snapshot"])
    snapshot["capability_digest"] = main.digest(b"tampered")
    db.execute(
        "UPDATE local_executor_admission_checks SET policy_snapshot=? WHERE id=?",
        (json.dumps(snapshot, sort_keys=True), row["id"]),
    )
    db.commit()
    result = create_attestation(db, executor)
    assert result["payload"]["readiness_reason"] == "ADMISSION_BINDING_INVALID"


def test_sequence_increments_and_events_are_append_only(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    first = create_attestation(db, executor)
    second = create_attestation(db, executor)
    assert second["payload"]["event_sequence"] == (
        first["payload"]["event_sequence"] + 1
    )
    with pytest.raises(Exception, match="immutable"):
        db.execute(
            """UPDATE local_executor_readiness_attestations
               SET payload_digest=? WHERE id=?""",
            (main.digest(b"tampered"), first["id"]),
        )
    with pytest.raises(Exception, match="append-only"):
        db.execute(
            "DELETE FROM local_executor_readiness_attestations WHERE id=?",
            (first["id"],),
        )


def test_signature_and_digest_cover_event_protection_fields(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    result = create_attestation(db, executor)
    payload = result["payload"]
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"payload_digest", "signature"}
    }
    assert payload["payload_digest"] == main.canonical_digest(unsigned)
    assert payload["signature"] == "signed:" + payload["payload_digest"]
    assert payload["local_audit_head"] == main.digest(b"audit-head")
    assert payload["nonce"]


def test_ttl_is_bounded(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    with pytest.raises(ValueError, match="TTL_INVALID"):
        create_attestation(db, executor, ttl_seconds=59)
    with pytest.raises(ValueError, match="TTL_INVALID"):
        create_attestation(db, executor, ttl_seconds=3601)


def test_no_path_or_execution_side_effect_fields(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    payload = create_attestation(db, executor)["payload"]
    text = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "input_path", "output_path", "local_path", "patient_id",
        "model_weights", "private_key", "artifact_id", "run_id", "job_id",
    ):
        assert forbidden not in text
    assert db.execute(
        "SELECT count(*) FROM local_authorized_reference_executions"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM local_authorized_execution_artifacts"
    ).fetchone()[0] == 0


def test_missing_local_audit_head_is_not_ready(tmp_path) -> None:
    db, executor, *_ = ready_objects(tmp_path)
    db.execute("DELETE FROM audit")
    db.commit()
    result = create_attestation(db, executor)
    assert result["payload"]["readiness_result"] == "NOT_READY"
    assert result["payload"]["readiness_reason"] == "LOCAL_AUDIT_HEAD_MISSING"
