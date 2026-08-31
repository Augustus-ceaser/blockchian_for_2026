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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from app.modules.identity.models import sql_values, utc_now
# Importing the Phase 4 extension in an isolated fast-model test must still
# register the Artifact target table in shared metadata.  This is a metadata
# dependency only; no execution service is invoked here.
from app.modules.compute import models as _compute_models  # noqa: F401

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

MODEL_PRODUCT_STATUSES = ("draft", "active", "suspended", "unpublished", "archived")
MODEL_VERSION_STATUSES = ("draft", "under_review", "approved", "retired")
MODEL_PUBLICATION_STATUSES = ("active", "withdrawn", "expired")
MODEL_PUBLICATION_VISIBILITIES = ("space", "restricted", "invitation_only")
READINESS_TYPES = ("data_ready", "model_ready", "platform_ready")
ARTIFACT_REVIEW_TYPES = (
    "data_provider_egress_review",
    "platform_compliance_review",
    "model_provider_quality_review",
)
ARTIFACT_REVIEW_TASK_STATUSES = ("pending", "claimed", "decided", "cancelled")
ARTIFACT_REVIEW_DECISIONS = ("approved", "rejected")
RESULT_PACKAGE_STATUSES = ("available", "revoked")
DOWNLOAD_GRANT_STATUSES = ("active", "exhausted", "expired", "revoked")
SAFE_RESULT_FILENAMES = (
    "aggregate_metrics.json",
    "confusion_matrix.csv",
    "execution_summary.json",
)


class MarketplaceInvariantError(ValueError):
    """Raised when a Phase 4 immutable fact or lifecycle is violated."""


def _changed_columns(target: object) -> set[str]:
    state = inspect(target)
    return {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }


def _old_value(target: object, attribute: str, current: str) -> str:
    history = inspect(target).attrs[attribute].history
    return history.deleted[0] if history.deleted else current


class ModelProduct(Base):
    __tablename__ = "model_products"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    provider_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    product_code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft"
    )
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
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    unpublished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            f"lifecycle_status IN ({sql_values(MODEL_PRODUCT_STATUSES)})",
            name="lifecycle_status",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint("space_id", "product_code", name="uq_model_products_space_code"),
        UniqueConstraint("space_id", "id", name="uq_model_products_space_id"),
        UniqueConstraint(
            "space_id",
            "provider_organization_id",
            "id",
            name="uq_model_products_space_provider_id",
        ),
        Index(
            "ix_model_products_space_status_domain",
            "space_id",
            "lifecycle_status",
            "domain",
        ),
        Index(
            "ix_model_products_provider_status",
            "provider_organization_id",
            "lifecycle_status",
        ),
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    model_product_id: Mapped[UUID] = mapped_column()
    version_no: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft"
    )
    entrypoint_id: Mapped[str] = mapped_column(String(96))
    model_digest: Mapped[str] = mapped_column(String(71))
    manifest_digest: Mapped[str] = mapped_column(String(71))
    registry_digest: Mapped[str] = mapped_column(String(71))
    runtime: Mapped[str] = mapped_column(Text)
    input_schema_version: Mapped[str] = mapped_column(Text)
    output_schema_version: Mapped[str] = mapped_column(Text)
    compatibility_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    license_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    default_policy_template: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    default_policy_digest: Mapped[str] = mapped_column(String(71))
    snapshot_digest: Mapped[str | None] = mapped_column(String(71))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "model_product_id"],
            [f"{SCHEMA}.model_products.space_id", f"{SCHEMA}.model_products.id"],
            name="fk_model_versions_space_product",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint(f"status IN ({sql_values(MODEL_VERSION_STATUSES)})", name="status"),
        CheckConstraint(
            "length(model_digest) = 71 AND substr(model_digest, 1, 7) = 'sha256:' AND "
            "length(manifest_digest) = 71 AND substr(manifest_digest, 1, 7) = 'sha256:' AND "
            "length(registry_digest) = 71 AND substr(registry_digest, 1, 7) = 'sha256:' AND "
            "length(default_policy_digest) = 71 AND substr(default_policy_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        CheckConstraint(
            "snapshot_digest IS NULL OR (length(snapshot_digest) = 71 AND substr(snapshot_digest, 1, 7) = 'sha256:')",
            name="snapshot_digest_format",
        ),
        CheckConstraint(
            "status = 'draft' OR snapshot_digest IS NOT NULL",
            name="snapshot_required_after_draft",
        ),
        CheckConstraint(
            "(approved_at IS NULL AND approved_by IS NULL) OR "
            "(approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="approval_pair",
        ),
        UniqueConstraint(
            "model_product_id", "version_no", name="uq_model_versions_product_no"
        ),
        UniqueConstraint(
            "model_product_id", "version_label", name="uq_model_versions_product_label"
        ),
        UniqueConstraint(
            "model_product_id", "id", name="uq_model_versions_product_id"
        ),
        UniqueConstraint("space_id", "id", name="uq_model_versions_space_id"),
        UniqueConstraint("id", "snapshot_digest", name="uq_model_versions_id_digest"),
        Index(
            "ix_model_versions_product_status_no",
            "model_product_id",
            "status",
            text("version_no DESC"),
        ),
        Index("ix_model_versions_model_digest", "model_digest"),
    )


