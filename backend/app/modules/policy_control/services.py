from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor
from app.modules.applications.models import Application, ApplicationSnapshot
from app.modules.audit import canonical_json_digest_v1, canonical_json_text_v1
from app.modules.connector_control.models import (
    ConnectorAssetMirror, ConnectorAssetMirrorVersion, ConnectorCapabilityManifest,
    ConnectorCertificate, HospitalConnector, HospitalExecutorMirror,
    HospitalExecutorStatusEvent,
)
from app.modules.connector_control.services import (
    append_control_audit,
    get_verified_executor_readiness_source,
    sha256_bytes,
)
from app.modules.contracts.models import Contract, ContractRevision
from app.modules.marketplace.models import ModelVersion
from app.modules.policy_control.models import (
    ConnectorOrderDecision, ConnectorOrderReceipt, ControlReadinessSnapshot,
    ExecutionOrder, ExecutionOrderConsumptionReceipt, PolicyBundle,
    PolicyBundleVersion, PolicyRevocation, PolicySigningKey,
)


class PolicyControlError(ValueError):
    pass


def fixed_reference_authorization_ttl_seconds() -> int:
    value = int(
        os.getenv(
            "MEDTRUST_FIXED_REFERENCE_AUTHORIZATION_TTL_SECONDS", "3600"
        )
    )
    if value < 1200:
        raise PolicyControlError("FIXED_REFERENCE_AUTHORIZATION_TTL_INVALID")
    return value


def fixed_reference_minimum_validity_seconds(
    runtime_timeout_seconds: int,
) -> int:
    margin = int(
        os.getenv(
            "MEDTRUST_FIXED_REFERENCE_AUTHORIZATION_SAFETY_MARGIN_SECONDS",
            "300",
        )
    )
    if margin < 0:
        raise PolicyControlError("FIXED_REFERENCE_SAFETY_MARGIN_INVALID")
    return runtime_timeout_seconds + margin


FIXED_TASK_TYPE = "PATHMNIST_REFERENCE_V1"
FIXED_MODEL_DIGEST = (
    "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
)
FIXED_ALLOWED_OUTPUTS = [
    "aggregate_metrics.json",
    "confusion_matrix.csv",
    "execution_summary.json",
]
FIXED_TASK_DEFINITION = {
    "task_type": FIXED_TASK_TYPE,
    "task_version": "1",
    "sample_count": 20,
    "dataset": "PathMNIST",
    "model": "fixed ResNet-18 reference",
}
FIXED_INPUT_SCHEMA = {
    "schema_version": "phase5.13E/pathmnist-input/v1",
    "sample_indices": list(range(20)),
    "raw_data_transfer": False,
}
FIXED_OUTPUT_SCHEMA = {
    "schema_version": "phase5.13E/pathmnist-output/v1",
    "allowed_files": FIXED_ALLOWED_OUTPUTS,
    "artifact_auto_egress": False,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _operator(actor: DemoActor) -> None:
    if actor.role != "space_operator":
        raise PolicyControlError("only the platform operator may perform this action")


def _private_root() -> Path:
    root = Path(os.getenv("MEDTRUST_POLICY_SIGNING_ROOT", "/var/lib/medtrust/policy-signing"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _private_path(key_id: str) -> Path:
    return _private_root() / f"{key_id}.private.pem"


def _sign(key_id: str, payload: dict[str, Any]) -> str:
    with tempfile.TemporaryDirectory() as root:
        message = Path(root) / "message.bin"
        signature = Path(root) / "signature.bin"
        message.write_bytes(canonical_json_text_v1(payload).encode("utf-8"))
        result = subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(_private_path(key_id)),
             "-in", str(message), "-out", str(signature)],
            capture_output=True, check=False,
        )
        if result.returncode:
            raise PolicyControlError("POLICY_SIGNING_KEY_INVALID")
        return base64.b64encode(signature.read_bytes()).decode("ascii")


def verify_ed25519(public_pem: str, payload: dict[str, Any], signature: str) -> None:
    with tempfile.TemporaryDirectory() as root:
        public = Path(root) / "public.pem"
        message = Path(root) / "message.bin"
        signed = Path(root) / "signature.bin"
        public.write_text(public_pem, encoding="ascii")
        message.write_bytes(canonical_json_text_v1(payload).encode("utf-8"))
        try:
            signed.write_bytes(base64.b64decode(signature, validate=True))
        except Exception as exc:
            raise PolicyControlError("SIGNATURE_INVALID") from exc
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(public),
             "-in", str(message), "-sigfile", str(signed)],
            capture_output=True, check=False,
        )
        if result.returncode:
            raise PolicyControlError("SIGNATURE_INVALID")


async def ensure_active_signing_key(
    session: AsyncSession, *, actor: DemoActor, space_id: UUID
) -> PolicySigningKey:
    _operator(actor)
    current = await session.scalar(
        select(PolicySigningKey).where(
            PolicySigningKey.space_id == space_id, PolicySigningKey.status == "active"
        )
    )
    if current:
        if not _private_path(current.key_id).is_file():
            raise PolicyControlError("POLICY_PRIVATE_KEY_MISSING")
        return current
    key_id = f"phase513d-{secrets.token_hex(8)}"
    path = _private_path(key_id)
    generated = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(path)],
        capture_output=True, check=False,
    )
    if generated.returncode:
        raise PolicyControlError("POLICY_SIGNING_KEY_GENERATION_FAILED")
    public_result = subprocess.run(
        ["openssl", "pkey", "-in", str(path), "-pubout"],
        capture_output=True, check=False,
    )
    if public_result.returncode:
        raise PolicyControlError("POLICY_PUBLIC_KEY_EXPORT_FAILED")
    public_pem = public_result.stdout
    os.chmod(path, 0o600)
    stamp = utc_now()
    row = PolicySigningKey(
        space_id=space_id, key_id=key_id, algorithm="Ed25519",
        public_key_fingerprint=sha256_bytes(public_pem),
        public_key_material=public_pem.decode("ascii"), status="active",
        valid_from=stamp, valid_to=stamp + timedelta(days=30),
        activated_at=stamp,
    )
    session.add(row)
    await session.flush()
    await append_control_audit(
        session, space_id=space_id, event_type="policy.signing_key.activated",
        subject_type="policy_signing_key", subject_id=row.id,
        evidence={"key_id": key_id, "algorithm": "Ed25519", "fingerprint": row.public_key_fingerprint},
        actor=actor,
    )
    return row


