from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.modules.catalog.models import DataProduct, DataProductVersion
from app.modules.identity.models import Organization, User, sql_values, utc_now
from app.modules.spaces.models import Space

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

APPLICATION_STATUSES = (
    "draft",
    "submitted",
    "prechecking",
    "provider_review",
    "approved",
    "rejected",
    "withdrawn",
)
DIGEST_ALGORITHMS = ("sha256",)
APPLICATION_ACTION_CODES = (
    "ai_training",
    "model_validation",
    "research_analysis",
    "drug_development",
)
APPLICATION_OUTPUT_TYPES = (
    "aggregate_statistics",
    "model_artifact",
    "feature_dataset",
    "risk_scoring_model",
)
APPLICATION_ATTACHMENT_TYPES = (
    "research_protocol",
    "ethics",
    "authorization",
    "algorithm_document",
    "compliance_evidence",
    "other",
)
APPLICATION_ATTACHMENT_SCAN_STATUSES = ("pending", "clean", "rejected")


def action_parameters_default() -> dict[str, str]:
    return {"schema_version": "1.0"}


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    application_number: Mapped[str] = mapped_column(Text)
    applicant_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    applicant_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    provider_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    purpose: Mapped[str] = mapped_column(Text)
    legal_or_ethics_basis: Mapped[str | None] = mapped_column(Text)
    algorithm_name: Mapped[str] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(Text)
    algorithm_digest: Mapped[str] = mapped_column(Text)
    requested_duration_seconds: Mapped[int] = mapped_column(Integer)
    requested_run_limit: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(24), default="draft", server_default="draft"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_summary: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    space: Mapped[Space] = relationship(foreign_keys=[space_id])
    applicant_organization: Mapped[Organization] = relationship(
        foreign_keys=[applicant_organization_id]
    )
    provider_organization: Mapped[Organization] = relationship(
        foreign_keys=[provider_organization_id]
    )
    applicant_user: Mapped[User] = relationship(foreign_keys=[applicant_user_id])
    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    items: Mapped[list[ApplicationItem]] = relationship(
        back_populates="application",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ApplicationItem.position_no",
        overlaps="product,version",
    )
    snapshot: Mapped[ApplicationSnapshot | None] = relationship(
        back_populates="application",
        uselist=False,
        passive_deletes=True,
    )
    requested_actions: Mapped[list[ApplicationRequestedAction]] = relationship(
        back_populates="application",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ApplicationRequestedAction.action_code",
    )
    requested_output_types: Mapped[list[ApplicationRequestedOutputType]] = relationship(
        back_populates="application",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ApplicationRequestedOutputType.output_type",
    )
    attachments: Mapped[list[ApplicationAttachment]] = relationship(
        back_populates="application",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by=(
            "ApplicationAttachment.attachment_type, "
            "ApplicationAttachment.content_digest"
        ),
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_values(APPLICATION_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "applicant_organization_id <> provider_organization_id",
            name="applicant_provider_distinct",
        ),
        CheckConstraint(
            "requested_duration_seconds > 0", name="duration_positive"
        ),
        CheckConstraint("requested_run_limit > 0", name="run_limit_positive"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
            "(status = 'draft' AND submitted_at IS NULL) OR "
            "(status <> 'draft' AND submitted_at IS NOT NULL)",
            name="submission_timeline",
        ),
        CheckConstraint(
            "(status IN ('approved', 'rejected') AND decided_at IS NOT NULL) OR "
            "(status NOT IN ('approved', 'rejected') AND decided_at IS NULL)",
            name="decision_timeline",
        ),
        CheckConstraint(
            "(status = 'withdrawn' AND withdrawn_at IS NOT NULL) OR "
            "(status <> 'withdrawn' AND withdrawn_at IS NULL)",
            name="withdrawal_timeline",
        ),
        UniqueConstraint(
            "space_id", "application_number", name="uq_applications_space_number"
        ),
        UniqueConstraint(
            "id",
            "space_id",
            "provider_organization_id",
            name="uq_applications_id_space_provider",
        ),
        UniqueConstraint(
            "id",
            "space_id",
            name="uq_applications_id_space",
        ),
        Index(
            "ix_applications_space_status_submitted",
            "space_id",
            "status",
            text("submitted_at DESC"),
        ),
        Index(
            "ix_applications_applicant_status_created",
            "applicant_organization_id",
            "status",
            text("created_at DESC"),
        ),
        Index(
            "ix_applications_provider_status_submitted",
            "provider_organization_id",
            "status",
            text("submitted_at DESC"),
        ),
        Index("ix_applications_applicant_user", "applicant_user_id"),
        Index("ix_applications_created_by", "created_by"),
    )


