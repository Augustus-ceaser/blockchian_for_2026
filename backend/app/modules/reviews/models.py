from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.modules.identity.models import User, sql_values, utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

REVIEW_TYPES = (
    "application_precheck",
    "provider_review",
    "data_provider_review",
    "model_provider_review",
    "compliance_review",
    "ethics_review",
)
REVIEW_TASK_STATUSES = ("pending", "claimed", "decided", "cancelled")
REVIEW_CANCEL_REASONS = (
    "application_withdrawn",
    "upstream_rejected",
    "administrative_termination",
)
REVIEW_DECISIONS = ("approved", "rejected")
REVIEW_REASON_CODES = (
    "incomplete_materials",
    "missing_ethics_material",
    "subject_not_eligible",
    "policy_conflict",
    "purpose_not_justified",
    "compliance_requirement_not_met",
    "ethics_requirement_not_met",
    "conflict_of_interest",
    "other",
)
REVIEW_REMEDIATIONS = ("clone_and_resubmit",)


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    review_type: Mapped[str] = mapped_column(String(32))
    application_id: Mapped[UUID] = mapped_column()
    application_snapshot_id: Mapped[UUID] = mapped_column()
    target_digest: Mapped[str] = mapped_column(Text)
    assignee_organization_id: Mapped[UUID] = mapped_column()
    assignee_user_id: Mapped[UUID | None] = mapped_column()
    task_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    sequence_no: Mapped[int] = mapped_column(SmallInteger)
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    routing_rule_digest: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.users.id",
            name="fk_review_tasks_created_by",
            ondelete="RESTRICT",
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    decision: Mapped[ReviewDecision | None] = relationship(
        back_populates="review_task",
        uselist=False,
        primaryjoin="ReviewTask.id == ReviewDecision.review_task_id",
        foreign_keys="ReviewDecision.review_task_id",
        passive_deletes=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "space_id"],
            [f"{SCHEMA}.applications.id", f"{SCHEMA}.applications.space_id"],
            name="fk_review_tasks_application_space",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["application_id", "application_snapshot_id", "target_digest"],
            [
                f"{SCHEMA}.application_snapshots.application_id",
                f"{SCHEMA}.application_snapshots.id",
                f"{SCHEMA}.application_snapshots.snapshot_digest",
            ],
            name="fk_review_tasks_snapshot_evidence",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["space_id", "assignee_organization_id"],
            [
                f"{SCHEMA}.space_participants.space_id",
                f"{SCHEMA}.space_participants.organization_id",
            ],
            name="fk_review_tasks_assignee_participant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assignee_organization_id", "assignee_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_review_tasks_assignee_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"review_type IN ({sql_values(REVIEW_TYPES)})", name="review_type"
        ),
        CheckConstraint(
            f"task_status IN ({sql_values(REVIEW_TASK_STATUSES)})",
            name="task_status",
        ),
        CheckConstraint(
            f"cancel_reason IS NULL OR cancel_reason IN "
            f"({sql_values(REVIEW_CANCEL_REASONS)})",
            name="cancel_reason",
        ),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
            "due_at IS NULL OR due_at > created_at", name="due_after_created"
        ),
        CheckConstraint(
            "(task_status = 'pending' AND assignee_user_id IS NULL "
            "AND claimed_at IS NULL AND decided_at IS NULL "
            "AND cancelled_at IS NULL AND cancel_reason IS NULL) OR "
            "(task_status = 'claimed' AND assignee_user_id IS NOT NULL "
            "AND claimed_at IS NOT NULL AND decided_at IS NULL "
            "AND cancelled_at IS NULL AND cancel_reason IS NULL) OR "
            "(task_status = 'decided' AND assignee_user_id IS NOT NULL "
            "AND claimed_at IS NOT NULL AND decided_at IS NOT NULL "
            "AND cancelled_at IS NULL AND cancel_reason IS NULL) OR "
            "(task_status = 'cancelled' AND decided_at IS NULL "
            "AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
            "AND ((assignee_user_id IS NULL AND claimed_at IS NULL) OR "
            "(assignee_user_id IS NOT NULL AND claimed_at IS NOT NULL)))",
            name="lifecycle_shape",
        ),
        UniqueConstraint(
            "application_snapshot_id",
            "review_type",
            name="uq_review_tasks_snapshot_type",
        ),
        UniqueConstraint("id", "target_digest", name="uq_review_tasks_id_digest"),
        UniqueConstraint(
            "id", "assignee_organization_id", name="uq_review_tasks_id_org"
        ),
        UniqueConstraint("id", "assignee_user_id", name="uq_review_tasks_id_user"),
        Index(
            "ix_review_tasks_space_status_sequence",
            "space_id",
            "task_status",
            "sequence_no",
        ),
        Index(
            "ix_review_tasks_assignee_status",
            "assignee_organization_id",
            "task_status",
        ),
        Index("ix_review_tasks_application", "application_id"),
        Index("ix_review_tasks_snapshot", "application_snapshot_id"),
        Index("ix_review_tasks_routing_digest", "routing_rule_digest"),
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_task_id: Mapped[UUID] = mapped_column()
    decision: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    remediation: Mapped[str | None] = mapped_column(String(32))
    decided_by_user_id: Mapped[UUID] = mapped_column()
    decided_for_organization_id: Mapped[UUID] = mapped_column()
    target_digest: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    decision_digest: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    review_task: Mapped[ReviewTask] = relationship(
        back_populates="decision",
        primaryjoin="ReviewDecision.review_task_id == ReviewTask.id",
        foreign_keys=[review_task_id],
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["review_task_id", "target_digest"],
            [f"{SCHEMA}.review_tasks.id", f"{SCHEMA}.review_tasks.target_digest"],
            name="fk_review_decisions_task_digest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["review_task_id", "decided_for_organization_id"],
            [
                f"{SCHEMA}.review_tasks.id",
                f"{SCHEMA}.review_tasks.assignee_organization_id",
            ],
            name="fk_review_decisions_task_org",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["review_task_id", "decided_by_user_id"],
            [
                f"{SCHEMA}.review_tasks.id",
                f"{SCHEMA}.review_tasks.assignee_user_id",
            ],
            name="fk_review_decisions_task_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"decision IN ({sql_values(REVIEW_DECISIONS)})", name="decision"
        ),
        CheckConstraint(
            f"reason_code IS NULL OR reason_code IN "
            f"({sql_values(REVIEW_REASON_CODES)})",
            name="reason_code",
        ),
        CheckConstraint(
            f"remediation IS NULL OR remediation IN "
            f"({sql_values(REVIEW_REMEDIATIONS)})",
            name="remediation",
        ),
        CheckConstraint(
            "(decision = 'approved' AND reason_code IS NULL "
            "AND remediation IS NULL) OR "
            "(decision = 'rejected' AND reason_code IS NOT NULL)",
            name="decision_shape",
        ),
        UniqueConstraint("review_task_id", name="uq_review_decisions_task"),
        UniqueConstraint("decision_digest", name="uq_review_decisions_digest"),
        Index("ix_review_decisions_decided_by", "decided_by_user_id"),
        Index("ix_review_decisions_decided_for", "decided_for_organization_id"),
        Index("ix_review_decisions_decided_at", "decided_at"),
    )