async def create_fixed_execution_readiness(
    session: AsyncSession,
    *,
    actor: DemoActor,
    space_id: UUID,
    connector_id: UUID,
    executor_mirror_id: UUID,
    application_id: UUID,
    contract_id: UUID,
    asset_version_id: UUID,
    model_version_id: UUID,
) -> tuple[ControlReadinessSnapshot, dict[str, Any]]:
    _operator(actor)
    now = utc_now()
    connector = await session.get(HospitalConnector, connector_id)
    executor = await session.get(HospitalExecutorMirror, executor_mirror_id)
    application = await session.get(Application, application_id)
    contract = await session.get(Contract, contract_id)
    asset_version = await session.get(
        ConnectorAssetMirrorVersion, asset_version_id
    )
    model_version = await session.get(ModelVersion, model_version_id)
    if not all(
        (
            connector,
            executor,
            application,
            contract,
            asset_version,
            model_version,
        )
    ):
        raise PolicyControlError("EXECUTION_READINESS_SOURCE_NOT_FOUND")
    asset = await session.get(ConnectorAssetMirror, asset_version.mirror_id)
    revision = await session.scalar(
        select(ContractRevision).where(
            ContractRevision.contract_id == contract.id,
            ContractRevision.status == "active",
        )
    )
    application_snapshot = await session.scalar(
        select(ApplicationSnapshot).where(
            ApplicationSnapshot.application_id == application.id
        )
    )
    certificate = await session.get(
        ConnectorCertificate, connector.current_certificate_id
    )
    try:
        executor_source = await get_verified_executor_readiness_source(
            session,
            executor_mirror_id=executor_mirror_id,
            task_type=FIXED_TASK_TYPE,
        )
    except Exception as exc:
        raise PolicyControlError(
            "VERIFIED_EXECUTOR_READINESS_UNAVAILABLE"
        ) from exc
    status_event = await session.get(
        HospitalExecutorStatusEvent,
        UUID(executor_source["source_executor_status_event_id"]),
    )
    proof = status_event.payload_snapshot if status_event else {}
    checks = [
        ("application_approved", application.status == "approved"),
        (
            "application_snapshot_frozen",
            application_snapshot is not None
            and bool(application_snapshot.snapshot_digest),
        ),
        (
            "contract_active",
            revision is not None
            and revision.effective_from is not None
            and revision.effective_from <= now
            and (
                revision.effective_until is None
                or revision.effective_until > now
            ),
        ),
        (
            "application_contract_match",
            contract.application_id == application.id
            and application_snapshot is not None
            and contract.application_snapshot_id == application_snapshot.id
            and contract.application_snapshot_digest
            == application_snapshot.snapshot_digest,
        ),
        (
            "contract_digest_frozen",
            revision is not None and bool(revision.content_digest),
        ),
        ("connector_active", connector.status == "active"),
        ("connector_heartbeat_present", connector.last_heartbeat_at is not None),
        ("connector_not_paused", connector.paused_at is None),
        ("connector_not_revoked", connector.revoked_at is None),
        (
            "connector_certificate_active",
            certificate is not None
            and certificate.status == "active"
            and certificate.valid_from <= now < certificate.valid_to,
        ),
        (
            "executor_connector_match",
            executor.connector_id == connector.id
            and executor.space_id == space_id,
        ),
        ("executor_active", executor.status == "active"),
        (
            "status_v2_current",
            status_event is not None
            and executor.latest_status_event_id == status_event.id
            and status_event.verification_status == "verified",
        ),
        (
            "status_v2_ready",
            proof.get("readiness_result")
            == "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION",
        ),
        (
            "asset_version_current",
            asset is not None
            and asset.status == "synced"
            and asset.connector_id == connector.id
            and asset.current_version_id == asset_version.id,
        ),
        (
            "asset_provider_match",
            asset is not None
            and asset.organization_id == connector.organization_id
            and asset.organization_id == application.provider_organization_id,
        ),
        (
            "asset_metadata_digest_present",
            bool(asset_version.metadata_digest),
        ),
        ("quality_digest_present", bool(asset_version.quality_digest)),
        (
            "model_reference_present",
            bool(model_version.model_digest),
        ),
        (
            "fixed_model_reference",
            model_version.model_digest == FIXED_MODEL_DIGEST
            and application.algorithm_digest == FIXED_MODEL_DIGEST,
        ),
        (
            "fixed_reference_security",
            proof.get("capability", {}).get(
                "fixed_reference_execution_enabled"
            )
            is True
            and FIXED_TASK_TYPE
            in proof.get("capability", {}).get("supported_task_types", [])
            and proof.get("capability", {}).get(
                "arbitrary_execution_enabled"
            )
            is False
            and proof.get("capability", {}).get("user_code_enabled") is False
            and proof.get("capability", {}).get("user_model_enabled") is False
            and proof.get("capability", {}).get("data_transfer_enabled")
            is False
            and proof.get("capability", {}).get("model_transfer_enabled")
            is False
            and proof.get("capability", {}).get(
                "artifact_auto_egress_enabled"
            )
            is False
            and proof.get("capability", {}).get("hard_isolation") is False,
        ),
    ]
    failed = [code for code, passed in checks if not passed]
    if failed:
        raise PolicyControlError(
            "EXECUTION_READINESS_BLOCKED:" + ",".join(failed)
        )
    candidates = [
        executor_source["source_attestation_expires_at"],
        certificate.valid_to,
        now + timedelta(seconds=fixed_reference_authorization_ttl_seconds()),
    ]
    if revision.effective_until is not None:
        candidates.append(revision.effective_until)
    expires_at = min(candidates)
    if expires_at <= now:
        raise PolicyControlError("EXECUTION_READINESS_EXPIRED")
    application_digest = application_snapshot.snapshot_digest
    contract_digest = revision.content_digest
    model_reference_digest = model_version.model_digest
    readiness_body = {
        "schema_version": "phase5.13E-2C-R1/execution-readiness/v1",
        "readiness_mode": "FIXED_REFERENCE_EXECUTION",
        "requested_action": "EXECUTE_FIXED_REFERENCE_TASK",
        "task_type": FIXED_TASK_TYPE,
        "connector_id": str(connector.id),
        "executor_mirror_id": str(executor.id),
        "application_id": str(application.id),
        "application_digest": application_digest,
        "contract_id": str(contract.id),
        "contract_revision_id": str(revision.id),
        "contract_digest": contract_digest,
        "asset_version_id": str(asset_version.id),
        "asset_metadata_digest": asset_version.metadata_digest,
        "quality_digest": asset_version.quality_digest,
        "model_version_id": str(model_version.id),
        "model_reference_digest": model_reference_digest,
        "source_executor_status_event_id": executor_source[
            "source_executor_status_event_id"
        ],
        "source_executor_status_event_digest": executor_source[
            "source_executor_status_event_digest"
        ],
        "source_attestation_expires_at": executor_source[
            "source_attestation_expires_at"
        ].isoformat(),
        "checks": [
            {"code": code, "passed": passed} for code, passed in checks
        ],
        "computed_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "execution_authorized": True,
        "hard_isolation": False,
    }
    row = ControlReadinessSnapshot(
        space_id=space_id,
        connector_id=connector.id,
        application_id=application.id,
        contract_id=contract.id,
        contract_revision_id=revision.id,
        central_asset_record_id=asset.id,
        central_asset_version_id=asset_version.id,
        model_product_version_id=model_version.id,
        readiness_mode="FIXED_REFERENCE_EXECUTION",
        requested_action="EXECUTE_FIXED_REFERENCE_TASK",
        task_type=FIXED_TASK_TYPE,
        source_executor_status_event_id=status_event.id,
        source_executor_status_event_digest=status_event.payload_digest,
        source_attestation_expires_at=executor_source[
            "source_attestation_expires_at"
        ],
        source_asset_version_id=asset_version.id,
        source_asset_metadata_digest=asset_version.metadata_digest,
        source_quality_digest=asset_version.quality_digest,
        source_model_reference_digest=model_reference_digest,
        source_contract_digest=contract_digest,
        source_application_digest=application_digest,
        checks=readiness_body["checks"],
        status="passed",
        readiness_digest=canonical_json_digest_v1(readiness_body),
        execution_authorized=True,
        hard_isolation=False,
        expires_at=expires_at,
        computed_at=now,
        created_by=actor.user_id,
    )
    session.add(row)
    await session.flush()
    await append_control_audit(
        session,
        space_id=space_id,
        event_type="execution.readiness.fixed_reference.created",
        subject_type="control_readiness_snapshot",
        subject_id=row.id,
        evidence={
            "readiness_digest": row.readiness_digest,
            "source_executor_status_event_id": str(status_event.id),
            "source_executor_status_event_digest": status_event.payload_digest,
            "expires_at": expires_at.isoformat(),
            "execution_authorized": True,
            "execution_started": False,
            "hard_isolation": False,
        },
        actor=actor,
    )
    return row, {
        "connector": connector,
        "executor": executor,
        "application": application,
        "application_snapshot": application_snapshot,
        "contract": contract,
        "revision": revision,
        "asset": asset,
        "asset_version": asset_version,
        "model_version": model_version,
        "status_event": status_event,
        "proof": proof,
        "executor_source": executor_source,
    }


