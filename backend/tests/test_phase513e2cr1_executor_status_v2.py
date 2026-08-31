from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.connector_control import (
    ExecutorFixedExecutionReadinessAttestationV2,
)
from app.modules.audit import canonical_json_digest_v1
from app.modules.connector_control.models import (
    HospitalExecutorMirror,
    HospitalExecutorStatusEvent,
)
from app.modules.connector_control.services import (
    ConnectorControlError,
    EXECUTOR_READINESS_V2_EVENT,
    EXECUTOR_READINESS_V2_SCHEMA,
    get_verified_executor_readiness_source,
    validate_executor_readiness_v2_document,
)


def digest(label: str) -> str:
    return "sha256:" + __import__("hashlib").sha256(label.encode()).hexdigest()


def valid_payload() -> dict:
    now = datetime.now(timezone.utc)
    executor_id = "00000000-0000-4000-8000-000000000001"
    resources = {
        "cpu_cores": 2,
        "memory_mb": 2048,
        "disk_mb": 1024,
        "processes": 64,
        "timeout_seconds": 900,
    }
    resource_digest = canonical_json_digest_v1(resources)
    security_document = {
        "executor_id": executor_id,
        "security_version": "phase5.13E-1B/security-profile/v1",
        "network_mode": "none",
        "filesystem_mode": "readonly_input",
        "rootless": True,
        "privileged": False,
        "docker_socket_access": False,
        "runtime_download": False,
        "resource_policy": resources,
    }
    security_digest = canonical_json_digest_v1(security_document)
    checked_at = now - timedelta(minutes=1)
    valid_until = now + timedelta(minutes=30)
    image_manifest_digest = digest("image-manifest")
    image_digest = digest("image")
    capability_digest = digest("capability")
    admission_document = {
        "executor_id": executor_id,
        "security_profile_id": "security-profile-1",
        "image_manifest_id": "image-manifest-1",
        "image_manifest_digest": image_manifest_digest,
        "image_digest": image_digest,
        "security_profile_digest": security_digest,
        "resource_policy_digest": resource_digest,
        "capability_digest": capability_digest,
        "rejection_reasons": [],
        "execution_enabled": False,
        "checked_at": checked_at.isoformat(),
        "valid_until": valid_until.isoformat(),
    }
    return {
        "schema_version": EXECUTOR_READINESS_V2_SCHEMA,
        "event_type": EXECUTOR_READINESS_V2_EVENT,
        "connector_id": "00000000-0000-4000-8000-000000000002",
        "executor_id": executor_id,
        "executor_instance_id": "hex-00000000-0000-4000-8000-000000000001",
        "connector_certificate_fingerprint": digest("connector-certificate"),
        "executor_certificate_fingerprint": digest("executor-certificate"),
        "executor_status": "active",
        "executor_version": "0.1.0-alpha",
        "heartbeat_at": now - timedelta(seconds=10),
        "capability": {
            "local_object_id": "capability-1",
            "manifest_version": "1",
            "digest": capability_digest,
            "fixed_reference_execution_enabled": True,
            "supported_task_types": ["PATHMNIST_REFERENCE_V1"],
            "arbitrary_execution_enabled": False,
            "user_code_enabled": False,
            "user_model_enabled": False,
            "data_transfer_enabled": False,
            "model_transfer_enabled": False,
            "artifact_auto_egress_enabled": False,
            "hard_isolation": False,
        },
        "image_manifest": {
            "local_object_id": "image-manifest-1",
            "image_id": "pathmnist-reference-fixed",
            "image_digest": image_digest,
            "manifest_digest": image_manifest_digest,
            "lifecycle_status": "approved",
            "signature_status": "verified",
            "security_scan_status": "passed",
            "build_time": now - timedelta(days=1),
            "revoked_at": None,
        },
        "security_profile": {
            "local_object_id": "security-profile-1",
            "security_version": security_document["security_version"],
            "profile_digest": security_digest,
            "status": "valid",
            "network_mode": "none",
            "filesystem_mode": "readonly_input",
            "rootless": True,
            "privileged": False,
            "docker_socket_access": False,
            "runtime_download": False,
            "input_readonly": True,
            "capabilities_dropped": True,
            "created_at": now - timedelta(days=1),
        },
        "resource_policy": {
            "local_object_id": "security-profile-1:resource-policy",
            "policy_version": "phase5.13E-1B/security-profile/v1/resource",
            "policy_digest": resource_digest,
            "status": "active",
            **resources,
            "created_at": now - timedelta(days=1),
        },
        "admission": {
            "local_object_id": "admission-1",
            "admission_digest": canonical_json_digest_v1(admission_document),
            "result": "approved",
            "executor_id": executor_id,
            "image_manifest_digest": image_manifest_digest,
            "image_digest": image_digest,
            "security_profile_digest": security_digest,
            "resource_policy_digest": resource_digest,
            "capability_digest": capability_digest,
            "checked_at": checked_at,
            "valid_until": valid_until,
        },
        "readiness_result": "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION",
        "readiness_reason": None,
        "local_audit_head": digest("local-audit-head"),
        "local_state_revision": "phase5.13E_0008:audit-10",
        "event_sequence": 2,
        "nonce": "readiness-nonce-000000000000001",
        "generated_at": now,
        "not_before": now - timedelta(seconds=30),
        "expires_at": now + timedelta(minutes=15),
        "signing_key_id": "connector-signing-key-00000001",
        "payload_digest": digest("placeholder"),
        "signature": "c2lnbmF0dXJl" * 4,
    }


