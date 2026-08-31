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
    String,
    Text,
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
SYNC_STATUSES = (
    "created",
    "fetching_manifest",
    "not_modified",
    "validating",
    "applying",
    "succeeded",
    "failed",
)
RECORD_STATUSES = ("active", "stale")
GOVERNANCE_PRIMARY_STATUSES = (
    "unreviewed", "needs_source_review", "needs_license_review",
    "needs_access_review", "metadata_incomplete", "duplicate_pending",
    "in_review", "eligible_for_draft", "blocked", "rejected", "archived",
)
SOURCE_REVIEW_STATUSES = (
    "unreviewed", "official_source_confirmed", "aggregator_only",
    "source_missing", "source_malformed", "source_disputed",
)
LICENSE_REVIEW_STATUSES = (
    "unknown", "permissive", "research_only", "noncommercial", "controlled",
    "custom_terms", "redistribution_prohibited", "unverified", "not_applicable",
)
ACCESS_REVIEW_STATUSES = (
    "unknown", "open_download", "registration_required", "application_required",
    "controlled_access", "request_author", "metadata_only", "unavailable",
)
DUPLICATE_REVIEW_STATUSES = (
    "not_duplicate", "duplicate_unresolved", "canonical_candidate",
    "alias_candidate", "separate_valid_record", "duplicate_resolved",
)
REVIEW_DIMENSIONS = (
    "source", "license", "access", "metadata", "link", "duplicate", "productization",
)
DUPLICATE_RESOLUTION_TYPES = (
    "same_dataset_aliases",
    "same_url_different_entry",
    "same_name_different_dataset",
    "version_variants",
    "false_positive",
    "unresolved",
)
MODEL_GOVERNANCE_PRIMARY_STATUSES = (
    "unreviewed", "needs_source_review", "needs_paper_review",
    "needs_repository_review", "needs_model_card_review",
    "needs_license_review", "needs_weight_review", "needs_revision_review",
    "technical_contract_incomplete", "clinical_boundary_unclear",
    "security_review_required", "family_resolution_pending", "in_review",
    "eligible_for_model_draft", "blocked", "rejected", "archived",
)
MODEL_SOURCE_REVIEW_STATUSES = (
    "unreviewed", "official_source_confirmed", "author_source_confirmed",
    "aggregator_only", "source_missing", "source_disputed",
)
MODEL_PAPER_REVIEW_STATUSES = (
    "unreviewed", "official_paper_confirmed", "preprint_only",
    "paper_missing", "paper_disputed", "not_applicable",
)
MODEL_REPOSITORY_REVIEW_STATUSES = (
    "unreviewed", "official_repository_confirmed", "repository_archived",
    "fork_only", "repository_missing", "repository_disputed", "not_applicable",
)
MODEL_CARD_REVIEW_STATUSES = (
    "unreviewed", "official_model_card_confirmed", "incomplete",
    "missing", "not_applicable",
)
MODEL_LICENSE_REVIEW_STATUSES = (
    "unknown", "permissive", "research_only", "noncommercial",
    "custom_terms", "restricted", "redistribution_prohibited",
    "unverified", "not_applicable",
)
MODEL_WEIGHT_REVIEW_STATUSES = (
    "unknown", "not_released", "metadata_only", "public_available", "gated",
    "registration_required", "request_required", "author_request", "unavailable",
)
MODEL_REVISION_REVIEW_STATUSES = (
    "unknown", "unpinned", "commit_pinned", "release_tag_pinned",
    "model_revision_pinned", "conflicting_versions",
)
MODEL_CLINICAL_BOUNDARY_STATUSES = (
    "not_assessed", "research_only", "non_clinical",
    "clinical_claimed_by_source", "regulatory_cleared", "prohibited", "unclear",
)
MODEL_SECURITY_REVIEW_STATUSES = ("unreviewed", "review_required", "cleared", "blocked")
MODEL_FAMILY_STATUSES = ("none", "potential", "pending", "resolved", "disputed")
MODEL_REVIEW_DIMENSIONS = (
    "source", "paper", "repository", "model_card", "license", "weights",
    "revision", "technical_contract", "clinical_boundary", "security",
    "model_family", "productization",
)
MODEL_FAMILY_RESOLUTION_TYPES = (
    "same_model_aliases", "model_variants", "backbone_and_task_model",
    "different_models_same_paper", "repository_fork", "false_positive",
    "unresolved",
)


