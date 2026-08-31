from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.data_products import _actor
from app.db.session import get_db_session
from app.modules.connector_control.models import (
    ConnectorCapabilityManifest,
    ConnectorCertificate,
    ConnectorControlAuditEvent,
    ConnectorAssetMirror,
    ConnectorAssetMirrorVersion,
    ConnectorEnrollmentToken,
    ConnectorHeartbeat,
    ConnectorRegistrationRequest,
    HospitalEvidenceBundleReceipt,
    HospitalExecutorMirror,
    HospitalExecutorStatusEvent,
    HospitalConnector,
)
from app.modules.connector_control.services import (
    ConnectorControlError,
    accept_heartbeat,
    accept_asset_metadata_bundle,
    accept_hospital_evidence_bundle,
    accept_executor_status,
    append_control_audit,
    create_enrollment_token,
    decide_registration,
    rotate_certificate,
    submit_manifest,
    submit_registration,
    transition_connector,
    verify_presented_connector_certificate,
)
from app.modules.identity.models import Organization
from app.modules.spaces.models import SpaceParticipant, SpaceParticipantRole

router = APIRouter(prefix="/connector-control", tags=["hospital-connector-control-alpha"])


class EnrollmentRequest(BaseModel):
    organization_id: UUID
    connector_name: str = Field(min_length=3, max_length=120)
    lifetime_minutes: int = Field(default=15, ge=5, le=60)


class RegistrationRequest(BaseModel):
    enrollment_token: str = Field(min_length=32, max_length=256)
    organization_id: UUID
    connector_instance_id: str = Field(min_length=12, max_length=80)
    installation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    display_name: str = Field(min_length=3, max_length=120)
    csr_pem: str = Field(min_length=300, max_length=8192)
    connector_version: str = Field(max_length=32)
    operating_system: str = Field(max_length=40)
    architecture: str = Field(max_length=24)
    bootstrap_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    nonce: str = Field(min_length=16, max_length=96)
    request_timestamp: datetime


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


class TransitionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ManifestRequest(BaseModel):
    schema_version: str = Field(max_length=40)
    manifest_version: str = Field(max_length=40)
    sequence: int = Field(gt=0)
    connector_version: str = Field(max_length=32)
    operating_system: str = Field(max_length=40)
    architecture: str = Field(max_length=24)
    capability_payload: dict[str, Any]
    execution_enabled: bool
    data_transfer_enabled: bool
    model_transfer_enabled: bool
    local_asset_registry_enabled: bool
    metadata_sync_enabled: bool = False
    data_quality_summary_enabled: bool = False
    artifact_egress_enabled: bool
    hard_isolation: bool
    isolation_maturity: Literal["L0", "L1"]
    manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signed_at: datetime


class HeartbeatRequest(BaseModel):
    sequence: int = Field(gt=0)
    sent_at: datetime
    status: Literal["healthy", "degraded"]
    connector_version: str = Field(max_length=32)
    capability_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    local_audit_head: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    health_summary: dict[str, Any]
    nonce: str = Field(min_length=16, max_length=96)
    message_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CertificateRotationRequest(BaseModel):
    csr_pem: str = Field(min_length=300, max_length=8192)