def set_path(document: dict, path: str, value) -> None:
    target = document
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        ("schema_version", "v1", "EXECUTOR_STATUS_SCHEMA_UNSUPPORTED"),
        ("event_type", "heartbeat", "EXECUTOR_STATUS_EVENT_TYPE_INVALID"),
        ("executor_status", "paused", "EXECUTOR_STATUS_INVALID"),
        ("heartbeat_at", None, "EXECUTOR_STATUS_TIMESTAMP_INVALID"),
        ("capability.fixed_reference_execution_enabled", False, "FIXED_REFERENCE_CAPABILITY_MISSING"),
        ("capability.arbitrary_execution_enabled", True, "ARBITRARY_EXECUTION_FORBIDDEN_STATE_INVALID"),
        ("capability.data_transfer_enabled", True, "DATA_TRANSFER_FORBIDDEN"),
        ("capability.model_transfer_enabled", True, "MODEL_TRANSFER_FORBIDDEN"),
        ("capability.artifact_auto_egress_enabled", True, "AUTO_EGRESS_FORBIDDEN"),
        ("capability.hard_isolation", True, "HARD_ISOLATION_CLAIM_INVALID"),
        ("image_manifest.lifecycle_status", "revoked", "IMAGE_MANIFEST_NOT_APPROVED"),
        ("image_manifest.revoked_at", datetime.now(timezone.utc), "IMAGE_MANIFEST_REVOKED"),
        ("image_manifest.signature_status", "invalid", "IMAGE_SIGNATURE_STATUS_INVALID"),
        ("image_manifest.security_scan_status", "failed", "IMAGE_SCAN_STATUS_INVALID"),
        ("security_profile.status", "invalid", "SECURITY_PROFILE_INVALID"),
        ("resource_policy.status", "invalid", "RESOURCE_POLICY_INVALID"),
        ("security_profile.network_mode", "bridge", "NETWORK_POLICY_INVALID"),
        ("security_profile.filesystem_mode", "writable", "SECURITY_PROFILE_INVALID"),
        ("security_profile.rootless", False, "ROOTLESS_REQUIRED"),
        ("security_profile.privileged", True, "PRIVILEGED_FORBIDDEN"),
        ("security_profile.docker_socket_access", True, "DOCKER_SOCKET_FORBIDDEN"),
        ("security_profile.runtime_download", True, "RUNTIME_DOWNLOAD_FORBIDDEN"),
        ("security_profile.input_readonly", False, "SECURITY_PROFILE_INVALID"),
        ("security_profile.capabilities_dropped", False, "SECURITY_PROFILE_INVALID"),
        ("admission.result", "rejected", "ADMISSION_NOT_APPROVED"),
        ("admission.executor_id", "00000000-0000-4000-8000-000000000099", "EXECUTOR_STATUS_IDENTITY_MISMATCH"),
        ("admission.image_digest", digest("wrong"), "ADMISSION_IMAGE_DIGEST_MISMATCH"),
        ("admission.security_profile_digest", digest("wrong"), "ADMISSION_SECURITY_DIGEST_MISMATCH"),
        ("admission.resource_policy_digest", digest("wrong"), "ADMISSION_RESOURCE_DIGEST_MISMATCH"),
        ("admission.capability_digest", digest("wrong"), "ADMISSION_CAPABILITY_DIGEST_MISMATCH"),
        ("local_audit_head", None, "LOCAL_AUDIT_HEAD_INVALID"),
        ("resource_policy.policy_digest", digest("wrong"), "ADMISSION_RESOURCE_DIGEST_MISMATCH"),
        ("security_profile.profile_digest", digest("wrong"), "ADMISSION_SECURITY_DIGEST_MISMATCH"),
        ("admission.admission_digest", digest("wrong"), "ADMISSION_DIGEST_MISMATCH"),
    ],
)
def test_central_rejects_invalid_v2_proof_bindings(
    path: str, value, reason: str,
) -> None:
    payload = valid_payload()
    set_path(payload, path, value)
    assert validate_executor_readiness_v2_document(
        payload, now=datetime.now(timezone.utc)
    ) == reason


