from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.identity.models import utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class PolicySigningKey(Base):
    __tablename__ = "policy_signing_keys"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    key_id: Mapped[str] = mapped_column(String(100), unique=True)
    algorithm: Mapped[str] = mapped_column(String(24), default="Ed25519", server_default="Ed25519")
    public_key_fingerprint: Mapped[str] = mapped_column(String(71), unique=True)
    public_key_material: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="active", server_default="active")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_key_id: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (
        CheckConstraint("algorithm = 'Ed25519'", name="algorithm"),
        CheckConstraint("status IN ('generated','active','rotation_pending','superseded','revoked','expired')", name="status"),
        Index("ix_policy_signing_keys_space_status", "space_id", "status"),
    )


class ControlReadinessSnapshot(Base):
    __tablename__ = "control_readiness_snapshots"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    application_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id", ondelete="RESTRICT"))
    contract_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.contracts.id", ondelete="RESTRICT"))
    contract_revision_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.contract_revisions.id", ondelete="RESTRICT"))
    central_asset_record_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.connector_asset_mirrors.id", ondelete="RESTRICT"))
    central_asset_version_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.connector_asset_mirror_versions.id", ondelete="RESTRICT"))
    model_product_version_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT"))
    readiness_mode: Mapped[str] = mapped_column(String(40), default="CONTROL_POLICY_VALIDATION", server_default="CONTROL_POLICY_VALIDATION")
    requested_action: Mapped[str] = mapped_column(String(48), default="VALIDATE_POLICY_ONLY", server_default="VALIDATE_POLICY_ONLY")
    task_type: Mapped[str | None] = mapped_column(String(48))
    # Migration 0056 owns this cross-phase FK because historical migration
    # 0052 imports live metadata before the status-event table exists.
    source_executor_status_event_id: Mapped[UUID | None] = mapped_column()
    source_executor_status_event_digest: Mapped[str | None] = mapped_column(String(71))
    source_attestation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_asset_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.connector_asset_mirror_versions.id",
            ondelete="RESTRICT",
        )
    )
    source_asset_metadata_digest: Mapped[str | None] = mapped_column(String(71))
    source_quality_digest: Mapped[str | None] = mapped_column(String(71))
    source_model_reference_digest: Mapped[str | None] = mapped_column(String(71))
    source_contract_digest: Mapped[str | None] = mapped_column(String(71))
    source_application_digest: Mapped[str | None] = mapped_column(String(71))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(16))
    readiness_digest: Mapped[str] = mapped_column(String(71), unique=True)
    execution_authorized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    hard_isolation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    __table_args__ = (
        CheckConstraint("readiness_mode IN ('CONTROL_POLICY_VALIDATION','FIXED_REFERENCE_EXECUTION')", name="mode"),
        CheckConstraint("status IN ('passed','blocked')", name="status"),
        CheckConstraint(
            "(readiness_mode='CONTROL_POLICY_VALIDATION' AND NOT execution_authorized "
            "AND requested_action='VALIDATE_POLICY_ONLY' AND task_type IS NULL) OR "
            "(readiness_mode='FIXED_REFERENCE_EXECUTION' AND execution_authorized "
            "AND requested_action='EXECUTE_FIXED_REFERENCE_TASK' "
            "AND task_type='PATHMNIST_REFERENCE_V1')",
            name="authorization_mode",
        ),
        CheckConstraint("NOT hard_isolation", name="hard_isolation_false"),
    )


class PolicyBundle(Base):
    __tablename__ = "policy_bundles"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    policy_key: Mapped[str] = mapped_column(String(100), unique=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"))
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    application_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id", ondelete="RESTRICT"))
    contract_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.contracts.id", ondelete="RESTRICT"))
    control_readiness_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.control_readiness_snapshots.id", ondelete="RESTRICT"))
    current_version_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.policy_bundle_versions.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    created_by: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('draft','compiled','signed','active','superseded','revoked','expired','rejected')", name="status"),
        Index("ix_policy_bundles_connector_status", "connector_id", "status"),
    )


class PolicyBundleVersion(Base):
    __tablename__ = "policy_bundle_versions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    policy_bundle_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.policy_bundles.id", ondelete="RESTRICT"))
    schema_version: Mapped[str] = mapped_column(String(48))
    version: Mapped[int] = mapped_column(Integer)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    central_asset_version_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.connector_asset_mirror_versions.id", ondelete="RESTRICT"))
    model_product_version_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT"))
    requested_action: Mapped[str] = mapped_column(String(40), default="VALIDATE_POLICY_ONLY", server_default="VALIDATE_POLICY_ONLY")
    execution_authorized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    execution_scope: Mapped[str | None] = mapped_column(String(40))
    task_type: Mapped[str | None] = mapped_column(String(48))
    max_execution_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    canonical_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    signing_key_id: Mapped[str] = mapped_column(String(100))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    nonce: Mapped[str] = mapped_column(String(96), unique=True)
    supersedes_version_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.policy_bundle_versions.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("policy_bundle_id", "version", name="uq_policy_bundle_version"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(requested_action='VALIDATE_POLICY_ONLY' AND NOT execution_authorized "
            "AND execution_scope IS NULL AND task_type IS NULL AND max_execution_count=0) OR "
            "(requested_action='EXECUTE_FIXED_REFERENCE_TASK' AND execution_authorized "
            "AND execution_scope='FIXED_REFERENCE_ONLY' "
            "AND task_type='PATHMNIST_REFERENCE_V1' AND max_execution_count=1)",
            name="authorization_mode",
        ),
    )


