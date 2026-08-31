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
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.identity.models import utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class AssetMaterializationPlan(Base):
    __tablename__ = "asset_materialization_plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    relation_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.dataset_model_relations.id", ondelete="RESTRICT")
    )
    data_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.data_product_versions.id", ondelete="RESTRICT")
    )
    model_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT")
    )
    relation_evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.dataset_model_evidence.id", ondelete="RESTRICT")
    )
    plan_status: Mapped[str] = mapped_column(String(16))
    data_plan: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    model_plan: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    transformation_plan: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    execution_goal: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    data_estimated_bytes: Mapped[int] = mapped_column(BigInteger)
    model_estimated_bytes: Mapped[int] = mapped_column(BigInteger)
    derived_estimated_bytes: Mapped[int] = mapped_column(BigInteger)
    total_estimated_bytes: Mapped[int] = mapped_column(BigInteger)
    hardware_requirements: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    network_allowlist: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    asset_file_allowlist: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT)
    license_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    access_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    security_preflight: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    blocking_reasons: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    data_version_digest: Mapped[str] = mapped_column(String(71))
    model_version_digest: Mapped[str] = mapped_column(String(71))
    data_source_digest: Mapped[str] = mapped_column(String(64))
    model_source_digest: Mapped[str] = mapped_column(String(64))
    data_governance_digest: Mapped[str] = mapped_column(String(71))
    model_governance_digest: Mapped[str] = mapped_column(String(71))
    relation_evidence_digest: Mapped[str] = mapped_column(String(71))
    plan_digest: Mapped[str] = mapped_column(String(71), unique=True)
    create_idempotency_digest: Mapped[str] = mapped_column(String(71), unique=True)
    submit_idempotency_digest: Mapped[str | None] = mapped_column(String(71), unique=True)
    decision_idempotency_digest: Mapped[str | None] = mapped_column(String(71), unique=True)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    creator_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    submitted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    approver_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    rejection_reasons: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    supersedes_plan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.asset_materialization_plans.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "plan_status IN ('draft','submitted','approved','rejected','expired',"
            "'superseded','cancelled')",
            name="status",
        ),
        CheckConstraint(
            "data_estimated_bytes>=0 AND model_estimated_bytes>=0 AND "
            "derived_estimated_bytes>=0 AND total_estimated_bytes="
            "data_estimated_bytes+model_estimated_bytes+derived_estimated_bytes",
            name="byte_budget",
        ),
        Index("ix_asset_materialization_relation", "relation_id", "created_at"),
        Index("ix_asset_materialization_status", "space_id", "plan_status", "created_at"),
    )
