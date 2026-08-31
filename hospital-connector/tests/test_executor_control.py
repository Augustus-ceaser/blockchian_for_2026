from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import main
from app.registry import (
    approve_executor_registration,
    bootstrap_users,
    create_execution_image_manifest,
    create_executor_security_profile,
    create_executor_registration,
    destroy_executor_runtime,
    evaluate_executor_admission,
    list_executors,
    migrate,
    prepare_executor_runtime,
    reconcile_fixed_reference_execution,
    review_local_artifact,
    record_executor_heartbeat,
    reject_runtime_start,
    scan_local_artifact,
    start_fixed_reference_execution,
    transition_execution_image,
    transition_executor,
    validate_executor_capability,
)


def capability() -> dict:
    return {
        "schema_version": "phase5.13E-1A/executor-capability/v1",
        "manifest_version": "1",
        "executor_version": "0.1.0-alpha",
        "runtime": "container",
        "image_digest": main.digest(b"inert-image"),
        "architecture": "amd64",
        "network_mode": "none",
        "filesystem_mode": "readonly_input",
        "rootless": True,
        "gpu": False,
        "supported_task_types": ["PATHMNIST_REFERENCE_V1"],
        "resource_limits": {
            "cpu_cores": 2,
            "memory_mb": 2048,
            "disk_mb": 1024,
            "processes": 64,
            "timeout_seconds": 900,
        },
        "security_features": [
            "no_new_privileges",
            "drop_all_capabilities",
            "read_only_root",
            "no_runtime_install",
            "no_runtime_download",
        ],
        "execution_enabled": False,
        "hard_isolation": False,
    }


def database(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(tmp_path / "executor.sqlite3")
    db.row_factory = sqlite3.Row
    migrate(db)
    bootstrap_users(db, "curator", "reviewer", "policy", "admin")
    return db


def approved_executor(tmp_path: Path) -> tuple[sqlite3.Connection, dict]:
    db = database(tmp_path)
    instance = "hex-00000000-0000-4000-8000-000000000001"
    registration_id = create_executor_registration(
        db,
        executor_instance_id=instance,
        executor_version="0.1.0-alpha",
        architecture="amd64",
        csr_pem="-----BEGIN CERTIFICATE REQUEST-----\nfixture\n-----END CERTIFICATE REQUEST-----",
        installation_digest=main.digest(instance.encode()),
        capability_payload=capability(),
        runtime_digest=main.digest(b"inert-runtime"),
        nonce="registration-nonce-00000001",
        request_timestamp=datetime.now(timezone.utc).isoformat(),
        canonical_digest=main.canonical_digest,
    )
    executor_id = approve_executor_registration(
        db,
        registration_id=registration_id,
        connector_id="connector-fixture",
        reviewer_id="local-admin",
        certificate={
            "serial_number": "01",
            "subject": "CN=executor",
            "issuer": "CN=local-test-ca",
            "fingerprint_sha256": main.digest(b"executor-cert"),
            "certificate_pem": "certificate",
            "valid_from": datetime.now(timezone.utc).isoformat(),
            "valid_to": datetime.now(timezone.utc).isoformat(),
        },
    )
    executor = next(item for item in list_executors(db) if item["id"] == executor_id)
    return db, executor


def heartbeat(executor: dict, sequence: int = 1) -> dict:
    payload = {
        "executor_id": executor["id"],
        "sequence": sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "healthy",
        "capability_digest": executor["manifest_digest"],
        "runtime_digest": executor["runtime_digest"],
        "nonce": f"heartbeat-nonce-{sequence:08d}",
    }
    payload["message_digest"] = main.canonical_digest(payload)
    return payload


def test_local_migration_and_admin_role_are_separate(tmp_path: Path) -> None:
    db = database(tmp_path)
    tables = {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "local_executor_registrations",
        "local_executors",
        "local_executor_certificates",
        "local_executor_capability_manifests",
        "local_executor_heartbeats",
        "local_executor_status_sync_history",
        "local_executor_security_profiles",
        "local_execution_image_manifests",
        "local_executor_admission_checks",
    } <= tables
    assert db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version='phase5.13E_0001'"
    ).fetchone()
    assert db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version='phase5.13E_0002'"
    ).fetchone()
    assert db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version='phase5.13E_0003'"
    ).fetchone()
    admin = db.execute(
        "SELECT role FROM local_users WHERE username='local.connector-admin'"
    ).fetchone()
    assert admin["role"] == "connector_local_admin"