def test_central_accepts_valid_v2_proof_document() -> None:
    payload = valid_payload()
    assert validate_executor_readiness_v2_document(
        payload, now=datetime.now(timezone.utc)
    ) is None


def test_v2_schema_forbids_additional_properties() -> None:
    payload = valid_payload()
    payload["caller_supplied_image_digest"] = digest("forged")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExecutorFixedExecutionReadinessAttestationV2(**payload)


def test_v1_cannot_be_a_fixed_readiness_source() -> None:
    assert HospitalExecutorStatusEvent.__table__.c.schema_version.nullable
    assert HospitalExecutorMirror.__table__.c.latest_verified_readiness_event_id.nullable
    assert EXECUTOR_READINESS_V2_SCHEMA != "phase5.13E-1A/executor-status/v1"


def test_payload_digest_detects_mutation() -> None:
    payload = ExecutorFixedExecutionReadinessAttestationV2(
        **valid_payload()
    ).model_dump(mode="json")
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"payload_digest", "signature"}
    }
    original = canonical_json_digest_v1(copy.deepcopy(unsigned))
    unsigned["event_sequence"] += 1
    assert canonical_json_digest_v1(unsigned) != original


def test_event_model_carries_append_only_verification_evidence() -> None:
    columns = HospitalExecutorStatusEvent.__table__.c
    assert {
        "schema_version", "nonce", "signing_key_id", "signature",
        "verification_status", "verification_reason", "verified_at",
    } <= set(columns.keys())
    assert columns.nonce.unique


class ReadinessSessionStub:
    def __init__(self, mirror) -> None:
        self.mirror = mirror

    async def get(self, _model, _identifier):
        return self.mirror


def test_expired_attestation_cannot_be_a_policy_compilation_source() -> None:
    event_id = uuid4()
    mirror = SimpleNamespace(
        status="active",
        fixed_reference_readiness_status="ready",
        latest_verified_readiness_event_id=event_id,
        latest_status_event_id=event_id,
        readiness_valid_until=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    async def exercise() -> None:
        with pytest.raises(
            ConnectorControlError,
            match="VERIFIED_EXECUTOR_READINESS_UNAVAILABLE",
        ):
            await get_verified_executor_readiness_source(
                ReadinessSessionStub(mirror),
                executor_mirror_id=uuid4(),
                task_type="PATHMNIST_REFERENCE_V1",
            )

    asyncio.run(exercise())


def test_superseded_attestation_cannot_be_a_policy_compilation_source() -> None:
    mirror = SimpleNamespace(
        status="active",
        fixed_reference_readiness_status="ready",
        latest_verified_readiness_event_id=uuid4(),
        latest_status_event_id=uuid4(),
        readiness_valid_until=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    async def exercise() -> None:
        with pytest.raises(
            ConnectorControlError,
            match="VERIFIED_EXECUTOR_READINESS_UNAVAILABLE",
        ):
            await get_verified_executor_readiness_source(
                ReadinessSessionStub(mirror),
                executor_mirror_id=uuid4(),
                task_type="PATHMNIST_REFERENCE_V1",
            )

    asyncio.run(exercise())