class ModelPublication(Base):
    __tablename__ = "model_publications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    model_product_id: Mapped[UUID] = mapped_column()
    model_version_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    visibility: Mapped[str] = mapped_column(String(24))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    published_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "model_product_id"],
            [f"{SCHEMA}.model_products.space_id", f"{SCHEMA}.model_products.id"],
            name="fk_model_publications_space_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_product_id", "model_version_id"],
            [f"{SCHEMA}.model_versions.model_product_id", f"{SCHEMA}.model_versions.id"],
            name="fk_model_publications_product_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(MODEL_PUBLICATION_STATUSES)})", name="status"
        ),
        CheckConstraint(
            f"visibility IN ({sql_values(MODEL_PUBLICATION_VISIBILITIES)})",
            name="visibility",
        ),
        CheckConstraint(
            "(status = 'active' AND ended_at IS NULL) OR "
            "(status <> 'active' AND ended_at IS NOT NULL)",
            name="lifecycle_shape",
        ),
        UniqueConstraint(
            "model_product_id", "model_version_id", name="uq_model_publications_version"
        ),
        Index(
            "uq_model_publications_active_product",
            "model_product_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_model_publications_space_status", "space_id", "status"),
    )


class ApplicationModelSelection(Base):
    __tablename__ = "application_model_selections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column()
    space_id: Mapped[UUID] = mapped_column()
    model_provider_organization_id: Mapped[UUID] = mapped_column()
    model_product_id: Mapped[UUID] = mapped_column()
    model_version_id: Mapped[UUID] = mapped_column()
    model_snapshot_digest: Mapped[str] = mapped_column(String(71))
    requested_model_policy_digest: Mapped[str] = mapped_column(String(71))
    registry_digest: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "space_id"],
            [f"{SCHEMA}.applications.id", f"{SCHEMA}.applications.space_id"],
            name="fk_application_model_selection_application_space",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["space_id", "model_provider_organization_id", "model_product_id"],
            [
                f"{SCHEMA}.model_products.space_id",
                f"{SCHEMA}.model_products.provider_organization_id",
                f"{SCHEMA}.model_products.id",
            ],
            name="fk_application_model_selection_product_provider",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_product_id", "model_version_id"],
            [f"{SCHEMA}.model_versions.model_product_id", f"{SCHEMA}.model_versions.id"],
            name="fk_application_model_selection_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(model_snapshot_digest) = 71 AND substr(model_snapshot_digest, 1, 7) = 'sha256:' AND "
            "length(requested_model_policy_digest) = 71 AND substr(requested_model_policy_digest, 1, 7) = 'sha256:' AND "
            "length(registry_digest) = 71 AND substr(registry_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        UniqueConstraint("application_id", name="uq_application_model_selection_application"),
        UniqueConstraint(
            "application_id", "id", "model_version_id",
            name="uq_application_model_selection_scope",
        ),
        Index("ix_application_model_selection_version", "model_version_id"),
        Index(
            "ix_application_model_selection_provider",
            "model_provider_organization_id",
            "application_id",
        ),
    )


class ContractModelObject(Base):
    __tablename__ = "contract_model_objects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contract_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.contract_revisions.id", ondelete="RESTRICT")
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT")
    )
    model_snapshot_digest: Mapped[str] = mapped_column(String(71))
    model_name_snapshot: Mapped[str] = mapped_column(Text)
    authorized_scope: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    authorized_scope_digest: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint(
            "length(model_snapshot_digest) = 71 AND substr(model_snapshot_digest, 1, 7) = 'sha256:' AND "
            "length(authorized_scope_digest) = 71 AND substr(authorized_scope_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        UniqueConstraint(
            "contract_revision_id", name="uq_contract_model_objects_revision"
        ),
        UniqueConstraint(
            "contract_revision_id", "id", "model_version_id",
            name="uq_contract_model_objects_scope",
        ),
        Index("ix_contract_model_objects_model_version", "model_version_id"),
    )