class ExternalCatalogSource(Base):
    __tablename__ = "external_catalog_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    source_code: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(
        String(32), default="versioned_rest_catalog"
    )
    resource_kind: Mapped[str] = mapped_column(
        String(16), default="dataset", server_default="dataset"
    )
    auth_mode: Mapped[str] = mapped_column(String(16), default="none")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    expected_schema_version: Mapped[str] = mapped_column(String(32), default="1.0")
    last_successful_catalog_version: Mapped[str | None] = mapped_column(Text)
    last_successful_etag: Mapped[str | None] = mapped_column(Text)
    last_successful_digest: Mapped[str | None] = mapped_column(String(64))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="ready", server_default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    sync_runs: Mapped[list["ExternalCatalogSyncRun"]] = relationship(back_populates="source")

    __table_args__ = (
        UniqueConstraint("space_id", "source_code", name="uq_external_catalog_source_space_code"),
        CheckConstraint("auth_mode = 'none'", name="auth_mode_none"),
        CheckConstraint("resource_kind IN ('dataset', 'model')", name="resource_kind"),
        Index("ix_external_catalog_sources_space_status", "space_id", "status"),
    )


class ExternalCatalogSyncRun(Base):
    __tablename__ = "external_catalog_sync_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_catalog_sources.id", ondelete="RESTRICT")
    )
    resource_kind: Mapped[str] = mapped_column(
        String(16), default="dataset", server_default="dataset"
    )
    status: Mapped[str] = mapped_column(String(24), default="created")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_etag: Mapped[str | None] = mapped_column(Text)
    response_etag: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    schema_version: Mapped[str | None] = mapped_column(Text)
    catalog_version: Mapped[str | None] = mapped_column(Text)
    expected_record_count: Mapped[int | None] = mapped_column(Integer)
    received_record_count: Mapped[int | None] = mapped_column(Integer)
    manifest_digest: Mapped[str | None] = mapped_column(String(64))
    datasets_digest: Mapped[str | None] = mapped_column(String(64))
    models_digest: Mapped[str | None] = mapped_column(String(64))
    inserted_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stale_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(Text)

    source: Mapped[ExternalCatalogSource] = relationship(back_populates="sync_runs")

    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(SYNC_STATUSES)})", name="status"),
        CheckConstraint("resource_kind IN ('dataset', 'model')", name="resource_kind"),
        Index("ix_external_catalog_sync_runs_source_started", "source_id", "started_at"),
        Index(
            "uq_external_catalog_sync_runs_active",
            "source_id",
            unique=True,
            postgresql_where=text(
                "status IN ('created','fetching_manifest','validating','applying')"
            ),
            sqlite_where=text(
                "status IN ('created','fetching_manifest','validating','applying')"
            ),
        ),
    )