def test_capability_gate_rejects_unknown_or_unsafe_claims() -> None:
    unsafe = capability()
    unsafe["ready_to_execute"] = True
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        validate_executor_capability(unsafe)
    networked = capability()
    networked["network_mode"] = "bridge"
    with pytest.raises(ValueError, match="NETWORK_POLICY_INVALID"):
        validate_executor_capability(networked)
    unknown = capability()
    unknown["supported_task_types"] = ["ARBITRARY_PYTHON"]
    with pytest.raises(ValueError, match="TASK_TYPE_UNKNOWN"):
        validate_executor_capability(unknown)


def test_duplicate_registration_is_rejected(tmp_path: Path) -> None:
    db, executor = approved_executor(tmp_path)
    registration = db.execute(
        "SELECT * FROM local_executor_registrations WHERE executor_id=?",
        (executor["id"],),
    ).fetchone()
    with pytest.raises(ValueError, match="DUPLICATE"):
        create_executor_registration(
            db,
            executor_instance_id=executor["executor_instance_id"],
            executor_version="0.1.0-alpha",
            architecture="amd64",
            csr_pem=registration["csr_pem"],
            installation_digest=registration["installation_digest"],
            capability_payload=json.loads(registration["capability_payload"]),
            runtime_digest=registration["runtime_digest"],
            nonce="registration-nonce-00000002",
            request_timestamp=datetime.now(timezone.utc).isoformat(),
            canonical_digest=main.canonical_digest,
        )


def test_certificate_sequence_replay_and_revocation_fail_closed(
    tmp_path: Path,
) -> None:
    db, executor = approved_executor(tmp_path)
    payload = heartbeat(executor)
    with pytest.raises(ValueError, match="CERTIFICATE_INVALID"):
        record_executor_heartbeat(
            db, executor_id=executor["id"],
            certificate_fingerprint=main.digest(b"wrong-cert"),
            payload=payload, canonical_digest=main.canonical_digest,
        )
    accepted = record_executor_heartbeat(
        db, executor_id=executor["id"],
        certificate_fingerprint=executor["fingerprint_sha256"],
        payload=payload, canonical_digest=main.canonical_digest,
    )
    assert accepted["heartbeat_sequence"] == 1
    with pytest.raises(ValueError, match="SEQUENCE_NOT_INCREASING"):
        record_executor_heartbeat(
            db, executor_id=executor["id"],
            certificate_fingerprint=executor["fingerprint_sha256"],
            payload=payload, canonical_digest=main.canonical_digest,
        )
    transition_executor(
        db, executor_id=executor["id"], action="revoke", reason="negative test"
    )
    with pytest.raises(ValueError, match="REVOKED"):
        record_executor_heartbeat(
            db, executor_id=executor["id"],
            certificate_fingerprint=executor["fingerprint_sha256"],
            payload=heartbeat(executor, 2),
            canonical_digest=main.canonical_digest,
        )


def test_source_has_no_execution_implementation() -> None:
    source = Path(main.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "torch.load", "model.forward", "subprocess.Popen", "docker run",
        "ComputeRun", "LocalRun", "Artifact(", "EvidenceBundle(",
        "trust_remote_code", "pip install", "huggingface",
    ):
        assert forbidden not in source
    assert '"execution_enabled": False' in source
    assert '"hard_isolation": False' in source


def image_payload(executor: dict, **overrides) -> dict:
    payload = {
        "image_id": "pathmnist-reference-fixed",
        "image_digest": executor["image_digest"],
        "signature": "hmac-sha256:" + "1" * 64,
        "signature_verified": True,
        "builder": "controlled-test-builder",
        "build_time": datetime.now(timezone.utc).isoformat(),
        "dependency_hash": main.digest(b"fixed-dependencies"),
        "runtime_version": "python-3.12-control-only",
        "security_scan_status": "passed",
    }
    payload.update(overrides)
    return payload


