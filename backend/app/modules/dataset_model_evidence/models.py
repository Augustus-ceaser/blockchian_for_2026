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
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.identity.models import utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class DatasetModelRelation(Base):
    __tablename__ = "dataset_model_relations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    data_product_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.data_products.id", ondelete="RESTRICT")
    )
    data_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.data_product_versions.id", ondelete="RESTRICT")
    )
    model_product_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_products.id", ondelete="RESTRICT")
    )
    model_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT")
    )
    current_status: Mapped[str] = mapped_column(String(48))
    strongest_evidence_level: Mapped[str] = mapped_column(String(32))
    current_evidence_id: Mapped[UUID | None] = mapped_column()
    data_source_link_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.data_product_external_source_links.id", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    model_source_link_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.model_product_external_source_links.id", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    data_version_digest: Mapped[str] = mapped_column(String(71))
    model_version_digest: Mapped[str] = mapped_column(String(71))
    data_source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_governance_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    model_governance_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    public_visible: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "current_status IN ('not_assessed','external_declaration_only',"
            "'static_schema_compatible','static_schema_compatible_with_transformation',"
            "'static_schema_incompatible','insufficient_metadata','executed',"
            "'execution_failed','verified','superseded','archived')",
            name="status",
        ),
        CheckConstraint(
            "strongest_evidence_level IN ('none','external_declaration',"
            "'platform_static_review','runtime_execution','platform_verification')",
            name="level",
        ),
        UniqueConstraint(
            "data_product_version_id",
            "model_product_version_id",
            name="uq_dataset_model_relation_version_pair",
        ),
        Index("ix_dataset_model_relation_data", "data_product_id", "public_visible"),
        Index("ix_dataset_model_relation_model", "model_product_id", "public_visible"),
        Index("ix_dataset_model_relation_status", "space_id", "current_status", "active"),
    )


class DatasetModelEvidence(Base):
    __tablename__ = "dataset_model_evidence"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    relation_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.dataset_model_relations.id", ondelete="RESTRICT")
    )
    evidence_level: Mapped[str] = mapped_column(String(32))
    evidence_type: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    evidence_scope: Mapped[str] = mapped_column(String(32))
    evidence_reference: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    evidence_note: Mapped[str] = mapped_column(String(2000))
    structured_assessment: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    transformation_requirements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, default=list, server_default=text("'[]'")
    )
    blocking_reasons: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, server_default=text("'[]'")
    )
    warning_reasons: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, server_default=text("'[]'")
    )
    data_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.data_product_versions.id", ondelete="RESTRICT")
    )
    model_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT")
    )
    data_version_digest: Mapped[str] = mapped_column(String(71))
    model_version_digest: Mapped[str] = mapped_column(String(71))
    data_source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_governance_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    model_governance_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    reviewer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    reviewer_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    source_record_digest: Mapped[str] = mapped_column(String(71))
    idempotency_digest: Mapped[str] = mapped_column(String(71), unique=True)
    supersedes_evidence_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.dataset_model_evidence.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "evidence_level IN ('external_declaration','platform_static_review',"
            "'runtime_execution','platform_verification')",
            name="level",
        ),
        CheckConstraint(
            "evidence_type IN ('author_declared_training','author_declared_evaluation',"
            "'author_declared_benchmark','external_related_reference',"
            "'static_schema_compatible','static_schema_compatible_with_transformation',"
            "'static_schema_incompatible','insufficient_metadata','executed',"
            "'execution_failed','verified')",
            name="type",
        ),
        CheckConstraint("outcome IN ('supports','contradicts','inconclusive')", name="outcome"),
        CheckConstraint(
            "evidence_scope IN ('training','evaluation','benchmark','input_schema',"
            "'preprocessing','task','modality','format','resolution','label_schema',"
            "'runtime','verification')",
            name="scope",
        ),
        Index("ix_dataset_model_evidence_relation", "relation_id", "created_at"),
        Index("ix_dataset_model_evidence_level", "evidence_level", "evidence_type"),
    )