async def compile_fixed_execution_policy(
    session: AsyncSession,
    *,
    actor: DemoActor,
    space_id: UUID,
    connector_id: UUID,
    executor_mirror_id: UUID,
    application_id: UUID,
    contract_id: UUID,
    asset_version_id: UUID,
    model_version_id: UUID,
    purpose_code: str,
) -> tuple[PolicyBundle, PolicyBundleVersion, ControlReadinessSnapshot]:
    readiness, source = await create_fixed_execution_readiness(
        session,
        actor=actor,
        space_id=space_id,
        connector_id=connector_id,
        executor_mirror_id=executor_mirror_id,
        application_id=application_id,
        contract_id=contract_id,
        asset_version_id=asset_version_id,
        model_version_id=model_version_id,
    )
    now = utc_now()
    runtime_timeout_seconds = int(
        source["proof"]["resource_policy"]["timeout_seconds"]
    )
    minimum_validity = fixed_reference_minimum_validity_seconds(
        runtime_timeout_seconds
    )
    if (readiness.expires_at - now).total_seconds() < minimum_validity:
        raise PolicyControlError("POLICY_VALIDITY_TOO_SHORT")
    key = await ensure_active_signing_key(
        session, actor=actor, space_id=space_id
    )
    proof = source["proof"]
    status_event = source["status_event"]
    asset = source["asset"]
    asset_version = source["asset_version"]
    model_version = source["model_version"]
    revision = source["revision"]
    application = source["application"]
    application_snapshot = source["application_snapshot"]
    executor = source["executor"]
    payload = {
        "schema_version": "phase5.13E-2C-R1/policy-bundle/v1",
        "connector_id": str(connector_id),
        "organization_id": str(source["connector"].organization_id),
        "executor_mirror_id": str(executor.id),
        "executor_id": executor.local_executor_id,
        "application_id": str(application.id),
        "application_snapshot_digest": application_snapshot.snapshot_digest,
        "contract_id": str(source["contract"].id),
        "contract_revision_id": str(revision.id),
        "contract_digest": revision.content_digest,
        "control_readiness_id": str(readiness.id),
        "readiness_digest": readiness.readiness_digest,
        "source_executor_status_event_id": str(status_event.id),
        "source_executor_status_event_digest": status_event.payload_digest,
        "source_attestation_expires_at": readiness.source_attestation_expires_at.isoformat(),
        "central_asset_record_id": str(asset.id),
        "central_asset_version_id": str(asset_version.id),
        "local_asset_key": asset.local_asset_key,
        "local_asset_version_ref": asset_version.version_label,
        "local_asset_metadata_digest": asset_version.metadata_digest,
        "quality_digest": asset_version.quality_digest,
        "model_product_version_id": str(model_version.id),
        "model_reference_digest": readiness.source_model_reference_digest,
        "model_materialization_status": "FIXED_REFERENCE_ONLY",
        "attested_image_manifest_id": proof["image_manifest"]["local_object_id"],
        "attested_image_manifest_digest": proof["image_manifest"]["manifest_digest"],
        "image_digest": proof["image_manifest"]["image_digest"],
        "attested_security_profile_id": proof["security_profile"]["local_object_id"],
        "security_profile_digest": proof["security_profile"]["profile_digest"],
        "attested_resource_policy_id": proof["resource_policy"]["local_object_id"],
        "resource_policy_digest": proof["resource_policy"]["policy_digest"],
        "attested_admission_check_id": proof["admission"]["local_object_id"],
        "admission_digest": proof["admission"]["admission_digest"],
        "capability_digest": proof["capability"]["digest"],
        "purpose_code": purpose_code,
        "purpose_summary": application.purpose,
        "requested_action": "EXECUTE_FIXED_REFERENCE_TASK",
        "execution_scope": "FIXED_REFERENCE_ONLY",
        "task_type": FIXED_TASK_TYPE,
        "max_execution_count": 1,
        "task_definition_digest": canonical_json_digest_v1(
            FIXED_TASK_DEFINITION
        ),
        "runtime_timeout_seconds": runtime_timeout_seconds,
        "minimum_remaining_validity_seconds": minimum_validity,
        "input_schema_digest": canonical_json_digest_v1(FIXED_INPUT_SCHEMA),
        "output_schema_digest": canonical_json_digest_v1(FIXED_OUTPUT_SCHEMA),
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
            "allowed_files": FIXED_ALLOWED_OUTPUTS,
            "auto_egress": False,
        },
        "review_policy": {
            "local_policy_reviewer_required": True,
            "central_override": False,
        },
        "execution_authorized": True,
        "hard_isolation": False,
        "issued_at": now.isoformat(),
        "not_before": now.isoformat(),
        "expires_at": readiness.expires_at.isoformat(),
        "nonce": secrets.token_urlsafe(32),
        "signing_key_id": key.key_id,
    }
    bundle = PolicyBundle(
        policy_key=f"POL-FIXED-{secrets.token_hex(8)}",
        space_id=space_id,
        organization_id=source["connector"].organization_id,
        connector_id=connector_id,
        application_id=application_id,
        contract_id=contract_id,
        control_readiness_id=readiness.id,
        status="compiled",
        created_by=actor.user_id,
        expires_at=readiness.expires_at,
    )
    session.add(bundle)
    await session.flush()
    version = PolicyBundleVersion(
        policy_bundle_id=bundle.id,
        schema_version=payload["schema_version"],
        version=1,
        connector_id=connector_id,
        central_asset_version_id=asset_version_id,
        model_product_version_id=model_version_id,
        requested_action="EXECUTE_FIXED_REFERENCE_TASK",
        execution_authorized=True,
        execution_scope="FIXED_REFERENCE_ONLY",
        task_type=FIXED_TASK_TYPE,
        max_execution_count=1,
        canonical_payload=payload,
        payload_digest=canonical_json_digest_v1(payload),
        signature="",
        signing_key_id=key.key_id,
        issued_at=now,
        not_before=now,
        expires_at=readiness.expires_at,
        nonce=payload["nonce"],
        signed_at=now,
    )
    session.add(version)
    await session.flush()
    bundle.current_version_id = version.id
    await append_control_audit(
        session,
        space_id=space_id,
        event_type="policy.bundle.fixed_reference.compiled",
        subject_type="policy_bundle",
        subject_id=bundle.id,
        evidence={
            "version_id": str(version.id),
            "digest": version.payload_digest,
            "readiness_digest": readiness.readiness_digest,
            "execution_authorized": True,
            "execution_started": False,
            "hard_isolation": False,
        },
        actor=actor,
    )
    return bundle, version, readiness


