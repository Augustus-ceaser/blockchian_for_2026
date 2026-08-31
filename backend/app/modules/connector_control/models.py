from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.identity.models import sql_values, utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

CONNECTOR_STATUSES = (
    "pending_registration", "pending_approval", "pending_certificate",
    "active", "paused", "offline", "revoked", "certificate_expired",
    "certificate_rotation_required", "error", "archived",
)
REGISTRATION_STATUSES = (
    "submitted", "under_review", "approved", "rejected", "expired",
    "certificate_issued", "consumed", "superseded", "cancelled",
)
CERTIFICATE_STATUSES = ("issued", "active", "rotation_pending", "superseded", "revoked", "expired")
TOKEN_STATUSES = ("active", "consumed", "expired", "cancelled")


class HospitalConnector(Base):
    __tablename__ = "hospital_connectors"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"))
    connector_instance_id: Mapped[str] = mapped_column(String(80), unique=True)
    installation_digest: Mapped[str] = mapped_column(String(71), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    environment: Mapped[str] = mapped_column(String(24), default="local_test", server_default="local_test")
    connector_version: Mapped[str] = mapped_column(String(32))
    operating_system: Mapped[str] = mapped_column(String(40))
    architecture: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(40), default="pending_approval", server_default="pending_approval")
    current_certificate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.connector_certificates.id", ondelete="RESTRICT")
    )
    current_capability_manifest_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.connector_capability_manifests.id", ondelete="RESTRICT")
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    heartbeat_status: Mapped[str] = mapped_column(String(16), default="never", server_default="never")
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_by: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now())
    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(CONNECTOR_STATUSES)})", name="status"),
        CheckConstraint("last_heartbeat_sequence >= 0", name="heartbeat_sequence_nonnegative"),
        Index("ix_hospital_connectors_status_heartbeat", "status", "last_heartbeat_at"),
        Index("ix_hospital_connectors_org_status", "organization_id", "status"),
    )


class ConnectorEnrollmentToken(Base):
    __tablename__ = "connector_enrollment_tokens"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"))
    connector_name: Mapped[str] = mapped_column(String(120))
    token_digest: Mapped[str] = mapped_column(String(71), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_connector_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.connector_registration_requests.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(TOKEN_STATUSES)})", name="status"),
        Index("ix_connector_enrollment_tokens_status_expires", "status", "expires_at"),
    )


class ConnectorRegistrationRequest(Base):
    __tablename__ = "connector_registration_requests"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enrollment_token_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.connector_enrollment_tokens.id", ondelete="RESTRICT"))
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"))
    connector_instance_id: Mapped[str] = mapped_column(String(80), unique=True)
    installation_digest: Mapped[str] = mapped_column(String(71))
    display_name: Mapped[str] = mapped_column(String(120))
    csr_pem: Mapped[bytes] = mapped_column(LargeBinary)
    csr_fingerprint: Mapped[str] = mapped_column(String(71), unique=True)
    connector_version: Mapped[str] = mapped_column(String(32))
    operating_system: Mapped[str] = mapped_column(String(40))
    architecture: Mapped[str] = mapped_column(String(24))
    bootstrap_manifest_digest: Mapped[str] = mapped_column(String(71))
    nonce: Mapped[str] = mapped_column(String(96), unique=True)
    request_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(28), default="submitted", server_default="submitted")
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    connector_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(REGISTRATION_STATUSES)})", name="status"),
        Index("ix_connector_registration_status_created", "status", "created_at"),
    )


class ConnectorCertificate(Base):
    __tablename__ = "connector_certificates"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    serial_number: Mapped[str] = mapped_column(String(80), unique=True)
    subject: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(Text)
    fingerprint_sha256: Mapped[str] = mapped_column(String(71), unique=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[str] = mapped_column(String(80))
    certificate_pem: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(24), default="issued", server_default="issued")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    supersedes_certificate_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.connector_certificates.id", ondelete="RESTRICT"))
    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(CERTIFICATE_STATUSES)})", name="status"),
        Index("ix_connector_certificates_connector_status", "connector_id", "status"),
    )