class ExternalModelRecord(Base):
    __tablename__ = "external_model_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_catalog_sources.id", ondelete="RESTRICT")
    )
    external_model_id: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.external_model_versions.id",
            name="fk_external_model_records_current_version",
            use_alter=True,
            ondelete="RESTRICT",
        )
    )
    canonical_name: Mapped[str] = mapped_column(Text)
    display_name_cn: Mapped[str | None] = mapped_column(Text)
    display_name_en: Mapped[str | None] = mapped_column(Text)
    source_catalog: Mapped[str] = mapped_column(Text)
    model_categories: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    modalities: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    task_types: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    disease_areas: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    organs: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    species: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    paper_title: Mapped[str | None] = mapped_column(Text)
    paper_doi: Mapped[str | None] = mapped_column(Text)
    paper_url: Mapped[str | None] = mapped_column(Text)
    code_repository_url: Mapped[str | None] = mapped_column(Text)
    model_card_url: Mapped[str | None] = mapped_column(Text)
    upstream_provider: Mapped[str | None] = mapped_column(Text)
    framework: Mapped[str | None] = mapped_column(Text)
    library_name: Mapped[str | None] = mapped_column(Text)
    architecture: Mapped[str | None] = mapped_column(Text)
    pipeline_tag: Mapped[str | None] = mapped_column(Text)
    input_schema: Mapped[str | None] = mapped_column(Text)
    output_schema: Mapped[str | None] = mapped_column(Text)
    preprocessing_summary: Mapped[str | None] = mapped_column(Text)
    training_dataset_references: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    evaluation_dataset_references: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    metrics_summary: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    license_name: Mapped[str | None] = mapped_column(Text)
    license_url: Mapped[str | None] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(32), default="unknown")
    access_status: Mapped[str] = mapped_column(String(32), default="unknown")
    weights_status: Mapped[str] = mapped_column(String(32), default="unknown")
    weights_files: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    estimated_weights_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    revision: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(Text)
    release_tag: Mapped[str | None] = mapped_column(Text)
    gated: Mapped[bool | None] = mapped_column(Boolean)
    clinical_use_status: Mapped[str] = mapped_column(String(40), default="not_assessed")
    intended_use_summary: Mapped[str | None] = mapped_column(Text)
    limitations_summary: Mapped[str | None] = mapped_column(Text)
    execution_status: Mapped[str] = mapped_column(
        String(32), default="not_materialized", server_default="not_materialized"
    )
    quality_flags: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    raw_record_digest: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")

    versions: Mapped[list["ExternalModelVersion"]] = relationship(
        back_populates="record",
        foreign_keys="ExternalModelVersion.record_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped["ExternalModelVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )

    __table_args__ = (
        UniqueConstraint("source_id", "external_model_id", name="uq_external_model_source_external"),
        CheckConstraint(f"status IN ({sql_values(RECORD_STATUSES)})", name="status"),
        CheckConstraint("execution_status = 'not_materialized'", name="execution_status"),
        Index("ix_external_model_source_status", "source_id", "status"),
        Index("ix_external_model_framework", "framework"),
        Index("ix_external_model_license", "license_status"),
        Index("ix_external_model_access", "access_status"),
        Index("ix_external_model_weights", "weights_status"),
        Index("ix_external_model_execution", "execution_status"),
        Index("ix_external_model_categories", "model_categories", postgresql_using="gin"),
        Index("ix_external_model_modalities", "modalities", postgresql_using="gin"),
        Index("ix_external_model_tasks", "task_types", postgresql_using="gin"),
    )


class ExternalModelVersion(Base):
    __tablename__ = "external_model_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="CASCADE")
    )
    catalog_version: Mapped[str] = mapped_column(Text)
    record_digest: Mapped[str] = mapped_column(String(64))
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    source_evidence: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    record: Mapped[ExternalModelRecord] = relationship(
        back_populates="versions", foreign_keys=[record_id]
    )

    __table_args__ = (
        UniqueConstraint("record_id", "record_digest", name="uq_external_model_version_digest"),
        Index("ix_external_model_versions_record_current", "record_id", "is_current"),
    )