def test_security_profile_image_trust_and_admission_are_control_only(
    tmp_path: Path,
) -> None:
    db, executor = approved_executor(tmp_path)
    profile_id = create_executor_security_profile(
        db, executor_id=executor["id"], checked_by="local-admin",
        canonical_digest=main.canonical_digest,
    )
    manifest_id = create_execution_image_manifest(
        db, payload=image_payload(executor),
        canonical_digest=main.canonical_digest,
    )
    assert transition_execution_image(
        db, manifest_id=manifest_id, action="approve"
    ) == "approved"
    result = evaluate_executor_admission(
        db, executor_id=executor["id"], image_manifest_id=manifest_id,
        checked_by="local-admin", canonical_digest=main.canonical_digest,
    )
    assert profile_id
    assert result["decision"] == "approved"
    row = db.execute(
        "SELECT * FROM local_executor_admission_checks WHERE id=?",
        (result["id"],),
    ).fetchone()
    assert row["execution_enabled"] == 0


def test_latest_unsigned_revoked_and_digest_mismatch_images_are_rejected(
    tmp_path: Path,
) -> None:
    db, executor = approved_executor(tmp_path)
    create_executor_security_profile(
        db, executor_id=executor["id"], checked_by="local-admin",
        canonical_digest=main.canonical_digest,
    )
    with pytest.raises(ValueError, match="LATEST_IMAGE_FORBIDDEN"):
        create_execution_image_manifest(
            db, payload=image_payload(executor, image_id="executor:latest"),
            canonical_digest=main.canonical_digest,
        )
    unsigned_id = create_execution_image_manifest(
        db, payload=image_payload(
            executor, image_id="unsigned-image", signature=None,
            signature_verified=False,
        ),
        canonical_digest=main.canonical_digest,
    )
    with pytest.raises(ValueError, match="UNSIGNED_IMAGE"):
        transition_execution_image(
            db, manifest_id=unsigned_id, action="approve"
        )
    wrong_id = create_execution_image_manifest(
        db, payload=image_payload(
            executor, image_id="wrong-digest",
            image_digest=main.digest(b"wrong-image"),
        ),
        canonical_digest=main.canonical_digest,
    )
    transition_execution_image(db, manifest_id=wrong_id, action="approve")
    mismatch = evaluate_executor_admission(
        db, executor_id=executor["id"], image_manifest_id=wrong_id,
        checked_by="local-admin", canonical_digest=main.canonical_digest,
    )
    assert "IMAGE_DIGEST_MISMATCH" in mismatch["rejection_reasons"]
    transition_execution_image(db, manifest_id=wrong_id, action="revoke")
    revoked = evaluate_executor_admission(
        db, executor_id=executor["id"], image_manifest_id=wrong_id,
        checked_by="local-admin", canonical_digest=main.canonical_digest,
    )
    assert "UNTRUSTED_IMAGE" in revoked["rejection_reasons"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("network_mode", "bridge", "NETWORK_NOT_ALLOWED"),
        ("rootless", 0, "ROOT_PRIVILEGE_FORBIDDEN"),
        ("privileged", 1, "PRIVILEGED_CONTAINER_FORBIDDEN"),
        ("docker_socket_access", 1, "DOCKER_SOCKET_FORBIDDEN"),
        ("runtime_download", 1, "RUNTIME_DOWNLOAD_FORBIDDEN"),
        ("resource_policy", "{}", "RESOURCE_POLICY_INVALID"),
    ],
)
def test_admission_rejects_unsafe_profile(
    tmp_path: Path, field: str, value, reason: str,
) -> None:
    db, executor = approved_executor(tmp_path)
    manifest_id = create_execution_image_manifest(
        db, payload=image_payload(executor),
        canonical_digest=main.canonical_digest,
    )
    transition_execution_image(db, manifest_id=manifest_id, action="approve")
    values = {
        "network_mode": "none", "filesystem_mode": "readonly_input",
        "rootless": 1, "privileged": 0, "docker_socket_access": 0,
        "runtime_download": 0,
        "resource_policy": json.dumps(capability()["resource_limits"]),
    }
    values[field] = value
    db.execute(
        """INSERT INTO local_executor_security_profiles
           (id,executor_id,security_version,network_mode,filesystem_mode,
            rootless,privileged,docker_socket_access,runtime_download,
            resource_policy,profile_digest,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,'valid',?)""",
        (
            "profile-unsafe", executor["id"], "test-v1",
            values["network_mode"], values["filesystem_mode"],
            values["rootless"], values["privileged"],
            values["docker_socket_access"], values["runtime_download"],
            values["resource_policy"], main.digest(field.encode()),
            "9999-01-01T00:00:00+00:00",
        ),
    )
    db.commit()
    result = evaluate_executor_admission(
        db, executor_id=executor["id"], image_manifest_id=manifest_id,
        checked_by="local-admin", canonical_digest=main.canonical_digest,
    )
    assert reason in result["rejection_reasons"]