class ApplicationItem(Base):
    __tablename__ = "application_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column()
    space_id: Mapped[UUID] = mapped_column()
    provider_organization_id: Mapped[UUID] = mapped_column()
    data_product_id: Mapped[UUID] = mapped_column()
    data_product_version_id: Mapped[UUID] = mapped_column()
    position_no: Mapped[int] = mapped_column(Integer)
    requested_product_snapshot_digest: Mapped[str] = mapped_column(Text)
    requested_policy_digest: Mapped[str] = mapped_column(Text)
    requested_scope: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    application: Mapped[Application] = relationship(
        back_populates="items",
        foreign_keys=[application_id, space_id, provider_organization_id],
        overlaps="product,version",
    )
    product: Mapped[DataProduct] = relationship(
        foreign_keys=[space_id, provider_organization_id, data_product_id],
        overlaps="application,items,version",
    )
    version: Mapped[DataProductVersion] = relationship(
        foreign_keys=[data_product_id, data_product_version_id],
        overlaps="application,items,product",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "space_id", "provider_organization_id"],
            [
                f"{SCHEMA}.applications.id",
                f"{SCHEMA}.applications.space_id",
                f"{SCHEMA}.applications.provider_organization_id",
            ],
            name="fk_application_items_application_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["space_id", "provider_organization_id", "data_product_id"],
            [
                f"{SCHEMA}.data_products.space_id",
                f"{SCHEMA}.data_products.provider_organization_id",
                f"{SCHEMA}.data_products.id",
            ],
            name="fk_application_items_product_provider",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["data_product_id", "data_product_version_id"],
            [
                f"{SCHEMA}.data_product_versions.data_product_id",
                f"{SCHEMA}.data_product_versions.id",
            ],
            name="fk_application_items_product_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position_no > 0", name="position_no_positive"),
        UniqueConstraint(
            "application_id",
            "data_product_version_id",
            name="uq_application_items_application_version",
        ),
        UniqueConstraint(
            "application_id", "position_no", name="uq_application_items_position"
        ),
        UniqueConstraint(
            "application_id",
            "id",
            "data_product_version_id",
            name="uq_application_items_application_id_version",
        ),
        Index(
            "ix_application_items_version_application",
            "data_product_version_id",
            "application_id",
        ),
        Index("ix_application_items_product", "data_product_id"),
    )


class ApplicationRequestedAction(Base):
    __tablename__ = "application_requested_actions"

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.applications.id",
            name="fk_application_requested_actions_application",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    action_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=action_parameters_default,
        server_default=text("'{\"schema_version\":\"1.0\"}'"),
    )

    application: Mapped[Application] = relationship(
        back_populates="requested_actions"
    )

    __table_args__ = (
        CheckConstraint(
            f"action_code IN ({sql_values(APPLICATION_ACTION_CODES)})",
            name="action_code",
        ),
        Index(
            "ix_application_requested_actions_code_application",
            "action_code",
            "application_id",
        ),
    )


class ApplicationRequestedOutputType(Base):
    __tablename__ = "application_requested_output_types"

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.applications.id",
            name="fk_application_requested_outputs_application",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    output_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean)

    application: Mapped[Application] = relationship(
        back_populates="requested_output_types"
    )

    __table_args__ = (
        CheckConstraint(
            f"output_type IN ({sql_values(APPLICATION_OUTPUT_TYPES)})",
            name="output_type",
        ),
        Index(
            "ix_application_requested_outputs_type_application",
            "output_type",
            "application_id",
        ),
    )


class ApplicationAttachment(Base):
    __tablename__ = "application_attachments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.applications.id",
            name="fk_application_attachments_application",
            ondelete="CASCADE",
        )
    )
    attachment_type: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(Text)
    storage_ref: Mapped[str] = mapped_column(Text)
    content_digest: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    scan_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.users.id",
            name="fk_application_attachments_created_by",
            ondelete="RESTRICT",
        )
    )

    application: Mapped[Application] = relationship(back_populates="attachments")
    creator: Mapped[User] = relationship(foreign_keys=[created_by])

    __table_args__ = (
        CheckConstraint(
            f"attachment_type IN ({sql_values(APPLICATION_ATTACHMENT_TYPES)})",
            name="attachment_type",
        ),
        CheckConstraint(
            f"scan_status IN ({sql_values(APPLICATION_ATTACHMENT_SCAN_STATUSES)})",
            name="scan_status",
        ),
        CheckConstraint("length(trim(display_name)) > 0", name="display_name_nonempty"),
        CheckConstraint("length(trim(storage_ref)) > 0", name="storage_ref_nonempty"),
        CheckConstraint(
            "content_digest LIKE 'sha256:%' AND length(content_digest) = 71",
            name="content_digest_shape",
        ),
        CheckConstraint("size_bytes >= 0", name="size_bytes_nonnegative"),
        UniqueConstraint(
            "application_id",
            "content_digest",
            name="uq_application_attachments_application_digest",
        ),
        Index(
            "ix_application_attachments_application_type",
            "application_id",
            "attachment_type",
        ),
        Index(
            "ix_application_attachments_scan_application",
            "scan_status",
            "application_id",
        ),
        Index("ix_application_attachments_created_by", "created_by"),
    )


class ApplicationSnapshot(Base):
    __tablename__ = "application_snapshots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.applications.id", ondelete="RESTRICT")
    )
    schema_version: Mapped[str] = mapped_column(Text)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    snapshot_digest: Mapped[str] = mapped_column(Text)
    digest_algorithm: Mapped[str] = mapped_column(
        String(16), default="sha256", server_default="sha256"
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    captured_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    application: Mapped[Application] = relationship(back_populates="snapshot")
    captured_by_user: Mapped[User] = relationship(foreign_keys=[captured_by])

    __table_args__ = (
        CheckConstraint("schema_version <> ''", name="schema_version_nonempty"),
        CheckConstraint(
            f"digest_algorithm IN ({sql_values(DIGEST_ALGORITHMS)})",
            name="digest_algorithm",
        ),
        UniqueConstraint("application_id", name="uq_application_snapshots_application"),
        UniqueConstraint(
            "application_id",
            "id",
            "snapshot_digest",
            name="uq_application_snapshots_application_id_digest",
        ),
        UniqueConstraint(
            "application_id",
            "snapshot_digest",
            name="uq_application_snapshots_application_digest",
        ),
        Index("ix_application_snapshots_digest", "snapshot_digest"),
        Index("ix_application_snapshots_captured_by", "captured_by"),
    )