class ContractReadinessConfirmation(Base):
    __tablename__ = "contract_readiness_confirmations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    contract_revision_id: Mapped[UUID] = mapped_column()
    readiness_type: Mapped[str] = mapped_column(String(24))
    responsible_organization_id: Mapped[UUID] = mapped_column()
    confirmed_by_user_id: Mapped[UUID] = mapped_column()
    target_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    target_digest: Mapped[str] = mapped_column(String(71))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    evidence_digest: Mapped[str] = mapped_column(String(71))
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_revision_id"],
            [f"{SCHEMA}.contract_revisions.id"],
            name="fk_contract_readiness_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            [
                f"{SCHEMA}.space_participants.space_id",
                f"{SCHEMA}.space_participants.organization_id",
            ],
            name="fk_contract_readiness_responsible_participant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["responsible_organization_id", "confirmed_by_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_contract_readiness_confirmer_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"readiness_type IN ({sql_values(READINESS_TYPES)})", name="readiness_type"
        ),
        CheckConstraint(
            "length(target_digest) = 71 AND substr(target_digest, 1, 7) = 'sha256:' AND "
            "length(evidence_digest) = 71 AND substr(evidence_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        UniqueConstraint(
            "contract_revision_id", "readiness_type", "target_digest",
            name="uq_contract_readiness_revision_type_target",
        ),
        Index(
            "ix_contract_readiness_revision_type_time",
            "contract_revision_id",
            "readiness_type",
            text("confirmed_at DESC"),
        ),
    )


class ContractReadinessRevocation(Base):
    __tablename__ = "contract_readiness_revocations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    readiness_confirmation_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.contract_readiness_confirmations.id", ondelete="RESTRICT"
        )
    )
    responsible_organization_id: Mapped[UUID] = mapped_column()
    revoked_by_user_id: Mapped[UUID] = mapped_column()
    reason_code: Mapped[str] = mapped_column(String(64))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    evidence_digest: Mapped[str] = mapped_column(String(71))
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            [
                f"{SCHEMA}.space_participants.space_id",
                f"{SCHEMA}.space_participants.organization_id",
            ],
            name="fk_contract_readiness_revocation_responsible_participant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["responsible_organization_id", "revoked_by_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_contract_readiness_revocation_actor_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(evidence_digest) = 71 AND substr(evidence_digest, 1, 7) = 'sha256:'",
            name="digest_format",
        ),
        UniqueConstraint(
            "readiness_confirmation_id",
            name="uq_contract_readiness_revocation_confirmation",
        ),
        Index(
            "ix_contract_readiness_revocation_space_time",
            "space_id",
            text("revoked_at DESC"),
        ),
    )


class ArtifactReviewTask(Base):
    __tablename__ = "artifact_review_tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    artifact_id: Mapped[UUID] = mapped_column()
    target_content_digest: Mapped[str] = mapped_column(String(71))
    review_type: Mapped[str] = mapped_column(String(40))
    responsible_organization_id: Mapped[UUID] = mapped_column()
    assigned_user_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    routing_rule_digest: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id", "space_id", "target_content_digest"],
            [
                f"{SCHEMA}.artifacts.id",
                f"{SCHEMA}.artifacts.space_id",
                f"{SCHEMA}.artifacts.content_digest",
            ],
            name="fk_artifact_review_tasks_artifact_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            [
                f"{SCHEMA}.space_participants.space_id",
                f"{SCHEMA}.space_participants.organization_id",
            ],
            name="fk_artifact_review_tasks_responsible_participant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["responsible_organization_id", "assigned_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_artifact_review_tasks_assigned_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"review_type IN ({sql_values(ARTIFACT_REVIEW_TYPES)})", name="review_type"
        ),
        CheckConstraint(
            f"status IN ({sql_values(ARTIFACT_REVIEW_TASK_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "length(routing_rule_digest) = 71 AND substr(routing_rule_digest, 1, 7) = 'sha256:'",
            name="routing_rule_digest_format",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
            "(status = 'pending' AND assigned_user_id IS NULL AND claimed_at IS NULL "
            "AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'claimed' AND assigned_user_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'decided' AND assigned_user_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND decided_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND decided_at IS NULL AND cancelled_at IS NOT NULL)",
            name="lifecycle_shape",
        ),
        UniqueConstraint(
            "artifact_id", "review_type", name="uq_artifact_review_tasks_artifact_type"
        ),
        UniqueConstraint("id", "target_content_digest", name="uq_artifact_review_tasks_id_digest"),
        UniqueConstraint("id", "responsible_organization_id", name="uq_artifact_review_tasks_id_org"),
        UniqueConstraint(
            "id",
            "target_content_digest",
            "responsible_organization_id",
            name="uq_artifact_review_tasks_decision_scope",
        ),
        Index("ix_artifact_review_tasks_org_status", "responsible_organization_id", "status"),
        Index("ix_artifact_review_tasks_artifact", "artifact_id", "review_type"),
    )