class ExternalModelGovernanceProfile(Base):
    __tablename__ = "external_model_governance_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="RESTRICT"),
        unique=True,
    )
    primary_status: Mapped[str] = mapped_column(String(48))
    source_review_status: Mapped[str] = mapped_column(String(48))
    paper_review_status: Mapped[str] = mapped_column(String(48))
    repository_review_status: Mapped[str] = mapped_column(String(48))
    model_card_review_status: Mapped[str] = mapped_column(String(48))
    license_review_status: Mapped[str] = mapped_column(String(48))
    weight_review_status: Mapped[str] = mapped_column(String(48))
    revision_review_status: Mapped[str] = mapped_column(String(48))
    technical_contract_score: Mapped[int] = mapped_column(Integer)
    technical_missing_fields: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    clinical_boundary_status: Mapped[str] = mapped_column(String(48))
    security_review_status: Mapped[str] = mapped_column(String(32))
    security_risk_flags: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    model_family_status: Mapped[str] = mapped_column(String(24))
    potential_family_key: Mapped[str | None] = mapped_column(Text)
    productization_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    blocking_reasons: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    warning_reasons: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reviewed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"primary_status IN ({sql_values(MODEL_GOVERNANCE_PRIMARY_STATUSES)})", name="primary_status"),
        CheckConstraint(f"source_review_status IN ({sql_values(MODEL_SOURCE_REVIEW_STATUSES)})", name="source_status"),
        CheckConstraint(f"paper_review_status IN ({sql_values(MODEL_PAPER_REVIEW_STATUSES)})", name="paper_status"),
        CheckConstraint(f"repository_review_status IN ({sql_values(MODEL_REPOSITORY_REVIEW_STATUSES)})", name="repository_status"),
        CheckConstraint(f"model_card_review_status IN ({sql_values(MODEL_CARD_REVIEW_STATUSES)})", name="model_card_status"),
        CheckConstraint(f"license_review_status IN ({sql_values(MODEL_LICENSE_REVIEW_STATUSES)})", name="license_status"),
        CheckConstraint(f"weight_review_status IN ({sql_values(MODEL_WEIGHT_REVIEW_STATUSES)})", name="weight_status"),
        CheckConstraint(f"revision_review_status IN ({sql_values(MODEL_REVISION_REVIEW_STATUSES)})", name="revision_status"),
        CheckConstraint(f"clinical_boundary_status IN ({sql_values(MODEL_CLINICAL_BOUNDARY_STATUSES)})", name="clinical_status"),
        CheckConstraint(f"security_review_status IN ({sql_values(MODEL_SECURITY_REVIEW_STATUSES)})", name="security_status"),
        CheckConstraint(f"model_family_status IN ({sql_values(MODEL_FAMILY_STATUSES)})", name="family_status"),
        CheckConstraint("technical_contract_score BETWEEN 0 AND 100", name="technical_score"),
        Index("ix_external_model_governance_primary", "primary_status"),
        Index("ix_external_model_governance_dimensions", "license_review_status", "weight_review_status", "revision_review_status"),
        Index("ix_external_model_governance_family", "model_family_status", "potential_family_key"),
    )


class ExternalModelGovernanceReview(Base):
    __tablename__ = "external_model_governance_reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="RESTRICT")
    )
    review_dimension: Mapped[str] = mapped_column(String(32))
    previous_value: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(64))
    decision_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    evidence_type: Mapped[str] = mapped_column(String(40))
    evidence_reference: Mapped[str | None] = mapped_column(Text)
    evidence_note: Mapped[str] = mapped_column(Text)
    reviewer_user_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    reviewer_organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_record_digest: Mapped[str] = mapped_column(String(64))
    supersedes_review_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_governance_reviews.id", ondelete="RESTRICT")
    )
    idempotency_digest: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"review_dimension IN ({sql_values(MODEL_REVIEW_DIMENSIONS)})", name="dimension"),
        Index("ix_external_model_reviews_record_time", "record_id", "reviewed_at"),
        Index("ix_external_model_reviews_dimension", "review_dimension", "decision"),
    )


class ExternalModelFamilyResolution(Base):
    __tablename__ = "external_model_family_resolutions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    model_family_key: Mapped[str] = mapped_column(Text, unique=True)
    resolution_status: Mapped[str] = mapped_column(String(24))
    canonical_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="RESTRICT")
    )
    resolution_type: Mapped[str] = mapped_column(String(48))
    member_record_ids: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT)
    rationale: Mapped[str] = mapped_column(Text)
    resolved_by: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_digest: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    __table_args__ = (
        CheckConstraint("resolution_status IN ('resolved','unresolved','disputed')", name="status"),
        CheckConstraint(f"resolution_type IN ({sql_values(MODEL_FAMILY_RESOLUTION_TYPES)})", name="type"),
        Index("ix_external_model_family_status", "resolution_status", "model_family_key"),
    )