async def compile_policy(
    session: AsyncSession, *, actor: DemoActor, space_id: UUID,
    connector_id: UUID, application_id: UUID, contract_id: UUID,
    asset_version_id: UUID, model_version_id: UUID, purpose_code: str,
    executor_mirror_id: UUID | None = None,
    execution_mode: str = "CONTROL_POLICY_VALIDATION",
) -> tuple[PolicyBundle, PolicyBundleVersion, ControlReadinessSnapshot]:
    if execution_mode == "FIXED_REFERENCE_EXECUTION":
        if executor_mirror_id is None:
            raise PolicyControlError("EXECUTOR_MIRROR_REQUIRED")
        return await compile_fixed_execution_policy(
            session,
            actor=actor,
            space_id=space_id,
            connector_id=connector_id,
            executor_mirror_id=executor_mirror_id,
            application_id=application_id,
            contract_id=contract_id,
            asset_version_id=asset_version_id,
            model_version_id=model_version_id,
            purpose_code=purpose_code,
        )
    if execution_mode != "CONTROL_POLICY_VALIDATION":
        raise PolicyControlError("POLICY_MODE_INVALID")
    _operator(actor)
    connector = await session.get(HospitalConnector, connector_id)
    application = await session.get(Application, application_id)
    contract = await session.get(Contract, contract_id)
    asset_version = await session.get(ConnectorAssetMirrorVersion, asset_version_id)
    model_version = await session.get(ModelVersion, model_version_id)
    if not all((connector, application, contract, asset_version, model_version)):
        raise PolicyControlError("POLICY_SOURCE_NOT_FOUND")
    mirror = await session.get(ConnectorAssetMirror, asset_version.mirror_id)
    revision = await session.scalar(
        select(ContractRevision).where(
            ContractRevision.contract_id == contract.id,
            ContractRevision.status == "active",
        )
    )
    snapshot = await session.scalar(
        select(ApplicationSnapshot).where(ApplicationSnapshot.application_id == application.id)
    )
    manifest = await session.get(ConnectorCapabilityManifest, connector.current_capability_manifest_id)
    checks = [
        ("application_approved", application.status == "approved"),
        ("contract_active", revision is not None),
        ("application_contract_match", contract.application_id == application.id),
        ("connector_active", connector.status == "active"),
        ("heartbeat_present", connector.last_heartbeat_at is not None),
        ("connector_not_paused", connector.paused_at is None),
        ("connector_not_revoked", connector.revoked_at is None),
        ("asset_mirror_synced", mirror is not None and mirror.status == "synced"),
        ("asset_connector_match", mirror is not None and mirror.connector_id == connector.id),
        ("quality_profile_present", bool(asset_version.quality_digest)),
        ("model_metadata_reference_present", bool(model_version.snapshot_digest or model_version.model_digest)),
        ("policy_schema_supported", manifest is not None and manifest.metadata_sync_enabled),
        ("metadata_only_validation_supported", manifest is not None and manifest.local_asset_registry_enabled),
        ("execution_disabled", manifest is not None and not manifest.execution_enabled),
        ("hard_isolation_false", manifest is not None and not manifest.hard_isolation),
    ]
    check_rows = [{"code": code, "passed": passed} for code, passed in checks]
    readiness_body = {
        "schema_version": "phase5.13D/control-readiness/v1",
        "readiness_mode": "CONTROL_POLICY_VALIDATION",
        "connector_id": str(connector.id), "application_id": str(application.id),
        "contract_id": str(contract.id), "contract_revision_id": str(revision.id) if revision else None,
        "asset_version_id": str(asset_version.id), "model_version_id": str(model_version.id),
        "checks": check_rows, "execution_authorized": False, "hard_isolation": False,
    }
    readiness = ControlReadinessSnapshot(
        space_id=space_id, connector_id=connector.id, application_id=application.id,
        contract_id=contract.id, contract_revision_id=revision.id if revision else UUID(int=0),
        central_asset_record_id=mirror.id if mirror else UUID(int=0),
        central_asset_version_id=asset_version.id, model_product_version_id=model_version.id,
        checks=check_rows, status="passed" if all(value for _, value in checks) else "blocked",
        readiness_digest=canonical_json_digest_v1(readiness_body),
        execution_authorized=False, hard_isolation=False, created_by=actor.user_id,
    )
    if readiness.status != "passed":
        raise PolicyControlError("CONTROL_READINESS_BLOCKED:" + ",".join(code for code, value in checks if not value))
    key = await ensure_active_signing_key(session, actor=actor, space_id=space_id)
    stamp = utc_now()
    session.add(readiness)
    await session.flush()
    payload = {
        "schema_version": "phase5.13D/policy-bundle/v1",
        "connector_id": str(connector.id), "organization_id": str(connector.organization_id),
        "application_id": str(application.id),
        "application_snapshot_digest": snapshot.snapshot_digest if snapshot else None,
        "contract_id": str(contract.id), "contract_revision_id": str(revision.id),
        "contract_digest": revision.content_digest,
        "control_readiness_id": str(readiness.id), "readiness_digest": readiness.readiness_digest,
        "central_asset_record_id": str(mirror.id), "central_asset_version_id": str(asset_version.id),
        "local_asset_key": mirror.local_asset_key, "local_asset_version_ref": asset_version.version_label,
        "local_asset_metadata_digest": asset_version.metadata_digest,
        "quality_digest": asset_version.quality_digest,
        "model_product_version_id": str(model_version.id),
        "model_reference_digest": model_version.snapshot_digest or model_version.model_digest,
        "model_materialization_status": "NOT_EVALUATED_IN_PHASE_5_13D",
        "purpose_code": purpose_code, "purpose_summary": application.purpose,
        "requested_action": "VALIDATE_POLICY_ONLY",
        "allowed_operations": ["VERIFY_SIGNATURE", "VERIFY_DIGEST", "LOCAL_POLICY_REVIEW"],
        "forbidden_operations": ["READ_RAW_DATA", "LOAD_MODEL", "EXECUTE", "CREATE_ARTIFACT", "EGRESS"],
        "network_policy": {"central_channel": "mTLS_pull_only", "data_transfer": False},
        "filesystem_policy": {"local_path_access": False},
        "resource_policy": {"executor_registration": False},
        "output_policy": {"artifact_creation": False},
        "retention_policy": {"order_metadata_only": True},
        "review_policy": {"local_policy_reviewer_required": True, "central_override": False},
        "revocation_policy": {"blocks_future_execution": True},
        "execution_authorized": False, "hard_isolation": False,
        "issued_at": stamp.isoformat(), "not_before": stamp.isoformat(),
        "expires_at": (stamp + timedelta(hours=4)).isoformat(),
        "nonce": secrets.token_urlsafe(32), "signing_key_id": key.key_id,
    }
    bundle = PolicyBundle(
        policy_key=f"POL-{secrets.token_hex(8)}", space_id=space_id,
        organization_id=connector.organization_id, connector_id=connector.id,
        application_id=application.id, contract_id=contract.id,
        control_readiness_id=readiness.id, status="compiled",
        created_by=actor.user_id, expires_at=stamp + timedelta(hours=4),
    )
    session.add(bundle)
    await session.flush()
    version = PolicyBundleVersion(
        policy_bundle_id=bundle.id, schema_version=payload["schema_version"], version=1,
        connector_id=connector.id, central_asset_version_id=asset_version.id,
        model_product_version_id=model_version.id, requested_action="VALIDATE_POLICY_ONLY",
        execution_authorized=False, canonical_payload=payload,
        payload_digest=canonical_json_digest_v1(payload), signature="",
        signing_key_id=key.key_id, issued_at=stamp, not_before=stamp,
        expires_at=stamp + timedelta(hours=4), nonce=payload["nonce"],
        signed_at=stamp,
    )
    session.add(version)
    await session.flush()
    bundle.current_version_id = version.id
    await append_control_audit(
        session, space_id=space_id, event_type="policy.bundle.compiled",
        subject_type="policy_bundle", subject_id=bundle.id,
        evidence={"version_id": str(version.id), "digest": version.payload_digest, "execution_authorized": False},
        actor=actor,
    )
    return bundle, version, readiness


