from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.identity.models import sql_values, utc_now

SCHEMA = "medtrust"

INBOX_STATUSES = ("received", "processing", "completed", "dead_letter")
INBOX_OUTCOME_CODES = (
    "executor_submitted",
    "already_dispatched",
    "authorization_revoked",
    "ignored_terminal_run",
    "non_retryable_rejection",
)


class ConsumerInboxEntry(Base):
    """Durable, consumer-scoped receipt and processing lease for one AuditEvent."""

    __tablename__ = "consumer_inbox_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    consumer_name: Mapped[str] = mapped_column(String(96))
    event_id: Mapped[UUID] = mapped_column()
    source_message_id: Mapped[UUID] = mapped_column()
    space_id: Mapped[UUID] = mapped_column()
    payload_digest: Mapped[str] = mapped_column(String(71))
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
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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
        ForeignKeyConstraint(
            ["event_id", "space_id"],
            [f"{SCHEMA}.audit_events.event_id", f"{SCHEMA}.audit_events.space_id"],
            name="fk_consumer_inbox_entries_event_space",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_message_id", "space_id"],
            [
                f"{SCHEMA}.outbox_messages.message_id",
                f"{SCHEMA}.outbox_messages.space_id",
            ],
            name="fk_consumer_inbox_entries_message_space",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(INBOX_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="attempt_count_range",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
            "length(payload_digest)=71 AND payload_digest LIKE 'sha256:%'",
            name="payload_digest_format",
        ),
        CheckConstraint(
            "outcome_code IS NULL OR outcome_code IN "
            f"({sql_values(INBOX_OUTCOME_CODES)})",
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
            "consumer_name", "event_id", name="uq_consumer_inbox_entries_consumer_event"
        ),
        Index(
            "ix_consumer_inbox_entries_received_claim",
            "consumer_name",
            "available_at",
            "received_at",
            "id",
            postgresql_where=text("status='received'"),
            sqlite_where=text("status='received'"),
        ),
        Index(
            "ix_consumer_inbox_entries_processing_lease",
            "consumer_name",
            "lease_expires_at",
            "id",
            postgresql_where=text("status='processing'"),
            sqlite_where=text("status='processing'"),
        ),
        Index("ix_consumer_inbox_entries_source_message", "source_message_id"),
        Index("ix_consumer_inbox_entries_space_received", "space_id", "received_at"),
        Index("ix_consumer_inbox_entries_event", "event_id"),
    )