class ExternalDatasetRecord(Base):
    __tablename__ = "external_dataset_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_catalog_sources.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.external_dataset_versions.id",
            name="fk_external_dataset_records_current_version",
            use_alter=True,
            ondelete="RESTRICT",
        )
    )
    canonical_name: Mapped[str] = mapped_column(Text)
    display_name_cn: Mapped[str | None] = mapped_column(Text)
    display_name_en: Mapped[str | None] = mapped_column(Text)
    source_catalog: Mapped[str] = mapped_column(Text)
    official_source_name: Mapped[str | None] = mapped_column(Text)
    official_source_url: Mapped[str | None] = mapped_column(Text)
    catalog_source_url: Mapped[str | None] = mapped_column(Text)
    modalities: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    disease_areas: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    organs: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    task_types: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    species: Mapped[str | None] = mapped_column(Text)
    sample_count: Mapped[int | None] = mapped_column(Integer)
    patient_count: Mapped[int | None] = mapped_column(Integer)
    file_count: Mapped[int | None] = mapped_column(Integer)
    approximate_size_bytes: Mapped[int | None] = mapped_column(Integer)
    data_formats: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    license_name: Mapped[str | None] = mapped_column(Text)
    license_url: Mapped[str | None] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(32), default="unknown")
    access_level: Mapped[str] = mapped_column(String(32), default="unknown")
    registration_required: Mapped[bool | None] = mapped_column(Boolean)
    dataset_version: Mapped[str | None] = mapped_column(Text)
    upstream_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    link_status: Mapped[str] = mapped_column(String(48), default="unknown")
    quality_flags: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    duplicate_group_id: Mapped[str | None] = mapped_column(Text)
    raw_record_digest: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")

    versions: Mapped[list["ExternalDatasetVersion"]] = relationship(
        back_populates="record",
        foreign_keys="ExternalDatasetVersion.record_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped["ExternalDatasetVersion | None"] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
    )

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_external_dataset_source_external"),
        CheckConstraint(f"status IN ({sql_values(RECORD_STATUSES)})", name="status"),
        Index("ix_external_dataset_records_source_status", "source_id", "status"),
        Index("ix_external_dataset_records_license", "license_status"),
        Index("ix_external_dataset_records_link", "link_status"),
        Index(
            "ix_external_dataset_records_modalities",
            "modalities",
            postgresql_using="gin",
        ),
        Index(
            "ix_external_dataset_records_disease_areas",
            "disease_areas",
            postgresql_using="gin",
        ),
    )


class ExternalDatasetVersion(Base):
    __tablename__ = "external_dataset_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_records.id", ondelete="CASCADE")
    )
    catalog_version: Mapped[str] = mapped_column(Text)
    record_digest: Mapped[str] = mapped_column(String(64))
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    record: Mapped[ExternalDatasetRecord] = relationship(
        back_populates="versions", foreign_keys=[record_id]
    )

    __table_args__ = (
        UniqueConstraint("record_id", "record_digest", name="uq_external_dataset_version_digest"),
        Index("ix_external_dataset_versions_record_current", "record_id", "is_current"),
    )


class ExternalDatasetGovernanceProfile(Base):
    __tablename__ = "external_dataset_governance_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_records.id", ondelete="RESTRICT"),
        unique=True,
    )
    primary_status: Mapped[str] = mapped_column(String(40))
    source_review_status: Mapped[str] = mapped_column(String(40), default="unreviewed")
    license_review_status: Mapped[str] = mapped_column(String(40), default="unknown")
    access_review_status: Mapped[str] = mapped_column(String(40), default="unknown")
    metadata_completeness_score: Mapped[int] = mapped_column(Integer)
    metadata_missing_fields: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    link_review_status: Mapped[str] = mapped_column(String(48))
    duplicate_review_status: Mapped[str] = mapped_column(String(40))
    productization_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    blocking_reasons: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    warning_reasons: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reviewed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"primary_status IN ({sql_values(GOVERNANCE_PRIMARY_STATUSES)})", name="primary_status"),
        CheckConstraint(f"source_review_status IN ({sql_values(SOURCE_REVIEW_STATUSES)})", name="source_review_status"),
        CheckConstraint(f"license_review_status IN ({sql_values(LICENSE_REVIEW_STATUSES)})", name="license_review_status"),
        CheckConstraint(f"access_review_status IN ({sql_values(ACCESS_REVIEW_STATUSES)})", name="access_review_status"),
        CheckConstraint(f"duplicate_review_status IN ({sql_values(DUPLICATE_REVIEW_STATUSES)})", name="duplicate_review_status"),
        CheckConstraint("metadata_completeness_score BETWEEN 0 AND 100", name="metadata_score"),
        Index("ix_external_governance_primary", "primary_status"),
        Index("ix_external_governance_dimensions", "source_review_status", "license_review_status", "access_review_status"),
        Index("ix_external_governance_duplicate", "duplicate_review_status"),
    )