async def sign_activate_policy(
    session: AsyncSession, *, actor: DemoActor, space_id: UUID, bundle_id: UUID
) -> tuple[PolicyBundle, PolicyBundleVersion]:
    _operator(actor)
    bundle = await session.get(PolicyBundle, bundle_id, with_for_update=True)
    if bundle is None or bundle.space_id != space_id or bundle.status not in {"compiled", "signed"}:
        raise PolicyControlError("POLICY_NOT_SIGNABLE")
    version = await session.get(PolicyBundleVersion, bundle.current_version_id, with_for_update=True)
    key = await session.scalar(select(PolicySigningKey).where(PolicySigningKey.key_id == version.signing_key_id))
    if key is None or key.status != "active" or key.valid_to <= utc_now():
        raise PolicyControlError("POLICY_SIGNING_KEY_NOT_ACTIVE")
    if canonical_json_digest_v1(version.canonical_payload) != version.payload_digest:
        raise PolicyControlError("POLICY_DIGEST_MISMATCH")
    if version.execution_authorized:
        readiness = await session.get(
            ControlReadinessSnapshot, bundle.control_readiness_id
        )
        minimum_validity = fixed_reference_minimum_validity_seconds(
            int(version.canonical_payload["runtime_timeout_seconds"])
        )
        if (
            readiness is None
            or readiness.readiness_mode != "FIXED_REFERENCE_EXECUTION"
            or readiness.status != "passed"
            or readiness.expires_at is None
            or readiness.expires_at <= utc_now() + timedelta(
                seconds=minimum_validity
            )
            or readiness.readiness_digest
            != version.canonical_payload.get("readiness_digest")
            or str(readiness.source_executor_status_event_id)
            != version.canonical_payload.get(
                "source_executor_status_event_id"
            )
        ):
            raise PolicyControlError("EXECUTION_READINESS_NOT_CURRENT")
        executor_id = UUID(version.canonical_payload["executor_mirror_id"])
        try:
            source = await get_verified_executor_readiness_source(
                session,
                executor_mirror_id=executor_id,
                task_type=FIXED_TASK_TYPE,
            )
        except Exception as exc:
            raise PolicyControlError(
                "VERIFIED_EXECUTOR_READINESS_UNAVAILABLE"
            ) from exc
        if (
            source["source_executor_status_event_digest"]
            != readiness.source_executor_status_event_digest
        ):
            raise PolicyControlError("EXECUTOR_STATUS_SUPERSEDED")
    version.signature = _sign(key.key_id, version.canonical_payload)
    verify_ed25519(key.public_key_material, version.canonical_payload, version.signature)
    bundle.status = "active"
    bundle.activated_at = utc_now()
    await append_control_audit(
        session, space_id=space_id, event_type="policy.bundle.activated",
        subject_type="policy_bundle", subject_id=bundle.id,
        evidence={"version_id": str(version.id), "digest": version.payload_digest, "key_id": key.key_id},
        actor=actor,
    )
    return bundle, version