class ArtifactReviewDecision(Base):
    __tablename__ = "artifact_review_decisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    artifact_review_task_id: Mapped[UUID] = mapped_column()
    responsible_organization_id: Mapped[UUID] = mapped_column()
    decided_by_user_id: Mapped[UUID] = mapped_column()
    target_content_digest: Mapped[str] = mapped_column(String(71))
    decision: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    evidence_digest: Mapped[str] = mapped_column(String(71))
    decision_digest: Mapped[str] = mapped_column(String(71))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "artifact_review_task_id",
                "target_content_digest",
                "responsible_organization_id",
            ],
            [
                f"{SCHEMA}.artifact_review_tasks.id",
                f"{SCHEMA}.artifact_review_tasks.target_content_digest",
                f"{SCHEMA}.artifact_review_tasks.responsible_organization_id",
            ],
            name="fk_artifact_review_decisions_task_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["responsible_organization_id", "decided_by_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_artifact_review_decisions_decider_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"decision IN ({sql_values(ARTIFACT_REVIEW_DECISIONS)})", name="decision"
        ),
        CheckConstraint(
            "length(evidence_digest) = 71 AND substr(evidence_digest, 1, 7) = 'sha256:' AND "
            "length(decision_digest) = 71 AND substr(decision_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        UniqueConstraint(
            "artifact_review_task_id", name="uq_artifact_review_decisions_task"
        ),
        Index("ix_artifact_review_decisions_decided_at", text("decided_at DESC")),
    )


class ApprovedResultPackage(Base):
    __tablename__ = "approved_result_packages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    artifact_id: Mapped[UUID] = mapped_column()
    requester_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        String(16), default="available", server_default="available"
    )
    package_digest: Mapped[str] = mapped_column(String(71))
    manifest_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    review_evidence_digest: Mapped[str] = mapped_column(String(71))
    authority_evaluation_digest: Mapped[str] = mapped_column(String(71))
    bucket_name: Mapped[str] = mapped_column(String(63))
    object_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id"],
            [f"{SCHEMA}.artifacts.id"],
            name="fk_result_packages_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_result_packages_space",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(RESULT_PACKAGE_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "length(package_digest) = 71 AND substr(package_digest, 1, 7) = 'sha256:' AND "
            "length(review_evidence_digest) = 71 AND substr(review_evidence_digest, 1, 7) = 'sha256:' AND "
            "length(authority_evaluation_digest) = 71 AND substr(authority_evaluation_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(
            "(status = 'available' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="lifecycle_shape",
        ),
        UniqueConstraint("artifact_id", name="uq_result_packages_artifact"),
        UniqueConstraint("space_id", "id", name="uq_result_packages_space_id"),
        Index("ix_result_packages_requester_status", "requester_organization_id", "status"),
    )