def approved_runtime_binding(tmp_path: Path) -> tuple[sqlite3.Connection, dict, str]:
    db, executor = approved_executor(tmp_path)
    create_executor_security_profile(
        db, executor_id=executor["id"], checked_by="local-admin",
        canonical_digest=main.canonical_digest,
    )
    manifest_id = create_execution_image_manifest(
        db, payload=image_payload(executor),
        canonical_digest=main.canonical_digest,
    )
    transition_execution_image(db, manifest_id=manifest_id, action="approve")
    admission = evaluate_executor_admission(
        db, executor_id=executor["id"], image_manifest_id=manifest_id,
        checked_by="local-admin", canonical_digest=main.canonical_digest,
    )
    assert admission["decision"] == "approved"
    return db, executor, admission["id"]


def runtime_root(name: str) -> Path:
    root = Path("D:/MedTrustCache/phase5.13E-2A-tests") / name
    if root.exists():
        shutil.rmtree(root)
    return root


def test_runtime_prepares_empty_d_drive_sandbox_and_is_idempotent(
    tmp_path: Path,
) -> None:
    db, executor, admission_id = approved_runtime_binding(tmp_path)
    root = runtime_root("prepare")
    try:
        result = prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id=admission_id,
            sandbox_root=root, checked_by="local-admin",
            canonical_digest=main.canonical_digest,
        )
        assert result["status"] == "prepared"
        workspace = root / result["sandbox_id"]
        assert sorted(item.name for item in workspace.iterdir()) == [
            "input", "logs", "output", "runtime",
        ]
        assert not any((workspace / "input").iterdir())
        assert not any((workspace / "output").iterdir())
        duplicate = prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id=admission_id,
            sandbox_root=root, checked_by="local-admin",
            canonical_digest=main.canonical_digest,
        )
        assert duplicate["id"] == result["id"]
        assert duplicate["created"] is False
        assert db.execute(
            "SELECT count(*) c FROM local_executor_runtime_sessions"
        ).fetchone()["c"] == 1
        assert db.execute(
            "SELECT count(*) c FROM local_runtime_lifecycle_events"
        ).fetchone()["c"] == 3
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_runtime_requires_approved_admission_and_approved_image(
    tmp_path: Path,
) -> None:
    db, executor = approved_executor(tmp_path)
    with pytest.raises(ValueError, match="ADMISSION_NOT_APPROVED"):
        prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id="missing",
            sandbox_root=runtime_root("missing-admission"),
            checked_by="local-admin", canonical_digest=main.canonical_digest,
        )
    revoked_root = tmp_path / "revoked"
    revoked_root.mkdir()
    db, executor, admission_id = approved_runtime_binding(revoked_root)
    admission = db.execute(
        "SELECT image_manifest_id FROM local_executor_admission_checks WHERE id=?",
        (admission_id,),
    ).fetchone()
    transition_execution_image(
        db, manifest_id=admission["image_manifest_id"], action="revoke"
    )
    with pytest.raises(ValueError, match="IMAGE_NOT_APPROVED"):
        prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id=admission_id,
            sandbox_root=runtime_root("revoked-image"),
            checked_by="local-admin", canonical_digest=main.canonical_digest,
        )


def test_runtime_rejects_c_drive_or_host_home_sandbox(tmp_path: Path) -> None:
    db, executor, admission_id = approved_runtime_binding(tmp_path)
    with pytest.raises(ValueError, match="SANDBOX_ROOT_FORBIDDEN"):
        prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id=admission_id,
            sandbox_root=Path.home(), checked_by="local-admin",
            canonical_digest=main.canonical_digest,
        )