async def issue_order(
    session: AsyncSession, *, actor: DemoActor, space_id: UUID, bundle_id: UUID,
    idempotency_key: str,
) -> ExecutionOrder:
    _operator(actor)
    existing = await session.scalar(select(ExecutionOrder).where(ExecutionOrder.idempotency_key == idempotency_key))
    if existing:
        return existing
    bundle = await session.get(PolicyBundle, bundle_id)
    if bundle is None or bundle.space_id != space_id or bundle.status != "active":
        raise PolicyControlError("ACTIVE_POLICY_REQUIRED")
    version = await session.get(PolicyBundleVersion, bundle.current_version_id)
    if not version.signature:
        raise PolicyControlError("SIGNED_POLICY_REQUIRED")
    key = await session.scalar(select(PolicySigningKey).where(PolicySigningKey.key_id == version.signing_key_id))
    if key is None or key.status != "active" or key.valid_to <= utc_now():
        raise PolicyControlError("POLICY_SIGNING_KEY_NOT_ACTIVE")
    sequence = (await session.scalar(
        select(func.max(ExecutionOrder.connector_sequence)).where(ExecutionOrder.connector_id == bundle.connector_id)
    ) or 0) + 1
    stamp = utc_now()
    order_id = uuid4()
    fixed = bool(version.execution_authorized)
    commercial_entitlement: dict[str, Any] | None = None
    if fixed:
        from app.modules.commerce.gating import (
            CommercialExecutionBlocked,
            require_paid_execution_entitlement,
        )

        try:
            commercial_entitlement = await require_paid_execution_entitlement(
                session, contract_id=bundle.contract_id
            )
        except CommercialExecutionBlocked as exc:
            raise PolicyControlError(
                f"COMMERCIAL_EXECUTION_BLOCKED:{exc}"
            ) from exc
        readiness = await session.get(
            ControlReadinessSnapshot, bundle.control_readiness_id
        )
        if (
            readiness is None
            or readiness.readiness_mode != "FIXED_REFERENCE_EXECUTION"
            or readiness.status != "passed"
            or readiness.expires_at is None
        ):
            raise PolicyControlError("EXECUTION_READINESS_NOT_CURRENT")
        minimum_validity = fixed_reference_minimum_validity_seconds(
            int(version.canonical_payload["runtime_timeout_seconds"])
        )
        expires_at = min(version.expires_at, readiness.expires_at)
        if expires_at <= stamp + timedelta(seconds=minimum_validity):
            raise PolicyControlError("ORDER_VALIDITY_TOO_SHORT")
        executor_id = UUID(version.canonical_payload["executor_mirror_id"])
        try:
            source = await get_verified_executor_readiness_source(
                session,
                executor_mirror_id=executor_id,
                task_type=FIXED_TASK_TYPE,
            )
        except Exception as exc:
            raise PolicyControlError(
                "VERIFIED_EXECUTOR_READINESS_UNAVAILABLE"
            ) from exc
        if (
            source["source_executor_status_event_digest"]
            != readiness.source_executor_status_event_digest
        ):
            raise PolicyControlError("EXECUTOR_STATUS_SUPERSEDED")
        payload = {
            "schema_version": "phase5.13E-2C-R1/execution-order/v1",
            "execution_order_id": str(order_id),
            "order_mode": "FIXED_REFERENCE_EXECUTION",
            "requested_action": "EXECUTE_FIXED_REFERENCE_TASK",
            "execution_scope": "FIXED_REFERENCE_ONLY",
            "task_type": FIXED_TASK_TYPE,
            "max_execution_count": 1,
            "consumed_count": 0,
            "policy_bundle_id": str(bundle.id),
            "policy_bundle_version_id": str(version.id),
            "policy_payload_digest": version.payload_digest,
            "readiness_id": str(readiness.id),
            "readiness_digest": readiness.readiness_digest,
            "source_executor_status_event_id": str(
                readiness.source_executor_status_event_id
            ),
            "source_executor_status_event_digest":
                readiness.source_executor_status_event_digest,
            "connector_id": str(bundle.connector_id),
            "executor_mirror_id": str(executor_id),
            "executor_id": version.canonical_payload["executor_id"],
            "central_asset_version_id": str(
                version.central_asset_version_id
            ),
            "local_asset_metadata_digest":
                version.canonical_payload["local_asset_metadata_digest"],
            "quality_digest": version.canonical_payload["quality_digest"],
            "model_reference_digest":
                version.canonical_payload["model_reference_digest"],
            "attested_image_manifest_id":
                version.canonical_payload["attested_image_manifest_id"],
            "attested_image_manifest_digest":
                version.canonical_payload[
                    "attested_image_manifest_digest"
                ],
            "image_digest": version.canonical_payload["image_digest"],
            "security_profile_digest":
                version.canonical_payload["security_profile_digest"],
            "resource_policy_digest":
                version.canonical_payload["resource_policy_digest"],
            "admission_digest":
                version.canonical_payload["admission_digest"],
            "capability_digest":
                version.canonical_payload["capability_digest"],
            "task_definition_digest":
                version.canonical_payload["task_definition_digest"],
            "input_schema_digest":
                version.canonical_payload["input_schema_digest"],
            "output_schema_digest":
                version.canonical_payload["output_schema_digest"],
            "connector_sequence": sequence,
            "correlation_id": secrets.token_hex(12),
            "issued_at": stamp.isoformat(),
            "not_before": stamp.isoformat(),
            "expires_at": expires_at.isoformat(),
            "nonce": secrets.token_urlsafe(32),
            "signing_key_id": key.key_id,
            "execution_authorized": True,
            "hard_isolation": False,
        }
    else:
        expires_at = min(version.expires_at, stamp + timedelta(hours=2))
        payload = {
            "schema_version": "phase5.13D/execution-order/v1",
            "execution_order_id": str(order_id),
            "order_mode": "CONTROL_VALIDATION_ONLY",
            "requested_action": "VALIDATE_POLICY_ONLY",
            "policy_bundle_id": str(bundle.id),
            "policy_bundle_version_id": str(version.id),
            "policy_payload_digest": version.payload_digest,
            "connector_id": str(bundle.connector_id),
            "connector_sequence": sequence,
            "correlation_id": secrets.token_hex(12),
            "issued_at": stamp.isoformat(),
            "not_before": stamp.isoformat(),
            "expires_at": expires_at.isoformat(),
            "nonce": secrets.token_urlsafe(32),
            "signing_key_id": key.key_id,
            "execution_authorized": False,
        }
    order = ExecutionOrder(
        id=order_id,
        order_key=f"ORD-{secrets.token_hex(8)}", space_id=space_id,
        order_mode=payload["order_mode"],
        requested_action=payload["requested_action"],
        execution_authorized=fixed,
        execution_scope="FIXED_REFERENCE_ONLY" if fixed else None,
        task_type=FIXED_TASK_TYPE if fixed else None,
        max_execution_count=1 if fixed else 0,
        consumed_count=0,
        executor_id=executor_id if fixed else None,
        policy_bundle_id=bundle.id, policy_bundle_version_id=version.id,
        policy_payload_digest=version.payload_digest, connector_id=bundle.connector_id,
        connector_sequence=sequence, idempotency_key=idempotency_key,
        correlation_id=payload["correlation_id"], issued_at=stamp, not_before=stamp,
        expires_at=expires_at, nonce=payload["nonce"],
        signing_key_id=key.key_id, canonical_payload=payload,
        payload_digest=canonical_json_digest_v1(payload),
        signature=_sign(key.key_id, payload), status="available_for_connector",
        created_by=actor.user_id,
    )
    session.add(order)
    await session.flush()
    await append_control_audit(
        session, space_id=space_id, event_type="execution_order.available",
        subject_type="execution_order", subject_id=order.id,
        evidence={
            "sequence": sequence,
            "digest": order.payload_digest,
            "mode": payload["order_mode"],
            "execution_authorized": fixed,
            "execution_started": False,
            "hard_isolation": False,
            "commercial_order_id": (
                commercial_entitlement.get("order_id")
                if commercial_entitlement
                else None
            ),
            "commercial_entitlement_digest": (
                commercial_entitlement.get("entitlement_digest")
                if commercial_entitlement
                else None
            ),
        },
        actor=actor,
    )
    return order


def _verify_connector_signature(certificate_pem: bytes, payload: dict[str, Any], signature: str) -> None:
    with tempfile.TemporaryDirectory() as root:
        cert = Path(root) / "cert.pem"
        public = Path(root) / "public.pem"
        message = Path(root) / "message.bin"
        signed = Path(root) / "signature.bin"
        cert.write_bytes(certificate_pem)
        message.write_bytes(canonical_json_text_v1(payload).encode("utf-8"))
        try:
            signed.write_bytes(base64.b64decode(signature, validate=True))
        except Exception as exc:
            raise PolicyControlError("CONNECTOR_SIGNATURE_INVALID") from exc
        exported = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"],
            capture_output=True, check=False,
        )
        public.write_bytes(exported.stdout)
        verified = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(public),
             "-signature", str(signed), str(message)],
            capture_output=True, check=False,
        )
        if exported.returncode or verified.returncode:
            raise PolicyControlError("CONNECTOR_SIGNATURE_INVALID")