class ResultDownloadGrant(Base):
    __tablename__ = "result_download_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    result_package_id: Mapped[UUID] = mapped_column()
    requester_organization_id: Mapped[UUID] = mapped_column()
    requester_user_id: Mapped[UUID] = mapped_column()
    token_digest: Mapped[str] = mapped_column(String(71))
    request_digest: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    max_downloads: Mapped[int] = mapped_column(Integer)
    download_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    last_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["result_package_id", "space_id"],
            [
                f"{SCHEMA}.approved_result_packages.id",
                f"{SCHEMA}.approved_result_packages.space_id",
            ],
            name="fk_result_download_grants_package_space",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requester_organization_id", "requester_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_result_download_grants_requester_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(DOWNLOAD_GRANT_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "length(token_digest) = 71 AND substr(token_digest, 1, 7) = 'sha256:' AND "
            "length(request_digest) = 71 AND substr(request_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        CheckConstraint("max_downloads > 0", name="max_downloads_positive"),
        CheckConstraint(
            "download_count >= 0 AND download_count <= max_downloads",
            name="download_count_range",
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        UniqueConstraint("token_digest", name="uq_result_download_grants_token"),
        Index("ix_result_download_grants_package_status", "result_package_id", "status"),
        Index("ix_result_download_grants_expiry", "status", "expires_at"),
    )


IMMUTABLE_TYPES = (
    ContractModelObject,
    ContractReadinessConfirmation,
    ContractReadinessRevocation,
    ArtifactReviewDecision,
)


def _application_status_for_model_selection(
    session: Session, selection: ApplicationModelSelection
) -> str | None:
    from app.modules.applications.models import Application

    application = session.get(Application, selection.application_id)
    return None if application is None else application.status


def _require_draft_model_selection(
    session: Session, selection: ApplicationModelSelection
) -> None:
    if _application_status_for_model_selection(session, selection) != "draft":
        raise MarketplaceInvariantError(
            "application model selection can only change while the application is draft"
        )


@event.listens_for(Session, "before_flush")
def guard_marketplace_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.deleted:
        if isinstance(
            target,
            (
                ModelVersion,
                ModelPublication,
                *IMMUTABLE_TYPES,
                ArtifactReviewTask,
                ApprovedResultPackage,
                ResultDownloadGrant,
            ),
        ):
            raise MarketplaceInvariantError("Phase 4 evidence rows cannot be deleted")
        if isinstance(target, ApplicationModelSelection):
            _require_draft_model_selection(session, target)

    for target in session.new:
        if isinstance(target, ModelVersion) and target.status not in (None, "draft"):
            raise MarketplaceInvariantError("new ModelVersion must start as draft")
        if isinstance(target, ApplicationModelSelection):
            _require_draft_model_selection(session, target)
        if isinstance(target, ArtifactReviewTask) and target.status not in (None, "pending"):
            raise MarketplaceInvariantError("new ArtifactReviewTask must start pending")
        if isinstance(target, ApprovedResultPackage) and target.status not in (None, "available"):
            raise MarketplaceInvariantError("new result package must start available")
        if isinstance(target, ResultDownloadGrant) and target.status not in (None, "active"):
            raise MarketplaceInvariantError("new download grant must start active")

    for target in session.dirty:
        changed = _changed_columns(target)
        if not changed:
            continue
        if isinstance(target, ApplicationModelSelection):
            _require_draft_model_selection(session, target)
            if changed & {"id", "application_id", "space_id", "created_at"}:
                raise MarketplaceInvariantError(
                    "application model selection identity is immutable"
                )
        elif isinstance(target, IMMUTABLE_TYPES):
            raise MarketplaceInvariantError("immutable Phase 4 evidence cannot be updated")
        elif isinstance(target, ModelVersion):
            old = _old_value(target, "status", target.status)
            legal = {
                "draft": {"draft", "under_review"},
                "under_review": {"approved", "draft"},
                "approved": {"retired"},
                "retired": set(),
            }
            if target.status not in legal.get(old, set()):
                raise MarketplaceInvariantError("illegal ModelVersion status transition")
            if old != "draft" and changed - {"status", "approved_at", "approved_by"}:
                raise MarketplaceInvariantError("submitted ModelVersion content is immutable")
            if target.status != old and not getattr(target, "_transition_validated", False):
                raise MarketplaceInvariantError("ModelVersion transition requires service")
        elif isinstance(target, ArtifactReviewTask):
            old = _old_value(target, "status", target.status)
            legal = {
                "pending": {"claimed", "cancelled"},
                "claimed": {"decided", "cancelled"},
                "decided": set(),
                "cancelled": set(),
            }
            if target.status not in legal.get(old, set()):
                raise MarketplaceInvariantError("illegal ArtifactReviewTask transition")
            mutable = {
                "status", "assigned_user_id", "claimed_at", "decided_at",
                "cancelled_at", "row_version",
            }
            if changed - mutable:
                raise MarketplaceInvariantError("ArtifactReviewTask target is immutable")
            if not getattr(target, "_transition_validated", False):
                raise MarketplaceInvariantError("ArtifactReviewTask transition requires service")
        elif isinstance(target, ApprovedResultPackage):
            if changed - {"status", "revoked_at"}:
                raise MarketplaceInvariantError("result package identity is immutable")
            if not getattr(target, "_transition_validated", False):
                raise MarketplaceInvariantError("result package transition requires service")
        elif isinstance(target, ResultDownloadGrant):
            mutable = {"status", "download_count", "last_downloaded_at", "revoked_at"}
            if changed - mutable:
                raise MarketplaceInvariantError("download grant identity is immutable")
            if not getattr(target, "_transition_validated", False):
                raise MarketplaceInvariantError("download grant transition requires service")