class AssetMetadataBundleRequest(BaseModel):
    schema_version: str = Field(max_length=40)
    bundle_id: str = Field(min_length=12, max_length=80)
    bundle_sequence: int = Field(gt=0)
    local_asset_key: str = Field(
        min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]+$"
    )
    version_label: str = Field(min_length=1, max_length=64)
    metadata_summary: dict[str, Any]
    disclosure_summary: dict[str, Any]
    quality_summary: dict[str, Any]
    deidentification_summary: dict[str, Any]
    known_limitations: list[str] = Field(max_length=30)
    warning_flags: list[str] = Field(max_length=30)
    metadata_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    quality_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signed_at: datetime
    nonce: str = Field(min_length=16, max_length=96)
    bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ExecutorStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["phase5.13E-1A/executor-status/v1"]
    executor_instance_id: str = Field(min_length=12, max_length=96)
    executor_version: str = Field(min_length=1, max_length=32)
    architecture: str = Field(min_length=2, max_length=24)
    status: Literal["pending", "approved", "active", "paused", "revoked", "offline"]
    certificate_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    capability_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    security_status: Literal["pending", "passed", "failed", "revoked"]
    status_sequence: int = Field(gt=0)
    heartbeat_sequence: int = Field(ge=0)
    heartbeat_at: datetime | None
    event_type: Literal["registered", "heartbeat", "paused", "resumed", "revoked"]
    execution_enabled: Literal[False]
    hard_isolation: Literal[False]
    sent_at: datetime
    nonce: str = Field(min_length=16, max_length=96)
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class StrictExecutorProof(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutorCapabilityProof(StrictExecutorProof):
    local_object_id: str | None
    manifest_version: str | None
    digest: str | None
    fixed_reference_execution_enabled: bool
    supported_task_types: list[str]
    arbitrary_execution_enabled: bool
    user_code_enabled: bool
    user_model_enabled: bool
    data_transfer_enabled: bool
    model_transfer_enabled: bool
    artifact_auto_egress_enabled: bool
    hard_isolation: bool


class ExecutorImageProof(StrictExecutorProof):
    local_object_id: str | None
    image_id: str | None
    image_digest: str | None
    manifest_digest: str | None
    lifecycle_status: str
    signature_status: str
    security_scan_status: str
    build_time: datetime | None
    revoked_at: datetime | None


class ExecutorSecurityProof(StrictExecutorProof):
    local_object_id: str | None
    security_version: str | None
    profile_digest: str | None
    status: str
    network_mode: str | None
    filesystem_mode: str | None
    rootless: bool
    privileged: bool
    docker_socket_access: bool
    runtime_download: bool
    input_readonly: bool
    capabilities_dropped: bool
    created_at: datetime | None


class ExecutorResourceProof(StrictExecutorProof):
    local_object_id: str | None
    policy_version: str | None
    policy_digest: str | None
    status: str
    cpu_cores: int | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None
    processes: int | None = None
    timeout_seconds: int | None = None
    created_at: datetime | None


class ExecutorAdmissionProof(StrictExecutorProof):
    local_object_id: str | None
    admission_digest: str | None
    result: str
    executor_id: str | None
    image_manifest_digest: str | None
    image_digest: str | None
    security_profile_digest: str | None
    resource_policy_digest: str | None
    capability_digest: str | None
    checked_at: datetime | None
    valid_until: datetime | None


class ExecutorFixedExecutionReadinessAttestationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["hospital_executor_status_v2"]
    event_type: Literal["EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION"]
    connector_id: UUID
    executor_id: str = Field(min_length=36, max_length=36)
    executor_instance_id: str = Field(min_length=12, max_length=96)
    connector_certificate_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    executor_certificate_fingerprint: str | None
    executor_status: str
    executor_version: str = Field(min_length=1, max_length=32)
    heartbeat_at: datetime | None
    capability: ExecutorCapabilityProof
    image_manifest: ExecutorImageProof
    security_profile: ExecutorSecurityProof
    resource_policy: ExecutorResourceProof
    admission: ExecutorAdmissionProof
    readiness_result: Literal[
        "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION", "NOT_READY"
    ]
    readiness_reason: str | None
    local_audit_head: str | None
    local_state_revision: str = Field(min_length=8, max_length=96)
    event_sequence: int = Field(gt=0)
    nonce: str = Field(min_length=16, max_length=96)
    generated_at: datetime
    not_before: datetime
    expires_at: datetime
    signing_key_id: str = Field(min_length=8, max_length=80)
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signature: str = Field(min_length=32, max_length=2048)


class HospitalEvidenceBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase5.13E-Final/evidence-bundle/v1"]
    bundle_id: UUID
    bundle_version: Literal[1]
    connector_id: UUID
    organization_id: UUID
    task_type: Literal["PATHMNIST_REFERENCE_V1"]
    local_artifact_ref: str = Field(min_length=36, max_length=36)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_bundle_id: UUID
    policy_bundle_version_id: UUID
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_order_id: UUID
    execution_order_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorization_snapshot_id: str = Field(min_length=36, max_length=36)
    authorization_snapshot_digest: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    consumption_receipt_digest: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    task_manifest_id: str = Field(min_length=36, max_length=36)
    task_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_session_id: str = Field(min_length=36, max_length=36)
    runtime_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reference_execution_id: str = Field(min_length=36, max_length=36)
    execution_result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_reference_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_manifest: list[dict[str, Any]] = Field(min_length=3, max_length=3)
    result_summary: dict[str, Any]
    scan_report_id: str = Field(min_length=36, max_length=36)
    scan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_id: str = Field(min_length=36, max_length=36)
    review_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_decision: Literal["APPROVE_FOR_EVIDENCE_CANDIDACY"]
    reviewer_role: Literal["local_artifact_reviewer"]
    causal_validation_id: str = Field(min_length=36, max_length=36)
    causal_validation_digest: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    local_audit_head: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_started_at: datetime
    execution_completed_at: datetime
    quality_limitations: list[str] = Field(min_length=1, max_length=8)
    security_boundaries: dict[str, bool]
    generated_at: datetime
    signing_key_id: str = Field(min_length=8, max_length=100)
    nonce: str = Field(min_length=16, max_length=96)
    bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signature: str = Field(min_length=32, max_length=2048)


async def _context(session: AsyncSession, identity: str):
    return await _actor(session, identity)


def _error(exc: ConnectorControlError) -> HTTPException:
    message = str(exc)
    status = 403 if "operator" in message or "REVOKED" in message else 409
    if "not found" in message:
        status = 404
    if "INVALID" in message or "MISMATCH" in message or "TOO_WEAK" in message:
        status = 400
    return HTTPException(status, message)


def _connector_payload(row: HospitalConnector) -> dict[str, Any]:
    return {
        "id": str(row.id), "organization_id": str(row.organization_id),
        "connector_instance_id": row.connector_instance_id, "display_name": row.display_name,
        "environment": row.environment, "connector_version": row.connector_version,
        "operating_system": row.operating_system, "architecture": row.architecture,
        "status": row.status, "heartbeat_status": row.heartbeat_status,
        "last_heartbeat_at": row.last_heartbeat_at, "last_heartbeat_sequence": row.last_heartbeat_sequence,
        "current_certificate_id": str(row.current_certificate_id) if row.current_certificate_id else None,
        "current_capability_manifest_id": str(row.current_capability_manifest_id) if row.current_capability_manifest_id else None,
        "hard_isolation": False, "execution_enabled": False, "data_transfer_enabled": False,
        "model_transfer_enabled": False,
        "local_asset_registry_enabled": bool(
            getattr(row, "current_capability_manifest_id", None)
        ),
        "artifact_egress_enabled": False,
    }


def _executor_payload(row: HospitalExecutorMirror) -> dict[str, Any]:
    valid_until = row.readiness_valid_until
    normalized_valid_until = (
        valid_until.replace(tzinfo=timezone.utc)
        if valid_until is not None and valid_until.tzinfo is None
        else valid_until
    )
    readiness_is_current = (
        row.fixed_reference_readiness_status == "ready"
        and row.status == "active"
        and row.latest_status_event_id == row.latest_verified_readiness_event_id
        and normalized_valid_until is not None
        and normalized_valid_until > datetime.now(timezone.utc)
    )
    readiness_status = "ready" if readiness_is_current else "not_ready"
    readiness_reason = (
        row.fixed_reference_readiness_reason
        if readiness_is_current
        else "ATTESTATION_EXPIRED_OR_SUPERSEDED"
    )
    return {
        "id": str(row.id),
        "connector_id": str(row.connector_id),
        "organization_id": str(row.organization_id),
        "executor_instance_id": row.executor_instance_id,
        "executor_version": row.executor_version,
        "architecture": row.architecture,
        "status": row.status,
        "certificate_fingerprint": row.certificate_fingerprint,
        "capability_manifest_digest": row.capability_manifest_digest,
        "runtime_digest": row.runtime_digest,
        "image_digest": row.image_digest,
        "security_status": row.security_status,
        "last_status_sequence": row.last_status_sequence,
        "last_heartbeat_sequence": row.last_heartbeat_sequence,
        "last_heartbeat_at": row.last_heartbeat_at,
        "execution_enabled": False,
        "hard_isolation": False,
        "control_only": True,
        "latest_status_schema_version": row.latest_status_schema_version,
        "latest_status_event_sequence": row.latest_status_event_sequence,
        "latest_status_event_digest": row.latest_status_event_digest,
        "fixed_reference_readiness_status": readiness_status,
        "fixed_reference_readiness_reason": readiness_reason,
        "latest_verified_readiness_event_id": (
            str(row.latest_verified_readiness_event_id)
            if row.latest_verified_readiness_event_id else None
        ),
        "latest_verified_readiness_digest":
            row.latest_verified_readiness_digest,
        "latest_verified_readiness_at": row.latest_verified_readiness_at,
        "readiness_valid_until": row.readiness_valid_until,
        "attested_image_digest": row.attested_image_digest,
        "attested_security_profile_digest":
            row.attested_security_profile_digest,
        "attested_resource_policy_digest":
            row.attested_resource_policy_digest,
        "attested_admission_digest": row.attested_admission_digest,
        "attested_capability_digest": row.attested_capability_digest,
        "readiness_statement": (
            "Verified for fixed-reference policy compilation; not executed"
            if readiness_is_current
            else "No current verified fixed-reference readiness"
        ),
        "central_independent_inspection": False,
        "last_synced_at": row.last_synced_at,
    }


@router.post("/enrollment-tokens")
async def issue_enrollment(
    request: EnrollmentRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    try:
        row, raw = await create_enrollment_token(
            session, actor=actor, space_id=context.space_id, organization_id=request.organization_id,
            connector_name=request.connector_name, lifetime_minutes=request.lifetime_minutes,
        )
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {
        "id": str(row.id), "organization_id": str(row.organization_id),
        "connector_name": row.connector_name, "expires_at": row.expires_at,
        "status": row.status, "enrollment_token": raw, "display_once": True,
    }


@router.get("/enrollment-options")
async def enrollment_options(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role != "space_operator":
        raise HTTPException(403, "only the platform operator may create enrollment tokens")
    rows = (
        await session.execute(
            select(Organization.id, Organization.display_name)
            .join(SpaceParticipant, SpaceParticipant.organization_id == Organization.id)
            .join(SpaceParticipantRole, SpaceParticipantRole.space_participant_id == SpaceParticipant.id)
            .where(
                SpaceParticipant.space_id == context.space_id,
                SpaceParticipant.admission_status == "admitted",
                SpaceParticipantRole.role_code == "data_provider",
            )
            .order_by(Organization.display_name)
        )
    ).all()
    return {"items": [{"id": str(row.id), "name": row.display_name} for row in rows]}


@router.get("/enrollment-tokens")
async def list_enrollment(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role != "space_operator":
        raise HTTPException(403, "only the platform operator may list enrollment records")
    rows = (await session.scalars(select(ConnectorEnrollmentToken).where(ConnectorEnrollmentToken.space_id == context.space_id).order_by(ConnectorEnrollmentToken.created_at.desc()))).all()
    return {"items": [{"id": str(row.id), "organization_id": str(row.organization_id), "connector_name": row.connector_name, "expires_at": row.expires_at, "used_at": row.used_at, "status": row.status} for row in rows]}


@router.post("/bootstrap/registrations")
async def register(request: RegistrationRequest, session: AsyncSession = Depends(get_db_session)):
    try:
        row = await submit_registration(session, raw_token=request.enrollment_token, payload=request.model_dump(exclude={"enrollment_token"}))
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"id": str(row.id), "status": row.status, "csr_fingerprint": row.csr_fingerprint}


@router.get("/bootstrap/registrations/{request_id}")
async def registration_status(request_id: UUID, connector_instance_id: str, session: AsyncSession = Depends(get_db_session)):
    row = await session.get(ConnectorRegistrationRequest, request_id)
    if row is None or row.connector_instance_id != connector_instance_id:
        raise HTTPException(404, "registration not found")
    payload: dict[str, Any] = {"id": str(row.id), "status": row.status, "connector_id": str(row.connector_id) if row.connector_id else None}
    if row.connector_id and row.status == "certificate_issued":
        connector = await session.get(HospitalConnector, row.connector_id)
        cert = await session.get(ConnectorCertificate, connector.current_certificate_id) if connector else None
        if cert:
            payload["certificate_pem"] = cert.certificate_pem.decode("ascii")
            payload["certificate_fingerprint"] = cert.fingerprint_sha256
            payload["certificate_valid_to"] = cert.valid_to
    return payload


@router.get("/registrations")
async def registrations(identity: str = Header(alias="X-Demo-Identity"), session: AsyncSession = Depends(get_db_session)):
    context, actor = await _context(session, identity)
    if actor.role != "space_operator":
        raise HTTPException(403, "only the platform operator may review registrations")
    rows = (await session.scalars(select(ConnectorRegistrationRequest).where(ConnectorRegistrationRequest.space_id == context.space_id).order_by(ConnectorRegistrationRequest.created_at.desc()))).all()
    return {"items": [{"id": str(row.id), "organization_id": str(row.organization_id), "connector_instance_id": row.connector_instance_id, "display_name": row.display_name, "connector_version": row.connector_version, "operating_system": row.operating_system, "architecture": row.architecture, "csr_fingerprint": row.csr_fingerprint, "bootstrap_manifest_digest": row.bootstrap_manifest_digest, "status": row.status, "created_at": row.created_at} for row in rows]}


@router.post("/registrations/{request_id}/decision")
async def registration_decision(request_id: UUID, request: DecisionRequest, identity: str = Header(alias="X-Demo-Identity"), session: AsyncSession = Depends(get_db_session)):
    context, actor = await _context(session, identity)
    try:
        registration, connector, cert = await decide_registration(session, actor=actor, space_id=context.space_id, request_id=request_id, approve=request.decision == "approve", reason=request.reason)
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"registration_id": str(registration.id), "status": registration.status, "connector": _connector_payload(connector) if connector else None, "certificate": {"id": str(cert.id), "fingerprint": cert.fingerprint_sha256, "valid_to": cert.valid_to, "issuer": cert.issuer} if cert else None}


@router.get("/connectors")
async def connectors(identity: str = Header(alias="X-Demo-Identity"), session: AsyncSession = Depends(get_db_session)):
    context, actor = await _context(session, identity)
    query = select(HospitalConnector).where(HospitalConnector.space_id == context.space_id)
    if actor.role == "data_provider":
        query = query.where(HospitalConnector.organization_id == actor.organization_id)
    elif actor.role != "space_operator":
        raise HTTPException(403, "connector control is not available to this role")
    rows = (await session.scalars(query.order_by(HospitalConnector.created_at.desc()))).all()
    return {"items": [_connector_payload(row) for row in rows], "total": len(rows)}


@router.get("/connectors/{connector_id}")
async def connector_detail(connector_id: UUID, identity: str = Header(alias="X-Demo-Identity"), session: AsyncSession = Depends(get_db_session)):
    context, actor = await _context(session, identity)
    row = await session.get(HospitalConnector, connector_id)
    if row is None or row.space_id != context.space_id or (actor.role == "data_provider" and row.organization_id != actor.organization_id):
        raise HTTPException(404, "connector not found")
    if actor.role not in {"space_operator", "data_provider"}:
        raise HTTPException(403, "connector control is not available to this role")
    cert = await session.get(ConnectorCertificate, row.current_certificate_id) if row.current_certificate_id else None
    manifest = await session.get(ConnectorCapabilityManifest, row.current_capability_manifest_id) if row.current_capability_manifest_id else None
    return {**_connector_payload(row), "certificate": {"fingerprint": cert.fingerprint_sha256, "issuer": cert.issuer, "valid_from": cert.valid_from, "valid_to": cert.valid_to, "status": cert.status} if cert else None, "capability_manifest": {"manifest_version": manifest.manifest_version, "sequence": manifest.sequence, "digest": manifest.manifest_digest, "payload": manifest.capability_payload} if manifest else None}


@router.get("/executors")
async def executors(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    query = select(HospitalExecutorMirror).where(
        HospitalExecutorMirror.space_id == context.space_id
    )
    if actor.role == "data_provider":
        query = query.where(
            HospitalExecutorMirror.organization_id == actor.organization_id
        )
    elif actor.role != "space_operator":
        raise HTTPException(403, "executor status is not available to this role")
    rows = (
        await session.scalars(
            query.order_by(HospitalExecutorMirror.last_synced_at.desc())
        )
    ).all()
    return {"items": [_executor_payload(row) for row in rows], "total": len(rows)}


@router.get("/executors/{executor_id}")
async def executor_detail(
    executor_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    row = await session.get(HospitalExecutorMirror, executor_id)
    if row is None or row.space_id != context.space_id:
        raise HTTPException(404, "executor status mirror not found")
    if actor.role == "data_provider":
        if row.organization_id != actor.organization_id:
            raise HTTPException(404, "executor status mirror not found")
    elif actor.role != "space_operator":
        raise HTTPException(403, "executor status is not available to this role")
    events = (
        await session.scalars(
            select(HospitalExecutorStatusEvent)
            .where(HospitalExecutorStatusEvent.mirror_id == row.id)
            .order_by(HospitalExecutorStatusEvent.status_sequence)
        )
    ).all()
    return {
        **_executor_payload(row),
        "events": [
            {
                "id": str(event.id),
                "status_sequence": event.status_sequence,
                "event_type": event.event_type,
                "schema_version": event.schema_version,
                "status": event.status,
                "payload_digest": event.payload_digest,
                "verification_status": event.verification_status,
                "verification_reason": event.verification_reason,
                "verified_at": event.verified_at,
                "received_at": event.received_at,
            }
            for event in events
        ],
    }


@router.get("/evidence-bundles")
async def evidence_bundles(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    query = select(HospitalEvidenceBundleReceipt).where(
        HospitalEvidenceBundleReceipt.space_id == context.space_id
    )
    if actor.role == "data_provider":
        query = query.where(
            HospitalEvidenceBundleReceipt.organization_id
            == actor.organization_id
        )
    elif actor.role != "space_operator":
        raise HTTPException(403, "hospital evidence is not available to this role")
    rows = (
        await session.scalars(
            query.order_by(HospitalEvidenceBundleReceipt.received_at.desc())
        )
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "bundle_id": str(row.bundle_id),
                "connector_id": str(row.connector_id),
                "schema_version": row.schema_version,
                "bundle_version": row.bundle_version,
                "artifact_digest": row.artifact_digest,
                "reference_execution_id": row.reference_execution_id,
                "bundle_digest": row.bundle_digest,
                "review_digest": row.review_digest,
                "causal_validation_digest": row.causal_validation_digest,
                "local_audit_head": row.local_audit_head,
                "verification_status": row.verification_status,
                "result_summary": row.evidence_summary["result_summary"],
                "security_boundaries":
                    row.evidence_summary["security_boundaries"],
                "received_at": row.received_at,
                "artifact_received": False,
                "raw_data_received": False,
                "local_path_received": False,
                "hard_isolation": False,
            }
            for row in rows
        ],
        "total": len(rows),
        "registry_boundary":
            "verified signed summary only; no local Artifact or raw data",
    }


@router.get("/assets")
async def connector_assets(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    query = select(ConnectorAssetMirror).where(ConnectorAssetMirror.space_id == context.space_id)
    if actor.role == "data_provider":
        query = query.where(ConnectorAssetMirror.organization_id == actor.organization_id)
    elif actor.role != "space_operator":
        raise HTTPException(403, "connector asset metadata is not available to this role")
    rows = (await session.scalars(query.order_by(ConnectorAssetMirror.last_synced_at.desc()))).all()
    items = []
    for row in rows:
        version = await session.get(ConnectorAssetMirrorVersion, row.current_version_id)
        items.append({
            "id": str(row.id), "connector_id": str(row.connector_id),
            "local_asset_key": row.local_asset_key, "display_name": row.display_name,
            "asset_kind": row.asset_kind, "modality": row.modality,
            "source_category": row.source_category,
            "sensitivity_classification": row.sensitivity_classification,
            "status": row.status, "last_synced_at": row.last_synced_at,
            "version": {
                "id": str(version.id), "version_label": version.version_label,
                "bundle_sequence": version.bundle_sequence,
                "disclosure_summary": version.disclosure_summary,
                "metadata_summary": version.metadata_summary,
                "quality_summary": version.quality_summary,
                "deidentification_summary": version.deidentification_summary,
                "known_limitations": version.known_limitations,
                "warning_flags": version.warning_flags,
                "metadata_digest": version.metadata_digest,
                "quality_digest": version.quality_digest,
            } if version else None,
            "metadata_only": True, "requestable": False,
            "execution_permitted": False, "materialized": False,
        })
    return {"items": items, "total": len(items)}


@router.get("/assets/{asset_id}")
async def connector_asset_detail(
    asset_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    row = await session.get(ConnectorAssetMirror, asset_id)
    if row is None or row.space_id != context.space_id:
        raise HTTPException(404, "connector asset metadata mirror not found")
    if actor.role == "data_provider":
        if row.organization_id != actor.organization_id:
            raise HTTPException(404, "connector asset metadata mirror not found")
    elif actor.role != "space_operator":
        raise HTTPException(403, "connector asset metadata is not available to this role")
    versions = (
        await session.scalars(
            select(ConnectorAssetMirrorVersion)
            .where(ConnectorAssetMirrorVersion.mirror_id == row.id)
            .order_by(ConnectorAssetMirrorVersion.bundle_sequence)
        )
    ).all()
    return {
        "id": str(row.id), "connector_id": str(row.connector_id),
        "local_asset_key": row.local_asset_key, "display_name": row.display_name,
        "asset_kind": row.asset_kind, "modality": row.modality,
        "source_category": row.source_category,
        "sensitivity_classification": row.sensitivity_classification,
        "status": row.status, "last_synced_at": row.last_synced_at,
        "metadata_only": True, "requestable": False,
        "execution_permitted": False, "materialized": False,
        "versions": [{
            "id": str(version.id), "version_label": version.version_label,
            "bundle_sequence": version.bundle_sequence,
            "metadata_summary": version.metadata_summary,
            "disclosure_summary": version.disclosure_summary,
            "quality_summary": version.quality_summary,
            "deidentification_summary": version.deidentification_summary,
            "known_limitations": version.known_limitations,
            "warning_flags": version.warning_flags,
            "metadata_digest": version.metadata_digest,
            "schema_digest": version.schema_digest,
            "quality_digest": version.quality_digest,
            "created_at": version.created_at,
        } for version in versions],
    }


@router.get("/audit")
async def connector_audit(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role != "space_operator":
        raise HTTPException(403, "only the platform operator may view connector audit")
    rows = (
        await session.scalars(
            select(ConnectorControlAuditEvent)
            .where(ConnectorControlAuditEvent.space_id == context.space_id)
            .order_by(ConnectorControlAuditEvent.stream_sequence)
        )
    ).all()
    valid = all(
        row.stream_sequence == index + 1
        and row.previous_event_digest == (rows[index - 1].event_digest if index else None)
        for index, row in enumerate(rows)
    )
    return {
        "items": [{
            "sequence": row.stream_sequence, "event_type": row.event_type,
            "subject_type": row.subject_type, "subject_id": str(row.subject_id),
            "occurred_at": row.occurred_at, "event_digest": row.event_digest,
        } for row in rows],
        "total": len(rows), "chain_valid": valid,
        "head_digest": rows[-1].event_digest if rows else None,
    }


@router.post("/connectors/{connector_id}/{action}")
async def connector_transition(connector_id: UUID, action: Literal["pause", "resume", "revoke"], request: TransitionRequest, identity: str = Header(alias="X-Demo-Identity"), session: AsyncSession = Depends(get_db_session)):
    context, actor = await _context(session, identity)
    try:
        row = await transition_connector(session, actor=actor, space_id=context.space_id, connector_id=connector_id, action=action, reason=request.reason)
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return _connector_payload(row)


async def _ingress_connector(
    session: AsyncSession,
    connector_id: UUID,
    client_certificate: str,
    ingress_verified: str,
    request: Request,
) -> tuple[HospitalConnector, ConnectorCertificate]:
    if ingress_verified.lower() != "true" or request.client is None or request.client.host in {"127.0.0.1", "::1"}:
        raise HTTPException(401, "verified mTLS ingress required")
    row = await session.get(HospitalConnector, connector_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "connector not found")
    if not client_certificate:
        raise HTTPException(401, "client certificate required")
    cert = await session.get(ConnectorCertificate, row.current_certificate_id)
    if cert is None:
        raise HTTPException(403, "active client certificate required")
    try:
        verify_presented_connector_certificate(
            escaped_certificate=client_certificate,
            connector=row,
            certificate=cert,
        )
    except ConnectorControlError as exc:
        raise HTTPException(403, str(exc)) from exc
    return row, cert


@router.post("/ingress/connectors/{connector_id}/manifests")
async def ingress_manifest(
    connector_id: UUID, payload: ManifestRequest, request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    row, _ = await _ingress_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    try:
        manifest = await submit_manifest(session, connector=row, payload=payload.model_dump())
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"id": str(manifest.id), "sequence": manifest.sequence, "manifest_digest": manifest.manifest_digest, "accepted": True}


@router.post("/ingress/connectors/{connector_id}/rotate-certificate")
async def ingress_rotate_certificate(
    connector_id: UUID, payload: CertificateRotationRequest, request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    row, current_certificate = await _ingress_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    try:
        cert = await rotate_certificate(
            session, connector=row,
            current_fingerprint=current_certificate.fingerprint_sha256,
            csr_pem=payload.csr_pem,
        )
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {
        "certificate_pem": cert.certificate_pem.decode("ascii"),
        "certificate_fingerprint": cert.fingerprint_sha256,
        "certificate_valid_to": cert.valid_to,
        "supersedes_certificate_id": str(cert.supersedes_certificate_id),
    }


@router.post("/ingress/connectors/{connector_id}/heartbeat")
async def ingress_heartbeat(
    connector_id: UUID, payload: HeartbeatRequest, request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    row, certificate = await _ingress_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    try:
        heartbeat = await accept_heartbeat(
            session, connector=row,
            certificate_fingerprint=certificate.fingerprint_sha256,
            payload=payload.model_dump(),
        )
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"id": str(heartbeat.id), "sequence": heartbeat.sequence, "acceptance_result": heartbeat.acceptance_result, "server_time": datetime.now().astimezone()}


@router.post("/ingress/connectors/{connector_id}/asset-metadata")
async def ingress_asset_metadata(
    connector_id: UUID, payload: AssetMetadataBundleRequest, request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    row, certificate = await _ingress_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    try:
        mirror, version, created = await accept_asset_metadata_bundle(
            session, connector=row,
            certificate_fingerprint=certificate.fingerprint_sha256,
            payload=payload.model_dump(),
        )
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {
        "mirror_id": str(mirror.id), "version_id": str(version.id),
        "bundle_sequence": version.bundle_sequence, "created": created,
        "metadata_only": True, "execution_permitted": False,
    }


@router.post("/ingress/connectors/{connector_id}/executors/status")
async def ingress_executor_status(
    connector_id: UUID,
    payload: ExecutorStatusRequest | ExecutorFixedExecutionReadinessAttestationV2,
    request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    connector, _ = await _ingress_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    connector_space_id = connector.space_id
    verified_connector_id = connector.id
    try:
        mirror, event, created = await accept_executor_status(
            session, connector=connector, payload=payload.model_dump()
        )
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        await append_control_audit(
            session,
            space_id=connector_space_id,
            event_type="executor.readiness_attestation.rejected",
            subject_type="hospital_connector",
            subject_id=verified_connector_id,
            evidence={
                "reason_code": str(exc),
                "schema_version": payload.schema_version,
                "event_type": payload.event_type,
                "untrusted_payload_retained": False,
            },
            actor_connector_id=verified_connector_id,
        )
        await session.commit()
        raise _error(exc) from exc
    return {
        "mirror_id": str(mirror.id),
        "event_id": str(event.id),
        "status": mirror.status,
        "status_sequence": mirror.last_status_sequence,
        "created": created,
        "execution_enabled": False,
        "hard_isolation": False,
        "fixed_reference_readiness_status":
            mirror.fixed_reference_readiness_status,
        "verified_executor_readiness_attestation":
            mirror.latest_verified_readiness_event_id is not None,
    }


@router.post("/ingress/connectors/{connector_id}/evidence-bundles")
async def ingress_hospital_evidence_bundle(
    connector_id: UUID,
    payload: HospitalEvidenceBundleRequest,
    request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    connector, _ = await _ingress_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    try:
        receipt, created = await accept_hospital_evidence_bundle(
            session, connector=connector, payload=payload.model_dump()
        )
        await session.commit()
    except ConnectorControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {
        "receipt_id": str(receipt.id),
        "bundle_id": str(receipt.bundle_id),
        "bundle_digest": receipt.bundle_digest,
        "verification_status": receipt.verification_status,
        "created": created,
        "artifact_received": False,
        "raw_data_received": False,
        "hard_isolation": False,
    }