async def accept_receipt(
    session: AsyncSession, *, connector: HospitalConnector, payload: dict[str, Any],
    digest: str, signature: str,
) -> ConnectorOrderReceipt:
    order = await session.get(ExecutionOrder, UUID(payload["execution_order_id"]), with_for_update=True)
    if order is None or order.connector_id != connector.id:
        raise PolicyControlError("ORDER_NOT_FOUND")
    if canonical_json_digest_v1(payload) != digest:
        raise PolicyControlError("RECEIPT_DIGEST_MISMATCH")
    if order.execution_authorized:
        expected_fields = {
            "schema_version", "receipt_id", "execution_order_id",
            "central_order_key",
            "connector_sequence", "order_digest", "policy_digest",
            "source_executor_status_event_digest", "validation_status",
            "automated_validation_digest", "received_at",
            "local_audit_head", "execution_started", "hard_isolation",
        }
        if (
            set(payload) != expected_fields
            or payload.get("schema_version")
            != "phase5.13E-2C-R1/connector-receipt/v1"
            or payload.get("order_digest") != order.payload_digest
            or payload.get("policy_digest") != order.policy_payload_digest
            or payload.get("source_executor_status_event_digest")
            != order.canonical_payload.get(
                "source_executor_status_event_digest"
            )
            or payload.get("connector_sequence") != order.connector_sequence
            or payload.get("execution_started") is not False
            or payload.get("hard_isolation") is not False
            or payload.get("validation_status") not in {"passed", "failed"}
        ):
            raise PolicyControlError("FIXED_RECEIPT_BINDING_INVALID")
    cert = await session.get(ConnectorCertificate, connector.current_certificate_id)
    _verify_connector_signature(cert.certificate_pem, payload, signature)
    existing = await session.scalar(select(ConnectorOrderReceipt).where(ConnectorOrderReceipt.execution_order_id == order.id))
    if existing:
        if existing.payload_digest != digest:
            raise PolicyControlError("RECEIPT_IDEMPOTENCY_CONFLICT")
        return existing
    row = ConnectorOrderReceipt(
        execution_order_id=order.id, connector_id=connector.id,
        receipt_payload=payload, payload_digest=digest, signature=signature,
        connector_key_id=cert.key_id,
    )
    session.add(row)
    order.status = "awaiting_local_review" if payload["validation_status"] == "passed" else "validation_failed"
    order.received_at = utc_now()
    order.validation_completed_at = utc_now()
    await session.flush()
    await append_control_audit(
        session, space_id=order.space_id, event_type="execution_order.receipt_verified",
        subject_type="execution_order", subject_id=order.id,
        evidence={
            "receipt_digest": digest,
            "validation_status": payload["validation_status"],
            "execution_authorized": order.execution_authorized,
            "execution_started": False,
            "hard_isolation": False,
        },
        actor_connector_id=connector.id,
    )
    return row


async def accept_decision(
    session: AsyncSession, *, connector: HospitalConnector, payload: dict[str, Any],
    digest: str, signature: str,
) -> ConnectorOrderDecision:
    order = await session.get(ExecutionOrder, UUID(payload["execution_order_id"]), with_for_update=True)
    if order is None or order.connector_id != connector.id:
        raise PolicyControlError("ORDER_NOT_FOUND")
    if canonical_json_digest_v1(payload) != digest:
        raise PolicyControlError("DECISION_DIGEST_MISMATCH")
    receipt = await session.scalar(
        select(ConnectorOrderReceipt).where(
            ConnectorOrderReceipt.execution_order_id == order.id
        )
    )
    if order.execution_authorized:
        fixed_decision = payload.get("decision")
        automated_failure = fixed_decision == "validation_failed"
        expected_fields = {
            "schema_version", "decision_id", "execution_order_id",
            "receipt_id",
            "receipt_digest", "policy_digest", "order_digest",
            "source_executor_status_event_digest",
            "automated_validation_digest", "reviewer_id", "decision",
            "reason_code", "reason_text", "decided_at",
            "local_audit_head", "execution_started", "hard_isolation",
        }
        if (
            set(payload) != expected_fields
            or payload.get("schema_version")
            != "phase5.13E-2C-R1/connector-decision/v1"
            or receipt is None
            or receipt.receipt_payload.get("validation_status")
            != ("failed" if automated_failure else "passed")
            or payload.get("receipt_id")
            != receipt.receipt_payload.get("receipt_id")
            or payload.get("receipt_digest") != receipt.payload_digest
            or payload.get("policy_digest") != order.policy_payload_digest
            or payload.get("order_digest") != order.payload_digest
            or payload.get("source_executor_status_event_digest")
            != order.canonical_payload.get(
                "source_executor_status_event_digest"
            )
            or payload.get("automated_validation_digest")
            != receipt.receipt_payload.get("automated_validation_digest")
            or payload.get("execution_started") is not False
            or payload.get("hard_isolation") is not False
            or order.status
            != ("validation_failed"
                if automated_failure else "awaiting_local_review")
            or (
                automated_failure
                and payload.get("reviewer_id") != "automated-validator"
            )
            or (
                not automated_failure
                and fixed_decision not in {"accepted", "rejected"}
            )
        ):
            raise PolicyControlError("FIXED_DECISION_BINDING_INVALID")
    cert = await session.get(ConnectorCertificate, connector.current_certificate_id)
    _verify_connector_signature(cert.certificate_pem, payload, signature)
    existing = await session.scalar(select(ConnectorOrderDecision).where(ConnectorOrderDecision.execution_order_id == order.id))
    if existing:
        if existing.payload_digest != digest:
            raise PolicyControlError("DECISION_IDEMPOTENCY_CONFLICT")
        return existing
    decision = payload["decision"]
    allowed = {"accepted", "rejected", "validation_failed", "revoked_after_acceptance"}
    if decision not in allowed:
        raise PolicyControlError("DECISION_INVALID")
    row = ConnectorOrderDecision(
        execution_order_id=order.id, connector_id=connector.id, decision=decision,
        reason_code=payload["reason_code"], reason_text=payload["reason_text"],
        decision_payload=payload, payload_digest=digest, signature=signature,
        connector_key_id=cert.key_id,
    )
    session.add(row)
    order.status = "revoked" if decision == "revoked_after_acceptance" else (
        "accepted" if decision == "accepted" else (
            "validation_failed" if decision == "validation_failed" else "rejected"
        )
    )
    order.local_decision_at = utc_now()
    await session.flush()
    await append_control_audit(
        session, space_id=order.space_id,
        event_type=f"execution_order.local_{decision}",
        subject_type="execution_order", subject_id=order.id,
        evidence={
            "decision_digest": digest,
            "decision": decision,
            "execution_authorized": order.execution_authorized,
            "central_override": False,
            "execution_started": False,
            "hard_isolation": False,
        },
        actor_connector_id=connector.id,
    )
    return row