class ConnectorCapabilityManifest(Base):
    __tablename__ = "connector_capability_manifests"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    schema_version: Mapped[str] = mapped_column(String(40))
    manifest_version: Mapped[str] = mapped_column(String(40))
    sequence: Mapped[int] = mapped_column(Integer)
    connector_version: Mapped[str] = mapped_column(String(32))
    operating_system: Mapped[str] = mapped_column(String(40))
    architecture: Mapped[str] = mapped_column(String(24))
    capability_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    execution_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    data_transfer_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    model_transfer_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    local_asset_registry_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    metadata_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    data_quality_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    artifact_egress_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    hard_isolation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    isolation_maturity: Mapped[str] = mapped_column(String(8), default="L1", server_default="L1")
    manifest_digest: Mapped[str] = mapped_column(String(71))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    __table_args__ = (
        UniqueConstraint("connector_id", "sequence", name="uq_connector_manifest_sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("NOT execution_enabled AND NOT data_transfer_enabled AND NOT model_transfer_enabled AND NOT artifact_egress_enabled AND NOT hard_isolation", name="alpha_capabilities_disabled"),
        CheckConstraint("isolation_maturity IN ('L0','L1')", name="alpha_maturity"),
        Index("ix_connector_manifest_current", "connector_id", "is_current"),
    )


class ConnectorHeartbeat(Base):
    __tablename__ = "connector_heartbeats"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    sequence: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    status: Mapped[str] = mapped_column(String(16))
    connector_version: Mapped[str] = mapped_column(String(32))
    capability_manifest_digest: Mapped[str] = mapped_column(String(71))
    local_audit_head: Mapped[str] = mapped_column(String(71))
    health_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    nonce: Mapped[str] = mapped_column(String(96), unique=True)
    message_digest: Mapped[str] = mapped_column(String(71), unique=True)
    certificate_fingerprint: Mapped[str] = mapped_column(String(71))
    acceptance_result: Mapped[str] = mapped_column(String(40))
    __table_args__ = (
        UniqueConstraint("connector_id", "sequence", name="uq_connector_heartbeat_sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        Index("ix_connector_heartbeats_connector_received", "connector_id", "received_at"),
    )


class ConnectorControlAuditEvent(Base):
    __tablename__ = "connector_control_audit_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    stream_sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(48))
    subject_id: Mapped[UUID] = mapped_column()
    actor_type: Mapped[str] = mapped_column(String(24))
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    actor_connector_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    previous_event_digest: Mapped[str | None] = mapped_column(String(71))
    event_digest: Mapped[str] = mapped_column(String(71), unique=True)
    __table_args__ = (
        UniqueConstraint("space_id", "stream_sequence", name="uq_connector_control_audit_sequence"),
        CheckConstraint("stream_sequence > 0", name="stream_sequence_positive"),
        CheckConstraint("actor_type IN ('operator','hospital_connector','system')", name="actor_type"),
        Index("ix_connector_control_audit_subject", "subject_type", "subject_id", "stream_sequence"),
    )


class ConnectorAssetMirror(Base):
    __tablename__ = "connector_asset_mirrors"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"))
    local_asset_key: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(160))
    asset_kind: Mapped[str] = mapped_column(String(32))
    modality: Mapped[str] = mapped_column(String(64))
    source_category: Mapped[str] = mapped_column(String(40))
    sensitivity_classification: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32))
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.connector_asset_mirror_versions.id", ondelete="RESTRICT")
    )
    first_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("connector_id", "local_asset_key", name="uq_connector_asset_mirror_key"),
        CheckConstraint("status IN ('synced','paused','unavailable','archived')", name="status"),
        Index("ix_connector_asset_mirrors_space_status", "space_id", "status"),
    )


class ConnectorAssetMirrorVersion(Base):
    __tablename__ = "connector_asset_mirror_versions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    mirror_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.connector_asset_mirrors.id", ondelete="RESTRICT"))
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    bundle_id: Mapped[str] = mapped_column(String(80))
    bundle_sequence: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(40))
    metadata_digest: Mapped[str] = mapped_column(String(71))
    schema_digest: Mapped[str] = mapped_column(String(71))
    quality_digest: Mapped[str] = mapped_column(String(71))
    bundle_digest: Mapped[str] = mapped_column(String(71))
    disclosure_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    metadata_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    deidentification_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    known_limitations: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    warning_flags: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("connector_id", "bundle_id", name="uq_connector_asset_bundle_id"),
        UniqueConstraint("connector_id", "bundle_sequence", name="uq_connector_asset_bundle_sequence"),
        CheckConstraint("bundle_sequence > 0", name="bundle_sequence_positive"),
        Index("ix_connector_asset_mirror_versions_mirror_received", "mirror_id", "received_at"),
    )


