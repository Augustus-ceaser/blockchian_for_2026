from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.modules.identity.models import sql_values, utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

AUDIT_EVENT_TYPES = (
    "contract.revision.activated",
    "compute.job.created",
    "compute.run.reserved",
    "compute.run.dispatched",
    "compute.run.started",
    "compute.run.completed",
    "compute.run.failed",
    "compute.run.interrupted",
    "artifact.created",
    "artifact.review.decided",
    "artifact.released",
    "data_product.version.created",
    "data_product.version.updated",
    "data_product.version.submitted",
    "data_product.version.returned",
    "data_product.version.approved",
    "data_product.version.published",
    "model_product.version.created",
    "model_product.version.updated",
    "model_product.version.submitted",
    "model_product.version.returned",
    "model_product.version.approved",
    "model_product.version.published",
    "application.created",
    "application.updated",
    "application.compatibility.checked",
    "application.submitted",
    "application.review.decided",
    "application.returned",
    "application.rejected",
    "application.approved",
    "service_access.request.created",
    "service_access.provider.approved",
    "service_access.provider.rejected",
    "service_access.operator.approved",
    "service_access.operator.rejected",
    "commercial.order.created",
    "commercial.agreement.accepted",
    "commercial.payment.succeeded",
    "commercial.fulfillment.created",
    "commercial.download.grant.created",
    "commercial.download.completed",
    "contract.draft.generated",
    "contract.policy.converged",
    "contract.revision.proposed",
    "contract.revision.signed",
    "contract.readiness.confirmed",
    "contract.readiness.revoked",
    "execution.eligibility.passed",
    "execution.eligibility.blocked",
    "execution.eligibility.invalidated",
    "compute.job.pre_dispatch_slot_reserved",
    "artifact.review.plan.created",
    "artifact.multiparty_review.decided",
    "result.package.created",
    "result.download.grant.created",
    "result.download.completed",
    "result.download.rejected",
    "data_product.unpublish.requested",
    "data_product.unpublish.approved",
    "data_product.unpublish.rejected",
    "data_product.unpublish.returned",
    "data_product.unpublished",
    "data_product.relist.requested",
    "data_product.relist.approved",
    "data_product.relist.rejected",
    "data_product.relist.returned",
    "data_product.republished",
    "data_product.deletion.requested",
    "data_product.deletion.approved",
    "data_product.deletion.rejected",
    "data_product.deletion.returned",
    "data_product.archived",
    "model_product.unpublish.requested",
    "model_product.unpublish.approved",
    "model_product.unpublish.rejected",
    "model_product.unpublish.returned",
    "model_product.unpublished",
    "model_product.relist.requested",
    "model_product.relist.approved",
    "model_product.relist.rejected",
    "model_product.relist.returned",
    "model_product.republished",
    "model_product.deletion.requested",
    "model_product.deletion.approved",
    "model_product.deletion.rejected",
    "model_product.deletion.returned",
    "model_product.archived",
    "product.lifecycle.cancelled",
    "external_catalog.sync.succeeded",
    "external_catalog.sync.not_modified",
    "external_catalog.sync.failed",
    "external_catalog.source.created",
    "external_catalog.sync.started",
    "external_catalog.governance.profile.initialized",
    "external_catalog.governance.initialized",
    "external_catalog.governance.recalculated",
    "external_catalog.governance.review.created",
    "external_catalog.governance.review.superseded",
    "external_catalog.duplicate.resolved",
    "external_catalog.governance.duplicate.resolved",
    "external_catalog.productization.eligibility.changed",
    "external_catalog.product.submitted",
    "external_catalog.product.published",
    "external_catalog.product.publication.rejected",
    "external_model_catalog.governance.profile.initialized",
    "external_model_catalog.governance.review.created",
    "external_model_catalog.governance.review.superseded",
    "external_model_catalog.family.resolved",
    "external_model_catalog.governance.recalculated",
    "external_model_catalog.productization.eligibility.changed",
    "external_model_catalog.product.submitted",
    "external_model_catalog.product.published",
    "external_model_catalog.product.publication.rejected",
    "dataset_model_relation.created",
    "dataset_model_evidence.created",
    "dataset_model_evidence.superseded",
    "dataset_model_evidence.execution_backfilled",
    "dataset_model_evidence.verification_backfilled",
    "dataset_model_relation.status_changed",
    "dataset_model_relation.publication_changed",
)
AUDIT_ACTOR_TYPES = ("user", "connector", "system")
AUDIT_SUBJECT_TYPES = (
    "contract_revision",
    "compute_job",
    "compute_run",
    "artifact",
    "artifact_review",
    "data_product_version",
    "model_version",
    "application",
    "review_decision",
    "contract_readiness",
    "contract_readiness_revocation",
    "execution_eligibility",
    "execution_eligibility_invalidation",
    "artifact_review_decision",
    "result_package",
    "result_download_grant",
    "product_lifecycle_request",
    "external_catalog_sync_run",
    "external_catalog_source",
    "dataset_model_relation",
    "service_access_request",
    "commercial_order",
    "commercial_payment",
    "commercial_fulfillment",
    "commercial_download_grant",
)
AUDIT_RESULTS = ("success", "failure", "denied", "interrupted", "cancelled")
OUTBOX_STATUSES = ("pending", "processing", "published", "dead_letter")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    stream_sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(96))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    canonicalization_version: Mapped[str] = mapped_column(
        String(40),
        default="medtrust-jsonb-c14n/v1",
        server_default="medtrust-jsonb-c14n/v1",
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    actor_connector_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.connectors.id", ondelete="RESTRICT")
    )
    actor_service_code: Mapped[str | None] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[UUID] = mapped_column()
    result: Mapped[str] = mapped_column(String(16))
    correlation_id: Mapped[UUID] = mapped_column()
    causation_id: Mapped[UUID | None] = mapped_column()
    command_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(71))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    evidence_digest: Mapped[str] = mapped_column(String(71))
    previous_event_digest: Mapped[str | None] = mapped_column(String(71))
    event_digest: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    outbox_messages: Mapped[list[OutboxMessage]] = relationship(
        back_populates="audit_event",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="OutboxMessage.created_at",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["causation_id", "space_id"],
            [f"{SCHEMA}.audit_events.event_id", f"{SCHEMA}.audit_events.space_id"],
            name="fk_audit_events_causation_space",
            ondelete="RESTRICT",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        CheckConstraint("stream_sequence > 0", name="stream_sequence_positive"),
        CheckConstraint("schema_version = 1", name="schema_version_v1"),
        CheckConstraint(
            "canonicalization_version = 'medtrust-jsonb-c14n/v1'",
            name="canonicalization_version_v1",
        ),
        CheckConstraint(
            f"event_type IN ({sql_values(AUDIT_EVENT_TYPES)})", name="event_type"
        ),
        CheckConstraint(
            f"actor_type IN ({sql_values(AUDIT_ACTOR_TYPES)})", name="actor_type"
        ),
        CheckConstraint(
            f"subject_type IN ({sql_values(AUDIT_SUBJECT_TYPES)})",
            name="subject_type",
        ),
        CheckConstraint(f"result IN ({sql_values(AUDIT_RESULTS)})", name="result"),
        CheckConstraint(
            "(actor_type='user' AND actor_organization_id IS NOT NULL "
            "AND actor_user_id IS NOT NULL AND actor_connector_id IS NULL "
            "AND actor_service_code IS NULL) OR "
            "(actor_type='connector' AND actor_organization_id IS NOT NULL "
            "AND actor_user_id IS NULL AND actor_connector_id IS NOT NULL "
            "AND actor_service_code IS NULL) OR "
            "(actor_type='system' AND actor_user_id IS NULL "
            "AND actor_connector_id IS NULL AND actor_service_code IS NOT NULL)",
            name="actor_shape",
        ),
        CheckConstraint(
            "length(idempotency_key)=71 AND idempotency_key LIKE 'sha256:%'",
            name="idempotency_digest_format",
        ),
        CheckConstraint(
            "length(evidence_digest)=71 AND evidence_digest LIKE 'sha256:%'",
            name="evidence_digest_format",
        ),
        CheckConstraint(
            "length(event_digest)=71 AND event_digest LIKE 'sha256:%'",
            name="event_digest_format",
        ),
        CheckConstraint(
            "previous_event_digest IS NULL OR "
            "(length(previous_event_digest)=71 AND previous_event_digest LIKE 'sha256:%')",
            name="previous_digest_format",
        ),
        CheckConstraint(
            "(stream_sequence=1 AND previous_event_digest IS NULL) OR "
            "(stream_sequence>1 AND previous_event_digest IS NOT NULL)",
            name="chain_shape",
        ),
        UniqueConstraint(
            "event_id", "space_id", name="uq_audit_events_event_space"
        ),
        UniqueConstraint(
            "space_id", "stream_sequence", name="uq_audit_events_space_sequence"
        ),
        UniqueConstraint(
            "space_id", "event_digest", name="uq_audit_events_space_digest"
        ),
        UniqueConstraint(
            "space_id",
            "idempotency_key",
            "event_type",
            "subject_type",
            "subject_id",
            name="uq_audit_events_idempotent_fact",
        ),
        UniqueConstraint(
            "space_id",
            "command_id",
            "event_type",
            "subject_type",
            "subject_id",
            name="uq_audit_events_command_fact",
        ),
        Index(
            "ix_audit_events_space_sequence_desc",
            "space_id",
            text("stream_sequence DESC"),
        ),
        Index(
            "ix_audit_events_space_occurred_desc",
            "space_id",
            text("occurred_at DESC"),
            "event_id",
        ),
        Index(
            "ix_audit_events_subject_occurred_desc",
            "subject_type",
            "subject_id",
            text("occurred_at DESC"),
        ),
        Index("ix_audit_events_correlation", "correlation_id", "occurred_at"),
        Index(
            "ix_audit_events_space_command_sequence",
            "space_id",
            "command_id",
            "stream_sequence",
        ),
        Index(
            "ix_audit_events_space_idempotency_sequence",
            "space_id",
            "idempotency_key",
            "stream_sequence",
        ),
        Index("ix_audit_events_type_occurred", "event_type", text("occurred_at DESC")),
    )


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    message_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    audit_event_id: Mapped[UUID] = mapped_column()
    space_id: Mapped[UUID] = mapped_column()
    topic: Mapped[str] = mapped_column(String(96))
    destination: Mapped[str] = mapped_column(String(96))
    message_schema_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1"
    )
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71))
    idempotency_key: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_owner: Mapped[str | None] = mapped_column(String(96))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1024))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    audit_event: Mapped[AuditEvent] = relationship(back_populates="outbox_messages")

    __table_args__ = (
        ForeignKeyConstraint(
            ["audit_event_id", "space_id"],
            [f"{SCHEMA}.audit_events.event_id", f"{SCHEMA}.audit_events.space_id"],
            name="fk_outbox_messages_event_space",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(OUTBOX_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="attempt_count_range",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint("message_schema_version = 1", name="schema_version_v1"),
        CheckConstraint(
            "length(payload_digest)=71 AND payload_digest LIKE 'sha256:%'",
            name="payload_digest_format",
        ),
        CheckConstraint(
            "length(idempotency_key)=71 AND idempotency_key LIKE 'sha256:%'",
            name="idempotency_digest_format",
        ),
        CheckConstraint(
            "(status='pending' AND locked_at IS NULL AND lock_owner IS NULL "
            "AND lease_expires_at IS NULL AND published_at IS NULL) OR "
            "(status='processing' AND locked_at IS NOT NULL AND lock_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND published_at IS NULL) OR "
            "(status='published' AND locked_at IS NULL AND lock_owner IS NULL "
            "AND lease_expires_at IS NULL AND published_at IS NOT NULL "
            "AND last_error IS NULL) OR "
            "(status='dead_letter' AND locked_at IS NULL AND lock_owner IS NULL "
            "AND lease_expires_at IS NULL AND published_at IS NULL)",
            name="delivery_shape",
        ),
        UniqueConstraint(
            "message_id", "space_id", name="uq_outbox_messages_message_space"
        ),
        UniqueConstraint(
            "audit_event_id",
            "topic",
            "destination",
            name="uq_outbox_messages_event_target",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_outbox_messages_idempotency_key"
        ),
        Index(
            "ix_outbox_messages_pending_claim",
            "available_at",
            "created_at",
            "message_id",
            postgresql_where=text("status='pending'"),
            sqlite_where=text("status='pending'"),
        ),
        Index(
            "ix_outbox_messages_processing_lease",
            "lease_expires_at",
            "message_id",
            postgresql_where=text("status='processing'"),
            sqlite_where=text("status='processing'"),
        ),
        Index(
            "ix_outbox_messages_destination_status_available",
            "destination",
            "status",
            "available_at",
        ),
        Index("ix_outbox_messages_space_created", "space_id", text("created_at DESC")),
        Index("ix_outbox_messages_event", "audit_event_id"),
        Index("ix_outbox_messages_status_updated", "status", "updated_at"),
    )