def test_runtime_start_is_unreachable_and_destroy_is_terminal(
    tmp_path: Path,
) -> None:
    db, executor, admission_id = approved_runtime_binding(tmp_path)
    root = runtime_root("destroy")
    result = prepare_executor_runtime(
        db, executor_id=executor["id"], admission_check_id=admission_id,
        sandbox_root=root, checked_by="local-admin",
        canonical_digest=main.canonical_digest,
    )
    with pytest.raises(ValueError, match="RUNTIME_START_FORBIDDEN"):
        reject_runtime_start(db, runtime_session_id=result["id"])
    destroyed = destroy_executor_runtime(
        db, runtime_session_id=result["id"], sandbox_root=root,
        checked_by="local-admin", canonical_digest=main.canonical_digest,
    )
    assert destroyed["status"] == "destroyed"
    assert not (root / result["sandbox_id"]).exists()
    again = destroy_executor_runtime(
        db, runtime_session_id=result["id"], sandbox_root=root,
        checked_by="local-admin", canonical_digest=main.canonical_digest,
    )
    assert again["status"] == "destroyed"
    with pytest.raises(sqlite3.IntegrityError, match="destroyed runtime is terminal"):
        db.execute(
            """UPDATE local_executor_runtime_sessions
               SET status='prepared' WHERE id=?""",
            (result["id"],),
        )


def test_runtime_schema_and_source_exclude_execution_and_sensitive_paths(
    tmp_path: Path,
) -> None:
    db = database(tmp_path)
    assert db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version='phase5.13E_0004'"
    ).fetchone()
    assert db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version='phase5.13E_0005'"
    ).fetchone()
    assert db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version='phase5.13E_0006'"
    ).fetchone()
    columns = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(local_executor_runtime_sessions)"
        ).fetchall()
    }
    assert not {
        "patient", "data_path", "model_path", "artifact_path",
        "input_path", "output_path",
    } & columns
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "RUNTIME_START_FORBIDDEN" in source
    assert 'name="task_type"' not in source
    assert 'name="model_reference"' not in source
    assert 'name="dataset_reference"' not in source
    assert 'type="file"' not in source


def test_fixed_reference_execution_creates_immutable_manifests_and_request(
    tmp_path: Path,
) -> None:
    db, executor, admission_id = approved_runtime_binding(tmp_path)
    root = runtime_root("fixed-reference")
    try:
        runtime = prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id=admission_id,
            sandbox_root=root, checked_by="local-admin",
            canonical_digest=main.canonical_digest,
        )
        launched = start_fixed_reference_execution(
            db, runtime_session_id=runtime["id"], sandbox_root=root,
            approved_execution_image_digest=capability()["image_digest"],
            checked_by="local-admin", canonical_digest=main.canonical_digest,
        )
        assert launched["status"] == "running"
        workspace = root / runtime["sandbox_id"]
        request = json.loads(
            (workspace / "runtime" / "request.json").read_text(encoding="utf-8")
        )
        assert request["task_manifest"]["task_type"] == "PATHMNIST_REFERENCE_V1"
        assert request["input_manifest"]["sample_count"] == 20
        assert set(request["input_manifest"]) == {
            "schema_version", "asset_version_id", "metadata_digest",
            "sample_count", "schema_digest", "fixed_indices",
            "fixed_indices_digest",
        }
        assert not {"filepath", "patient_id", "raw_filename", "binary_data"} & set(
            request["input_manifest"]
        )
        assert request["task_manifest"]["output_allowlist"] == [
            "aggregate_metrics.json", "confusion_matrix.csv",
            "execution_summary.json",
        ]
        duplicate = start_fixed_reference_execution(
            db, runtime_session_id=runtime["id"], sandbox_root=root,
            approved_execution_image_digest=capability()["image_digest"],
            checked_by="local-admin", canonical_digest=main.canonical_digest,
        )
        assert duplicate["id"] == launched["id"]
        assert duplicate["created"] is False
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "UPDATE local_execution_task_manifests SET task_type='OTHER'"
            )
    finally:
        if root.exists():
            shutil.rmtree(root)