class PolicyRevocation(Base):
    __tablename__ = "policy_revocations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    policy_bundle_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.policy_bundles.id", ondelete="RESTRICT"))
    policy_bundle_version_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.policy_bundle_versions.id", ondelete="RESTRICT"))
    revocation_id: Mapped[str] = mapped_column(String(100), unique=True)
    reason_code: Mapped[str] = mapped_column(String(64))
    reason_text: Mapped[str] = mapped_column(Text)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    nonce: Mapped[str] = mapped_column(String(96), unique=True)
    signing_key_id: Mapped[str] = mapped_column(String(100))
    payload_digest: Mapped[str] = mapped_column(String(71), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class ExecutionOrder(Base):
    __tablename__ = "execution_orders"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_key: Mapped[str] = mapped_column(String(100), unique=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    order_mode: Mapped[str] = mapped_column(String(40), default="CONTROL_VALIDATION_ONLY", server_default="CONTROL_VALIDATION_ONLY")
    requested_action: Mapped[str] = mapped_column(String(40), default="VALIDATE_POLICY_ONLY", server_default="VALIDATE_POLICY_ONLY")
    execution_authorized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    execution_scope: Mapped[str | None] = mapped_column(String(40))
    task_type: Mapped[str | None] = mapped_column(String(48))
    max_execution_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consumed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The cross-phase FK is owned by migration 0055. Keeping it out of live
    # metadata prevents historical migration 0052 from referencing the 0053 table.
    executor_id: Mapped[UUID | None] = mapped_column()
    policy_bundle_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.policy_bundles.id", ondelete="RESTRICT"))
    policy_bundle_version_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.policy_bundle_versions.id", ondelete="RESTRICT"))
    policy_payload_digest: Mapped[str] = mapped_column(String(71))
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    connector_sequence: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    correlation_id: Mapped[str] = mapped_column(String(100))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    nonce: Mapped[str] = mapped_column(String(96), unique=True)
    signing_key_id: Mapped[str] = mapped_column(String(100))
    canonical_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="available_for_connector", server_default="available_for_connector")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("connector_id", "connector_sequence", name="uq_execution_order_connector_sequence"),
        CheckConstraint("connector_sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "(order_mode='CONTROL_VALIDATION_ONLY' AND requested_action='VALIDATE_POLICY_ONLY' "
            "AND NOT execution_authorized AND execution_scope IS NULL "
            "AND task_type IS NULL AND max_execution_count=0) OR "
            "(order_mode='FIXED_REFERENCE_EXECUTION' "
            "AND requested_action='EXECUTE_FIXED_REFERENCE_TASK' "
            "AND execution_authorized AND execution_scope='FIXED_REFERENCE_ONLY' "
            "AND task_type='PATHMNIST_REFERENCE_V1' AND max_execution_count=1 "
            "AND executor_id IS NOT NULL)",
            name="authorization_mode",
        ),
        CheckConstraint("consumed_count >= 0 AND consumed_count <= max_execution_count", name="consumption_limit"),
        CheckConstraint("status IN ('draft','issued','available_for_connector','delivered','received','validating','validation_failed','awaiting_local_review','accepted','rejected','revoked','expired','cancelled','delivery_failed')", name="status"),
        Index("ix_execution_orders_connector_status", "connector_id", "status"),
    )


class ExecutionOrderDeliveryAttempt(Base):
    __tablename__ = "execution_order_delivery_attempts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_order_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.execution_orders.id", ondelete="RESTRICT"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[str] = mapped_column(String(24))
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_digest: Mapped[str | None] = mapped_column(String(71))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    __table_args__ = (UniqueConstraint("execution_order_id", "attempt_number", name="uq_order_delivery_attempt"),)


class ConnectorOrderReceipt(Base):
    __tablename__ = "connector_order_receipts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_order_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.execution_orders.id", ondelete="RESTRICT"), unique=True)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    receipt_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    connector_key_id: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class ConnectorOrderDecision(Base):
    __tablename__ = "connector_order_decisions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_order_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.execution_orders.id", ondelete="RESTRICT"), unique=True)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(24))
    reason_code: Mapped[str] = mapped_column(String(80))
    reason_text: Mapped[str] = mapped_column(Text)
    decision_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    connector_key_id: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    __table_args__ = (CheckConstraint("decision IN ('accepted','rejected','validation_failed','revoked_after_acceptance')", name="decision"),)


class ExecutionOrderConsumptionReceipt(Base):
    __tablename__ = "execution_order_consumption_receipts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_order_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.execution_orders.id", ondelete="RESTRICT"),
        unique=True,
    )
    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.hospital_connectors.id", ondelete="RESTRICT")
    )
    authorization_snapshot_id: Mapped[str] = mapped_column(String(36), unique=True)
    task_manifest_id: Mapped[str] = mapped_column(String(36), unique=True)
    runtime_session_id: Mapped[str] = mapped_column(String(36), unique=True)
    reference_execution_id: Mapped[str] = mapped_column(String(36), unique=True)
    consumption_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    connector_key_id: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