async def accept_execution_consumption(
    session: AsyncSession, *, connector: HospitalConnector,
    payload: dict[str, Any], digest: str, signature: str,
) -> ExecutionOrderConsumptionReceipt:
    expected_fields = {
        "schema_version", "consumption_receipt_id", "execution_order_id",
        "execution_order_digest", "authorization_snapshot_id",
        "authorization_snapshot_digest", "task_manifest_id",
        "task_manifest_digest", "runtime_session_id", "runtime_digest",
        "reference_execution_id", "request_digest", "consumed_at",
        "remaining_validity_seconds", "local_audit_head",
        "execution_started", "hard_isolation",
    }
    if (
        set(payload) != expected_fields
        or payload.get("schema_version")
        != "phase5.13E-2C-R1/execution-consumption/v1"
        or payload.get("execution_started") is not False
        or payload.get("hard_isolation") is not False
        or canonical_json_digest_v1(payload) != digest
    ):
        raise PolicyControlError("EXECUTION_CONSUMPTION_BINDING_INVALID")
    try:
        order_id = UUID(payload["execution_order_id"])
        consumed_at = datetime.fromisoformat(payload["consumed_at"])
    except (TypeError, ValueError) as exc:
        raise PolicyControlError("EXECUTION_CONSUMPTION_TIME_INVALID") from exc
    order = await session.get(ExecutionOrder, order_id, with_for_update=True)
    if (
        order is None
        or order.connector_id != connector.id
        or not order.execution_authorized
        or order.order_mode != "FIXED_REFERENCE_EXECUTION"
        or order.status != "accepted"
        or payload["execution_order_digest"] != order.payload_digest
        or consumed_at < order.not_before
        or consumed_at >= order.expires_at
        or payload["remaining_validity_seconds"] < 0
    ):
        raise PolicyControlError("EXECUTION_CONSUMPTION_ORDER_INVALID")
    bundle = await session.get(PolicyBundle, order.policy_bundle_id)
    if bundle is None:
        raise PolicyControlError("POLICY_BUNDLE_NOT_FOUND")
    from app.modules.commerce.gating import (
        CommercialExecutionBlocked,
        require_paid_execution_entitlement,
    )

    try:
        commercial_entitlement = await require_paid_execution_entitlement(
            session, contract_id=bundle.contract_id
        )
    except CommercialExecutionBlocked as exc:
        raise PolicyControlError(
            f"COMMERCIAL_EXECUTION_BLOCKED:{exc}"
        ) from exc
    cert = await session.get(
        ConnectorCertificate, connector.current_certificate_id
    )
    _verify_connector_signature(cert.certificate_pem, payload, signature)
    existing = await session.scalar(
        select(ExecutionOrderConsumptionReceipt).where(
            ExecutionOrderConsumptionReceipt.execution_order_id == order.id
        )
    )
    if existing is not None:
        if existing.payload_digest != digest:
            raise PolicyControlError("EXECUTION_CONSUMPTION_IDEMPOTENCY_CONFLICT")
        return existing
    if order.consumed_count != 0 or order.max_execution_count != 1:
        raise PolicyControlError("EXECUTION_ORDER_ALREADY_CONSUMED")
    row = ExecutionOrderConsumptionReceipt(
        id=UUID(payload["consumption_receipt_id"]),
        execution_order_id=order.id,
        connector_id=connector.id,
        authorization_snapshot_id=payload["authorization_snapshot_id"],
        task_manifest_id=payload["task_manifest_id"],
        runtime_session_id=payload["runtime_session_id"],
        reference_execution_id=payload["reference_execution_id"],
        consumption_payload=payload,
        payload_digest=digest,
        signature=signature,
        connector_key_id=cert.key_id,
    )
    session.add(row)
    order.consumed_count = 1
    await session.flush()
    await append_control_audit(
        session,
        space_id=order.space_id,
        event_type="execution_order.consumed",
        subject_type="execution_order",
        subject_id=order.id,
        evidence={
            "consumption_receipt_id": str(row.id),
            "consumption_digest": digest,
            "authorization_snapshot_id":
                payload["authorization_snapshot_id"],
            "task_manifest_id": payload["task_manifest_id"],
            "runtime_session_id": payload["runtime_session_id"],
            "reference_execution_id": payload["reference_execution_id"],
            "consumed_count": 1,
            "commercial_order_id": commercial_entitlement.get("order_id"),
            "commercial_entitlement_digest": commercial_entitlement.get(
                "entitlement_digest"
            ),
            "execution_started": False,
            "hard_isolation": False,
        },
        actor_connector_id=connector.id,
    )
    return row


async def revoke_policy(
    session: AsyncSession, *, actor: DemoActor, space_id: UUID,
    bundle_id: UUID, reason_code: str, reason_text: str,
) -> PolicyRevocation:
    _operator(actor)
    bundle = await session.get(PolicyBundle, bundle_id, with_for_update=True)
    if bundle is None or bundle.space_id != space_id or bundle.status == "revoked":
        raise PolicyControlError("POLICY_NOT_REVOCABLE")
    version = await session.get(PolicyBundleVersion, bundle.current_version_id)
    key = await session.scalar(select(PolicySigningKey).where(PolicySigningKey.key_id == version.signing_key_id))
    stamp = utc_now()
    body = {
        "schema_version": "phase5.13D/policy-revocation/v1",
        "policy_bundle_id": str(bundle.id), "policy_bundle_version_id": str(version.id),
        "reason_code": reason_code, "reason_text": reason_text,
        "effective_at": stamp.isoformat(), "issued_at": stamp.isoformat(),
        "nonce": secrets.token_urlsafe(32), "signing_key_id": key.key_id,
    }
    row = PolicyRevocation(
        policy_bundle_id=bundle.id, policy_bundle_version_id=version.id,
        revocation_id=f"REV-{secrets.token_hex(8)}", reason_code=reason_code,
        reason_text=reason_text, effective_at=stamp, issued_at=stamp,
        nonce=body["nonce"], signing_key_id=key.key_id,
        payload_digest=canonical_json_digest_v1(body), signature=_sign(key.key_id, body),
        created_by=actor.user_id,
    )
    session.add(row)
    bundle.status = "revoked"
    bundle.revoked_at = stamp
    bundle.revocation_reason = reason_text
    orders = (await session.scalars(select(ExecutionOrder).where(
        ExecutionOrder.policy_bundle_id == bundle.id,
        ExecutionOrder.status.in_(("available_for_connector", "delivered", "received", "awaiting_local_review", "accepted")),
    ))).all()
    for order in orders:
        order.status = "revoked"
        order.revoked_at = stamp
    await session.flush()
    await append_control_audit(
        session, space_id=space_id, event_type="policy.bundle.revoked",
        subject_type="policy_revocation", subject_id=row.id,
        evidence={"bundle_id": str(bundle.id), "revocation_id": row.revocation_id, "reason_code": reason_code},
        actor=actor,
    )
    return row