@pytest.mark.parametrize("digest", ["sha256:" + "0" * 64, "", "latest"])
def test_fixed_reference_execution_rejects_wrong_image_digest(
    tmp_path: Path, digest: str,
) -> None:
    db, executor, admission_id = approved_runtime_binding(tmp_path)
    root = runtime_root("wrong-image-" + hashlib.sha256(digest.encode()).hexdigest()[:8])
    try:
        runtime = prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id=admission_id,
            sandbox_root=root, checked_by="local-admin",
            canonical_digest=main.canonical_digest,
        )
        with pytest.raises(ValueError, match="IMAGE_DIGEST_MISMATCH"):
            start_fixed_reference_execution(
                db, runtime_session_id=runtime["id"], sandbox_root=root,
                approved_execution_image_digest=digest,
                checked_by="local-admin", canonical_digest=main.canonical_digest,
            )
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_fixed_reference_completion_creates_quarantined_local_artifact(
    tmp_path: Path,
) -> None:
    db, executor, admission_id = approved_runtime_binding(tmp_path)
    root = runtime_root("completed")
    try:
        runtime = prepare_executor_runtime(
            db, executor_id=executor["id"], admission_check_id=admission_id,
            sandbox_root=root, checked_by="local-admin",
            canonical_digest=main.canonical_digest,
        )
        execution = start_fixed_reference_execution(
            db, runtime_session_id=runtime["id"], sandbox_root=root,
            approved_execution_image_digest=capability()["image_digest"],
            checked_by="local-admin", canonical_digest=main.canonical_digest,
        )
        workspace = root / runtime["sandbox_id"]
        request = json.loads(
            (workspace / "runtime" / "request.json").read_text(encoding="utf-8")
        )
        manifest = []
        media_types = {
            "aggregate_metrics.json": "application/json",
            "confusion_matrix.csv": "text/csv",
            "execution_summary.json": "application/json",
        }
        for name in (
            "aggregate_metrics.json", "confusion_matrix.csv",
            "execution_summary.json",
        ):
            content = f"fixed-{name}".encode()
            (workspace / "output" / name).write_bytes(content)
            manifest.append({
                "name": name, "media_type": media_types[name],
                "size_bytes": len(content),
                "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            })
        result = {
            "schema_version": "phase5.13E-2B-1/worker-result/v1",
            "runtime_session_id": runtime["id"],
            "request_digest": request["request_digest"],
            "status": "completed",
            "started_at": main.now(),
            "completed_at": main.now(),
            "output_manifest": manifest,
        }
        result["result_digest"] = main.canonical_digest(result)
        (workspace / "runtime" / "result.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        completed = reconcile_fixed_reference_execution(
            db, runtime_session_id=runtime["id"], sandbox_root=root,
            canonical_digest=main.canonical_digest,
        )
        assert completed["status"] == "completed"
        assert completed["artifact_status"] == "quarantined"
        assert db.execute(
            "SELECT status FROM local_executor_runtime_sessions WHERE id=?",
            (runtime["id"],),
        ).fetchone()["status"] == "completed"
        artifact = db.execute(
            "SELECT * FROM local_execution_artifacts"
        ).fetchone()
        assert artifact["status"] == "quarantined"
        assert artifact["relative_reference"].startswith("sandbox/")
        assert db.execute(
            "SELECT count(*) c FROM local_execution_artifacts"
        ).fetchone()["c"] == 1
        assert db.execute(
            "SELECT count(*) c FROM local_runtime_lifecycle_events"
        ).fetchone()["c"] == 5
    finally:
        if root.exists():
            shutil.rmtree(root)


def scanned_artifact_fixture(tmp_path: Path, name: str) -> tuple:
    db, executor, admission_id = approved_runtime_binding(tmp_path)
    bootstrap_users(
        db, "curator", "reviewer", "policy", "admin",
        artifact_reviewer_password="artifact-reviewer",
    )
    root = runtime_root(name)
    runtime = prepare_executor_runtime(
        db, executor_id=executor["id"], admission_check_id=admission_id,
        sandbox_root=root, checked_by="admin",
        canonical_digest=main.canonical_digest,
    )
    execution = start_fixed_reference_execution(
        db, runtime_session_id=runtime["id"], sandbox_root=root,
        approved_execution_image_digest=capability()["image_digest"],
        checked_by="admin", canonical_digest=main.canonical_digest,
    )
    workspace = root / runtime["sandbox_id"]
    metrics = {
        "schema_version": "pathmnist-aggregate-metrics/v1", "sample_count": 20,
        "accuracy": "0.95", "mean_confidence": "0.96",
        "confusion_matrix": [[0] * 9 for _ in range(9)],
        "prediction_digest": main.digest(b"predictions"),
    }
    summary = {
        "schema_version": "pathmnist-execution-summary/v1",
        "entrypoint_id": "pathmnist_resnet18_v1", "sample_count": 20,
        "processed_count": 20, "failed_count": 0, "correct_predictions": 19,
        "accuracy": "0.95", "mean_confidence": "0.96", "split": "test",
        "model_digest": main.digest(b"model"), "dataset_digest": main.digest(b"data"),
        "dataset_digest_after": main.digest(b"data"),
        "dataset_digest_unchanged": True, "model_digest_verified": True,
        "prediction_digest": main.digest(b"predictions"),
        "network_access": False, "inference_only": True, "non_clinical": True,
        "unexpected_output_count": 0, "resource_usage": {"hard_isolation": False},
    }
    contents = {
        "aggregate_metrics.json": json.dumps(metrics).encode(),
        "confusion_matrix.csv": b"expected/predicted,a\n",
        "execution_summary.json": json.dumps(summary).encode(),
    }
    manifest = []
    for filename, content in contents.items():
        (workspace / "output" / filename).write_bytes(content)
        manifest.append({
            "name": filename,
            "media_type": "text/csv" if filename.endswith(".csv") else "application/json",
            "size_bytes": len(content),
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        })
    request = json.loads((workspace / "runtime" / "request.json").read_text())
    result = {
        "schema_version": "phase5.13E-2B-1/worker-result/v1",
        "runtime_session_id": runtime["id"],
        "request_digest": request["request_digest"], "status": "completed",
        "started_at": main.now(), "completed_at": main.now(),
        "output_manifest": manifest,
    }
    result["result_digest"] = main.canonical_digest(result)
    (workspace / "runtime" / "result.json").write_text(json.dumps(result))
    completed = reconcile_fixed_reference_execution(
        db, runtime_session_id=runtime["id"], sandbox_root=root,
        canonical_digest=main.canonical_digest,
    )
    return db, root, workspace, completed["artifact_id"]


def test_scanner_and_independent_review_approve_exact_artifact(tmp_path: Path) -> None:
    db, root, _, artifact_id = scanned_artifact_fixture(tmp_path, "scan-pass")
    try:
        scan = scan_local_artifact(
            db, artifact_id=artifact_id, sandbox_root=root,
            canonical_digest=main.canonical_digest,
        )
        assert scan["decision"] == "passed"
        reviewer = db.execute(
            "SELECT id FROM local_users WHERE role='local_artifact_reviewer'"
        ).fetchone()["id"]
        review = review_local_artifact(
            db, artifact_id=artifact_id, reviewer_id=reviewer,
            decision="approved", reason="Exact aggregate output reviewed.",
            canonical_digest=main.canonical_digest,
        )
        assert review["status"] == "approved"
        with pytest.raises(ValueError, match="ARTIFACT_NOT_REVIEW_PENDING"):
            review_local_artifact(
                db, artifact_id=artifact_id, reviewer_id=reviewer,
                decision="rejected", reason="Cannot mutate final decision.",
                canonical_digest=main.canonical_digest,
            )
    finally:
        shutil.rmtree(root)


def test_scanner_rejects_forbidden_file_and_admin_cannot_review(
    tmp_path: Path,
) -> None:
    db, root, workspace, artifact_id = scanned_artifact_fixture(
        tmp_path, "scan-reject"
    )
    try:
        (workspace / "output" / "weights.pth").write_bytes(b"secret")
        scan = scan_local_artifact(
            db, artifact_id=artifact_id, sandbox_root=root,
            canonical_digest=main.canonical_digest,
        )
        assert scan["decision"] == "failed"
        admin = db.execute(
            "SELECT id FROM local_users WHERE role='connector_local_admin'"
        ).fetchone()["id"]
        with pytest.raises(ValueError, match="ARTIFACT_REVIEWER_ROLE_REQUIRED"):
            review_local_artifact(
                db, artifact_id=artifact_id, reviewer_id=admin,
                decision="approved", reason="Admin must not approve.",
                canonical_digest=main.canonical_digest,
            )
    finally:
        shutil.rmtree(root)
