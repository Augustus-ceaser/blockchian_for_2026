from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
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

CALLBACK_TYPES = (
    "execution.started",
    "execution.completed",
    "execution.failed",
    "execution.interrupted",
)
CALLBACK_INBOX_STATUSES = ("received", "processing", "completed", "dead_letter")
CALLBACK_OUTCOME_CODES = (
    "run_started",
    "run_completed",
    "run_failed",
    "run_interrupted",
    "duplicate_fact",
    "terminal_noop",
    "non_retryable_rejection",
)


class ExecutionCallbackInboxEntry(Base):
    """Durable receipt and processing lease for one external Executor callback."""

    __tablename__ = "execution_callback_inbox_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    compute_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.compute_runs.id", ondelete="RESTRICT")
    )
    executor_namespace: Mapped[str] = mapped_column(String(96))
    external_execution_id: Mapped[str] = mapped_column(String(256))
    callback_id: Mapped[str] = mapped_column(String(160))
    callback_type: Mapped[str] = mapped_column(String(32))
    callback_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71))
    normalized_fact_digest: Mapped[str] = mapped_column(String(71))
    execution_evidence_digest: Mapped[str] = mapped_column(String(71))
    authentication_evidence_digest: Mapped[str] = mapped_column(String(71))
    correlation_id: Mapped[UUID] = mapped_column()
    causation_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(16), default="received", server_default="received"
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_owner: Mapped[str | None] = mapped_column(String(96))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_code: Mapped[str | None] = mapped_column(String(64))
    outcome_reference_type: Mapped[str | None] = mapped_column(String(32))
    outcome_reference_id: Mapped[UUID | None] = mapped_column()
    processing_error: Mapped[str | None] = mapped_column(String(1024))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            f"callback_type IN ({sql_values(CALLBACK_TYPES)})", name="callback_type"
        ),
        CheckConstraint("callback_schema_version=1", name="schema_version"),
        CheckConstraint(
            f"status IN ({sql_values(CALLBACK_INBOX_STATUSES)})", name="status"
        ),
        CheckConstraint("attempt_count >= 0 AND attempt_count <= 10", name="attempt_count_range"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
            "length(payload_digest)=71 AND payload_digest LIKE 'sha256:%'",
            name="payload_digest_format",
        ),
        CheckConstraint(
            "length(normalized_fact_digest)=71 AND normalized_fact_digest LIKE 'sha256:%'",
            name="fact_digest_format",
        ),
        CheckConstraint(
            "length(execution_evidence_digest)=71 AND execution_evidence_digest LIKE 'sha256:%'",
            name="execution_digest_format",
        ),
        CheckConstraint(
            "length(authentication_evidence_digest)=71 AND authentication_evidence_digest LIKE 'sha256:%'",
            name="authentication_digest_format",
        ),
        CheckConstraint(
            "outcome_code IS NULL OR outcome_code IN "
            f"({sql_values(CALLBACK_OUTCOME_CODES)})",
            name="outcome_code",
        ),
        CheckConstraint(
            "(outcome_reference_type IS NULL) = (outcome_reference_id IS NULL)",
            name="outcome_reference_shape",
        ),
        CheckConstraint(
            "(status='received' AND locked_at IS NULL AND lock_owner IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NULL AND terminal_at IS NULL "
            "AND outcome_code IS NULL AND outcome_reference_type IS NULL "
            "AND outcome_reference_id IS NULL) OR "
            "(status='processing' AND locked_at IS NOT NULL AND lock_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND processing_started_at IS NOT NULL "
            "AND completed_at IS NULL AND terminal_at IS NULL AND outcome_code IS NULL "
            "AND outcome_reference_type IS NULL AND outcome_reference_id IS NULL) OR "
            "(status='completed' AND locked_at IS NULL AND lock_owner IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NOT NULL AND terminal_at IS NOT NULL "
            "AND outcome_code IS NOT NULL) OR "
            "(status='dead_letter' AND locked_at IS NULL AND lock_owner IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NULL AND terminal_at IS NOT NULL "
            "AND processing_error IS NOT NULL)",
            name="lifecycle_shape",
        ),
        UniqueConstraint(
            "executor_namespace", "callback_id", name="uq_execution_callback_namespace_id"
        ),
        UniqueConstraint(
            "executor_namespace",
            "compute_run_id",
            "callback_type",
            "normalized_fact_digest",
            name="uq_execution_callback_semantic_fact",
        ),
        Index(
            "ix_execution_callback_received_claim",
            "available_at",
            "received_at",
            "id",
            postgresql_where=text("status='received'"),
            sqlite_where=text("status='received'"),
        ),
        Index(
            "ix_execution_callback_processing_lease",
            "lease_expires_at",
            "id",
            postgresql_where=text("status='processing'"),
            sqlite_where=text("status='processing'"),
        ),
        Index("ix_execution_callback_run_timeline", "compute_run_id", "occurred_at", "id"),
        Index("ix_execution_callback_space_received", "space_id", text("received_at DESC")),
        Index(
            "ix_execution_callback_external_execution",
            "executor_namespace",
            "external_execution_id",
        ),
    )