class ExternalDatasetGovernanceReview(Base):
    __tablename__ = "external_dataset_governance_reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_records.id", ondelete="RESTRICT")
    )
    review_dimension: Mapped[str] = mapped_column(String(32))
    previous_value: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(64))
    decision_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    evidence_type: Mapped[str] = mapped_column(String(40))
    evidence_reference: Mapped[str | None] = mapped_column(Text)
    evidence_note: Mapped[str] = mapped_column(Text)
    reviewer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    reviewer_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_record_digest: Mapped[str] = mapped_column(String(64))
    supersedes_review_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_governance_reviews.id", ondelete="RESTRICT")
    )
    idempotency_digest: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"review_dimension IN ({sql_values(REVIEW_DIMENSIONS)})", name="dimension"),
        Index("ix_external_governance_reviews_record_time", "record_id", "reviewed_at"),
        Index("ix_external_governance_reviews_dimension", "review_dimension", "decision"),
    )


class ExternalDatasetDuplicateResolution(Base):
    __tablename__ = "external_dataset_duplicate_resolutions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    duplicate_group_id: Mapped[str] = mapped_column(Text)
    resolution_status: Mapped[str] = mapped_column(String(24), default="resolved")
    canonical_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_records.id", ondelete="RESTRICT")
    )
    resolution_type: Mapped[str] = mapped_column(String(40))
    rationale: Mapped[str] = mapped_column(Text)
    resolved_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_digest: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("resolution_status IN ('resolved', 'unresolved')", name="status"),
        CheckConstraint(
            f"resolution_type IN ({sql_values(DUPLICATE_RESOLUTION_TYPES)})",
            name="type",
        ),
        Index("ix_external_duplicate_resolution_group", "duplicate_group_id", "resolved_at"),
    )


class DataProductExternalSourceLink(Base):
    """Immutable provenance for a metadata-only product draft.

    This link deliberately lives beside the external catalog, rather than in the
    mutable product form. It records exactly which imported version and review
    snapshot justified the draft without copying the upstream source payload.
    """

    __tablename__ = "data_product_external_source_links"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    data_product_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.data_products.id", ondelete="RESTRICT"), unique=True
    )
    data_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.data_product_versions.id", ondelete="RESTRICT"),
        unique=True,
    )
    external_dataset_record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_records.id", ondelete="RESTRICT"),
        unique=True,
    )
    external_dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_versions.id", ondelete="RESTRICT")
    )
    external_catalog_source_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_catalog_sources.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(Text)
    source_record_digest: Mapped[str] = mapped_column(String(64))
    governance_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_governance_profiles.id", ondelete="RESTRICT")
    )
    governance_snapshot_digest: Mapped[str] = mapped_column(String(71))
    source_review_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_governance_reviews.id", ondelete="RESTRICT")
    )
    license_review_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_governance_reviews.id", ondelete="RESTRICT")
    )
    access_review_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_governance_reviews.id", ondelete="RESTRICT")
    )
    productization_review_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_dataset_governance_reviews.id", ondelete="RESTRICT")
    )
    upstream_official_url: Mapped[str] = mapped_column(Text)
    upstream_rights_holder: Mapped[str | None] = mapped_column(Text)
    curator_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    materialization_status: Mapped[str] = mapped_column(String(32), default="metadata_only")
    data_holder_status: Mapped[str] = mapped_column(String(32), default="external_upstream")
    redistribution_status: Mapped[str] = mapped_column(String(24))
    execution_readiness: Mapped[str] = mapped_column(String(24), default="not_ready")
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("materialization_status = 'metadata_only'", name="materialization_status"),
        CheckConstraint("data_holder_status = 'external_upstream'", name="data_holder_status"),
        CheckConstraint(
            "redistribution_status IN ('allowed','restricted','prohibited','unknown')",
            name="redistribution_status",
        ),
        CheckConstraint("execution_readiness = 'not_ready'", name="execution_readiness"),
        Index("ix_external_source_links_record", "external_dataset_record_id"),
        Index("ix_external_source_links_governance", "governance_profile_id"),
    )