class HospitalExecutorMirror(Base):
    __tablename__ = "hospital_executor_mirrors"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT")
    )
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    executor_instance_id: Mapped[str] = mapped_column(String(96))
    local_executor_id: Mapped[str | None] = mapped_column(String(36))
    executor_version: Mapped[str] = mapped_column(String(32))
    architecture: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    certificate_fingerprint: Mapped[str] = mapped_column(String(71))
    capability_manifest_digest: Mapped[str] = mapped_column(String(71))
    runtime_digest: Mapped[str] = mapped_column(String(71))
    image_digest: Mapped[str] = mapped_column(String(71))
    security_status: Mapped[str] = mapped_column(String(24))
    last_status_sequence: Mapped[int] = mapped_column(Integer)
    last_heartbeat_sequence: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    hard_isolation: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    latest_status_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.hospital_executor_status_events.id", ondelete="RESTRICT")
    )
    latest_status_event_sequence: Mapped[int | None] = mapped_column(Integer)
    latest_status_event_digest: Mapped[str | None] = mapped_column(String(71))
    latest_status_schema_version: Mapped[str | None] = mapped_column(String(64))
    latest_verified_readiness_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.hospital_executor_status_events.id", ondelete="RESTRICT")
    )
    latest_verified_readiness_digest: Mapped[str | None] = mapped_column(String(71))
    latest_verified_readiness_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    readiness_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fixed_reference_readiness_status: Mapped[str | None] = mapped_column(String(24))
    fixed_reference_readiness_reason: Mapped[str | None] = mapped_column(String(80))
    attested_image_digest: Mapped[str | None] = mapped_column(String(71))
    attested_security_profile_digest: Mapped[str | None] = mapped_column(String(71))
    attested_resource_policy_digest: Mapped[str | None] = mapped_column(String(71))
    attested_admission_digest: Mapped[str | None] = mapped_column(String(71))
    attested_capability_digest: Mapped[str | None] = mapped_column(String(71))
    first_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "connector_id", "executor_instance_id",
            name="uq_hospital_executor_mirror_instance",
        ),
        CheckConstraint(
            "status IN ('pending','approved','active','paused','revoked','offline')",
            name="status",
        ),
        CheckConstraint(
            "security_status IN ('pending','passed','failed','revoked')",
            name="security_status",
        ),
        CheckConstraint(
            "last_status_sequence > 0 AND last_heartbeat_sequence >= 0",
            name="sequences_valid",
        ),
        CheckConstraint(
            "NOT execution_enabled AND NOT hard_isolation",
            name="executor_alpha_disabled",
        ),
        CheckConstraint(
            "fixed_reference_readiness_status IS NULL OR "
            "fixed_reference_readiness_status IN ('ready','not_ready')",
            name="fixed_readiness_status",
        ),
        Index("ix_hospital_executor_mirror_space_status", "space_id", "status"),
        Index(
            "ix_hospital_executor_mirror_connector_heartbeat",
            "connector_id", "last_heartbeat_at",
        ),
    )


class HospitalExecutorStatusEvent(Base):
    __tablename__ = "hospital_executor_status_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    mirror_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.hospital_executor_mirrors.id", ondelete="RESTRICT")
    )
    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT")
    )
    status_sequence: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    payload_digest: Mapped[str] = mapped_column(String(71), unique=True)
    nonce: Mapped[str | None] = mapped_column(String(96), unique=True)
    signing_key_id: Mapped[str | None] = mapped_column(String(80))
    signature: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(
        String(16), default="verified", server_default="verified"
    )
    verification_reason: Mapped[str | None] = mapped_column(String(80))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "mirror_id", "status_sequence",
            name="uq_executor_status_event_sequence",
        ),
        CheckConstraint("status_sequence > 0", name="status_sequence_positive"),
        CheckConstraint(
            "event_type IN ('registered','heartbeat','paused','resumed','revoked',"
            "'EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION')",
            name="event_type",
        ),
        CheckConstraint(
            "verification_status IN ('verified','rejected')",
            name="verification_status",
        ),
        Index(
            "ix_executor_status_events_mirror_received",
            "mirror_id", "received_at",
        ),
    )


class HospitalEvidenceBundleReceipt(Base):
    __tablename__ = "hospital_evidence_bundle_receipts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    bundle_id: Mapped[UUID] = mapped_column(unique=True)
    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT")
    )
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    schema_version: Mapped[str] = mapped_column(String(64))
    bundle_version: Mapped[int] = mapped_column(Integer)
    local_artifact_ref: Mapped[str] = mapped_column(String(36), unique=True)
    reference_execution_id: Mapped[str] = mapped_column(String(36), unique=True)
    policy_bundle_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.policy_bundles.id", ondelete="RESTRICT")
    )
    policy_bundle_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.policy_bundle_versions.id", ondelete="RESTRICT")
    )
    execution_order_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.execution_orders.id", ondelete="RESTRICT")
    )
    artifact_digest: Mapped[str] = mapped_column(String(71), unique=True)
    review_digest: Mapped[str] = mapped_column(String(71), unique=True)
    causal_validation_digest: Mapped[str] = mapped_column(
        String(71), unique=True
    )
    local_audit_head: Mapped[str] = mapped_column(String(71))
    bundle_digest: Mapped[str] = mapped_column(String(71), unique=True)
    signing_key_id: Mapped[str] = mapped_column(String(100))
    signature: Mapped[str] = mapped_column(Text)
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    verification_status: Mapped[str] = mapped_column(
        String(16), default="verified", server_default="verified"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("bundle_version = 1", name="bundle_version"),
        CheckConstraint(
            "schema_version = 'phase5.13E-Final/evidence-bundle/v1'",
            name="schema_version",
        ),
        CheckConstraint(
            "verification_status = 'verified'",
            name="verification_status",
        ),
        Index(
            "ix_hospital_evidence_bundle_connector_received",
            "connector_id", "received_at",
        ),
    )