class ModelProductExternalSourceLink(Base):
    """Immutable provenance for a metadata-only external model draft."""

    __tablename__ = "model_product_external_source_links"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    model_product_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_products.id", ondelete="RESTRICT"), unique=True
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT"), unique=True
    )
    external_model_record_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="RESTRICT"), unique=True
    )
    external_model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_versions.id", ondelete="RESTRICT")
    )
    external_catalog_source_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_catalog_sources.id", ondelete="RESTRICT")
    )
    external_model_id: Mapped[str] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(Text)
    source_record_digest: Mapped[str] = mapped_column(String(64))
    governance_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_model_governance_profiles.id", ondelete="RESTRICT")
    )
    governance_snapshot_digest: Mapped[str] = mapped_column(String(71))
    review_ids: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    upstream_official_url: Mapped[str] = mapped_column(Text)
    upstream_provider: Mapped[str | None] = mapped_column(Text)
    curator_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    materialization_status: Mapped[str] = mapped_column(String(32), default="metadata_only")
    weight_holder_status: Mapped[str] = mapped_column(String(32), default="external_upstream")
    execution_readiness: Mapped[str] = mapped_column(String(24), default="not_ready")
    platform_validation: Mapped[str] = mapped_column(String(24), default="not_validated")
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("materialization_status = 'metadata_only'", name="materialization_status"),
        CheckConstraint("weight_holder_status = 'external_upstream'", name="weight_holder_status"),
        CheckConstraint("execution_readiness = 'not_ready'", name="execution_readiness"),
        CheckConstraint("platform_validation = 'not_validated'", name="platform_validation"),
        Index("ix_model_external_links_record", "external_model_record_id"),
        Index("ix_model_external_links_governance", "governance_profile_id"),
    )


class ModelMetadataPublicationReviewTask(Base):
    """Independent review evidence for external metadata model publication."""

    __tablename__ = "model_metadata_publication_review_tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    model_product_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_products.id", ondelete="RESTRICT")
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT")
    )
    external_source_link_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.model_product_external_source_links.id", ondelete="RESTRICT"
        )
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    task_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    decision: Mapped[str | None] = mapped_column(String(16))
    submission_digest: Mapped[str] = mapped_column(String(71))
    review_digest: Mapped[str | None] = mapped_column(String(71))
    submitter_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    submitter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    reviewer_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        CheckConstraint(
            "task_status IN ('pending','decided')", name="task_status"
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('approved','returned')",
            name="decision",
        ),
        CheckConstraint(
            "(task_status='pending' AND decision IS NULL AND review_digest IS NULL "
            "AND reviewer_organization_id IS NULL AND reviewer_user_id IS NULL "
            "AND decided_at IS NULL) OR "
            "(task_status='decided' AND decision IS NOT NULL AND review_digest IS NOT NULL "
            "AND reviewer_organization_id IS NOT NULL AND reviewer_user_id IS NOT NULL "
            "AND decided_at IS NOT NULL)",
            name="lifecycle_shape",
        ),
        UniqueConstraint(
            "model_version_id",
            "sequence_no",
            name="uq_model_metadata_review_version_sequence",
        ),
        Index(
            "ix_model_metadata_review_space_status",
            "space_id",
            "task_status",
            "submitted_at",
        ),
        Index(
            "ix_model_metadata_review_version",
            "model_version_id",
            "sequence_no",
        ),
    )
