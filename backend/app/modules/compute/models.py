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

COMPUTE_JOB_STATUSES = (
    "created",
    "validating",
    "ready",
    "running",
    "stopping",
    "succeeded",
    "denied",
    "failed",
    "interrupted",
    "cancelled",
)
COMPUTE_RUN_STATUSES = (
    "prepared",
    "reserved",
    "dispatched",
    "running",
    "succeeded",
    "failed",
    "interrupted",
    "cancelled",
    "timed_out",
)
COMPUTE_OUTPUT_TYPES = (
    "aggregate_statistics",
    "model_artifact",
    "feature_dataset",
    "risk_scoring_model",
)
ARTIFACT_RELEASE_STATUSES = (
    "quarantined",
    "released",
    "revoked",
    "destroyed",
)
ARTIFACT_REVIEW_STATUSES = ("pending", "claimed", "decided", "cancelled")
ARTIFACT_REVIEW_DECISIONS = ("approved", "rejected")


class ExecutionEligibilitySnapshot(Base):
    __tablename__ = "execution_eligibility_snapshots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    contract_id: Mapped[UUID] = mapped_column()
    contract_revision_id: Mapped[UUID] = mapped_column()
    revision_content_digest: Mapped[str] = mapped_column(Text)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.applications.id", ondelete="RESTRICT")
    )
    data_product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.data_product_versions.id", ondelete="RESTRICT")
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="RESTRICT")
    )
    data_readiness_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.contract_readiness_confirmations.id", ondelete="RESTRICT"
        )
    )
    model_readiness_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.contract_readiness_confirmations.id", ondelete="RESTRICT"
        )
    )
    platform_readiness_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.contract_readiness_confirmations.id", ondelete="RESTRICT"
        )
    )
    check_matrix: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT)
    check_matrix_digest: Mapped[str] = mapped_column(Text)
    eligibility_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    eligibility_snapshot_digest: Mapped[str] = mapped_column(Text)
    execution_environment_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT
    )
    execution_environment_digest: Mapped[str] = mapped_column(Text)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_id", "space_id"],
            [f"{SCHEMA}.contracts.id", f"{SCHEMA}.contracts.space_id"],
            name="fk_execution_eligibility_contract_space",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "contract_id"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.contract_id",
            ],
            name="fk_execution_eligibility_revision_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "revision_content_digest"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.content_digest",
            ],
            name="fk_execution_eligibility_revision_digest",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(check_matrix_digest) = 71 AND substr(check_matrix_digest, 1, 7) = 'sha256:' AND "
            "length(eligibility_snapshot_digest) = 71 AND substr(eligibility_snapshot_digest, 1, 7) = 'sha256:' AND "
            "length(execution_environment_digest) = 71 AND substr(execution_environment_digest, 1, 7) = 'sha256:'",
            name="digest_formats",
        ),
        UniqueConstraint("id", "space_id", name="uq_execution_eligibility_id_space"),
        UniqueConstraint(
            "eligibility_snapshot_digest",
            name="uq_execution_eligibility_snapshot_digest",
        ),
        Index(
            "ix_execution_eligibility_revision_created",
            "contract_revision_id",
            text("created_at DESC"),
        ),
    )


class ExecutionEligibilityInvalidation(Base):
    __tablename__ = "execution_eligibility_invalidations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    execution_eligibility_snapshot_id: Mapped[UUID] = mapped_column()
    reason_code: Mapped[str] = mapped_column(String(64))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    evidence_digest: Mapped[str] = mapped_column(Text)
    invalidated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    invalidated_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["execution_eligibility_snapshot_id", "space_id"],
            [
                f"{SCHEMA}.execution_eligibility_snapshots.id",
                f"{SCHEMA}.execution_eligibility_snapshots.space_id",
            ],
            name="fk_execution_eligibility_invalidation_snapshot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(evidence_digest) = 71 AND substr(evidence_digest, 1, 7) = 'sha256:'",
            name="digest_format",
        ),
        UniqueConstraint(
            "execution_eligibility_snapshot_id",
            name="uq_execution_eligibility_invalidation_snapshot",
        ),
        Index(
            "ix_execution_eligibility_invalidation_space_time",
            "space_id",
            text("invalidated_at DESC"),
        ),
    )


class ComputeJob(Base):
    __tablename__ = "compute_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    contract_id: Mapped[UUID] = mapped_column()
    contract_revision_id: Mapped[UUID] = mapped_column()
    revision_content_digest: Mapped[str] = mapped_column(Text)
    requester_contract_party_id: Mapped[UUID] = mapped_column()
    requester_organization_id: Mapped[UUID] = mapped_column()
    requester_user_id: Mapped[UUID] = mapped_column()
    contract_object_id: Mapped[UUID] = mapped_column()
    purpose_code: Mapped[str] = mapped_column(String(40))
    requested_output_types: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    algorithm_spec_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    algorithm_spec_digest: Mapped[str] = mapped_column(Text)
    compute_input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    compute_input_digest: Mapped[str] = mapped_column(Text)
    creation_authorization_evaluation: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT
    )
    creation_authorization_evaluation_digest: Mapped[str] = mapped_column(Text)
    creation_request_digest: Mapped[str] = mapped_column(Text)
    execution_eligibility_snapshot_id: Mapped[UUID | None] = mapped_column()
    eligibility_snapshot_digest: Mapped[str | None] = mapped_column(Text)
    quota_policy_id: Mapped[UUID | None] = mapped_column()
    run_count_constraint_id: Mapped[UUID | None] = mapped_column()
    run_limit_snapshot: Mapped[int | None] = mapped_column(Integer)
    pre_dispatch_slot_ordinal: Mapped[int | None] = mapped_column(Integer)
    pre_dispatch_slot_digest: Mapped[str | None] = mapped_column(Text)
    pre_dispatch_reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(
        String(16), default="created", server_default="created"
    )
    denial_code: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    interruption_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    runs: Mapped[list[ComputeRun]] = relationship(
        back_populates="job",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ComputeRun.attempt_no",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_id", "space_id"],
            [f"{SCHEMA}.contracts.id", f"{SCHEMA}.contracts.space_id"],
            name="fk_compute_jobs_contract_space",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "contract_id"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.contract_id",
            ],
            name="fk_compute_jobs_revision_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "revision_content_digest"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.content_digest",
            ],
            name="fk_compute_jobs_revision_digest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "contract_revision_id",
                "requester_contract_party_id",
                "requester_organization_id",
            ],
            [
                f"{SCHEMA}.contract_parties.contract_revision_id",
                f"{SCHEMA}.contract_parties.id",
                f"{SCHEMA}.contract_parties.organization_id",
            ],
            name="fk_compute_jobs_requester_party_org",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "contract_object_id"],
            [
                f"{SCHEMA}.contract_objects.contract_revision_id",
                f"{SCHEMA}.contract_objects.id",
            ],
            name="fk_compute_jobs_revision_object",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requester_organization_id", "requester_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_compute_jobs_requester_member",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["execution_eligibility_snapshot_id", "space_id"],
            [
                f"{SCHEMA}.execution_eligibility_snapshots.id",
                f"{SCHEMA}.execution_eligibility_snapshots.space_id",
            ],
            name="fk_compute_jobs_eligibility_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "quota_policy_id"],
            [f"{SCHEMA}.policies.contract_revision_id", f"{SCHEMA}.policies.id"],
            name="fk_compute_jobs_quota_policy_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_count_constraint_id"],
            [f"{SCHEMA}.policy_constraints.id"],
            name="fk_compute_jobs_run_count_constraint",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(COMPUTE_JOB_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "run_limit_snapshot IS NULL OR run_limit_snapshot > 0",
            name="run_limit_positive",
        ),
        CheckConstraint(
            "pre_dispatch_slot_ordinal IS NULL OR pre_dispatch_slot_ordinal > 0",
            name="pre_dispatch_slot_ordinal_positive",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "id",
            "space_id",
            "contract_id",
            "contract_revision_id",
            "requester_contract_party_id",
            "contract_object_id",
            name="uq_compute_jobs_run_scope",
        ),
        UniqueConstraint(
            "creation_request_digest", name="uq_compute_jobs_creation_request_digest"
        ),
        Index("ix_compute_jobs_space_status_created", "space_id", "status", text("created_at DESC")),
        Index("ix_compute_jobs_revision_status", "contract_revision_id", "status"),
        Index("ix_compute_jobs_requester_created", "requester_organization_id", text("created_at DESC")),
        Index("ix_compute_jobs_object_created", "contract_object_id", text("created_at DESC")),
        Index(
            "uq_compute_jobs_pre_dispatch_slot",
            "contract_revision_id",
            "quota_policy_id",
            "requester_contract_party_id",
            "contract_object_id",
            "pre_dispatch_slot_ordinal",
            unique=True,
            postgresql_where=text("pre_dispatch_slot_ordinal IS NOT NULL"),
            sqlite_where=text("pre_dispatch_slot_ordinal IS NOT NULL"),
        ),
    )


class ComputeRun(Base):
    __tablename__ = "compute_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    compute_job_id: Mapped[UUID] = mapped_column()
    contract_id: Mapped[UUID] = mapped_column()
    contract_revision_id: Mapped[UUID] = mapped_column()
    requester_contract_party_id: Mapped[UUID] = mapped_column()
    contract_object_id: Mapped[UUID] = mapped_column()
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), default="prepared", server_default="prepared"
    )
    quota_policy_id: Mapped[UUID | None] = mapped_column()
    run_count_constraint_id: Mapped[UUID | None] = mapped_column()
    run_limit_snapshot: Mapped[int | None] = mapped_column(Integer)
    reservation_ordinal: Mapped[int | None] = mapped_column(Integer)
    quota_scope_digest: Mapped[str | None] = mapped_column(Text)
    quota_reservation_digest: Mapped[str | None] = mapped_column(Text)
    quota_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_authorization_evaluation: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT
    )
    start_authorization_evaluation_digest: Mapped[str | None] = mapped_column(Text)
    compute_binding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.policy_execution_bindings.id",
            name="fk_compute_runs_compute_binding",
            ondelete="RESTRICT",
        )
    )
    egress_binding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.policy_execution_bindings.id",
            name="fk_compute_runs_egress_binding",
            ondelete="RESTRICT",
        )
    )
    audit_binding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.policy_execution_bindings.id",
            name="fk_compute_runs_audit_binding",
            ondelete="RESTRICT",
        )
    )
    execution_environment_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT
    )
    execution_environment_digest: Mapped[str | None] = mapped_column(Text)
    execution_reference: Mapped[str | None] = mapped_column(Text)
    dispatch_receipt_digest: Mapped[str | None] = mapped_column(Text)
    start_receipt_digest: Mapped[str | None] = mapped_column(Text)
    completion_receipt_digest: Mapped[str | None] = mapped_column(Text)
    audit_receipt_digest: Mapped[str | None] = mapped_column(Text)
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    interruption_code: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    job: Mapped[ComputeJob] = relationship(back_populates="runs")

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "compute_job_id",
                "space_id",
                "contract_id",
                "contract_revision_id",
                "requester_contract_party_id",
                "contract_object_id",
            ],
            [
                f"{SCHEMA}.compute_jobs.id",
                f"{SCHEMA}.compute_jobs.space_id",
                f"{SCHEMA}.compute_jobs.contract_id",
                f"{SCHEMA}.compute_jobs.contract_revision_id",
                f"{SCHEMA}.compute_jobs.requester_contract_party_id",
                f"{SCHEMA}.compute_jobs.contract_object_id",
            ],
            name="fk_compute_runs_job_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "quota_policy_id"],
            [f"{SCHEMA}.policies.contract_revision_id", f"{SCHEMA}.policies.id"],
            name="fk_compute_runs_quota_policy_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_count_constraint_id"],
            [f"{SCHEMA}.policy_constraints.id"],
            name="fk_compute_runs_run_count_constraint",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(COMPUTE_RUN_STATUSES)})", name="status"
        ),
        CheckConstraint("attempt_no > 0", name="attempt_no_positive"),
        CheckConstraint(
            "run_limit_snapshot IS NULL OR run_limit_snapshot > 0",
            name="run_limit_positive",
        ),
        CheckConstraint(
            "reservation_ordinal IS NULL OR reservation_ordinal > 0",
            name="reservation_ordinal_positive",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "compute_job_id", "attempt_no", name="uq_compute_runs_job_attempt"
        ),
        UniqueConstraint(
            "id", "compute_job_id", "space_id", name="uq_compute_runs_artifact_scope"
        ),
        Index(
            "uq_compute_runs_quota_ordinal",
            "contract_revision_id",
            "quota_policy_id",
            "requester_contract_party_id",
            "contract_object_id",
            "reservation_ordinal",
            unique=True,
            postgresql_where=text("reservation_ordinal IS NOT NULL"),
            sqlite_where=text("reservation_ordinal IS NOT NULL"),
        ),
        Index(
            "uq_compute_runs_job_nonterminal",
            "compute_job_id",
            unique=True,
            postgresql_where=text(
                "status IN ('prepared','reserved','dispatched','running')"
            ),
            sqlite_where=text(
                "status IN ('prepared','reserved','dispatched','running')"
            ),
        ),
        Index("ix_compute_runs_job_attempt_desc", "compute_job_id", text("attempt_no DESC")),
        Index("ix_compute_runs_space_status_prepared", "space_id", "status", text("prepared_at DESC")),
        Index("ix_compute_runs_revision_status", "contract_revision_id", "status"),
        Index("ix_compute_runs_quota_ordinal_desc", "quota_policy_id", text("reservation_ordinal DESC")),
        Index("ix_compute_runs_compute_binding", "compute_binding_id"),
        Index("ix_compute_runs_egress_binding", "egress_binding_id"),
        Index("ix_compute_runs_audit_binding", "audit_binding_id"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    compute_job_id: Mapped[UUID] = mapped_column()
    compute_run_id: Mapped[UUID] = mapped_column()
    artifact_no: Mapped[int] = mapped_column(Integer)
    artifact_type: Mapped[str] = mapped_column(String(32))
    content_digest: Mapped[str] = mapped_column(Text)
    storage_reference: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    classification_level: Mapped[str] = mapped_column(String(32))
    output_policy_evaluation: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    output_policy_evaluation_digest: Mapped[str] = mapped_column(Text)
    release_status: Mapped[str] = mapped_column(
        String(16), default="quarantined", server_default="quarantined"
    )
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    release_evidence_digest: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        ForeignKeyConstraint(
            ["compute_run_id", "compute_job_id", "space_id"],
            [
                f"{SCHEMA}.compute_runs.id",
                f"{SCHEMA}.compute_runs.compute_job_id",
                f"{SCHEMA}.compute_runs.space_id",
            ],
            name="fk_artifacts_run_job_space",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"artifact_type IN ({sql_values(COMPUTE_OUTPUT_TYPES)})",
            name="artifact_type",
        ),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("classification_level <> ''", name="classification_nonempty"),
        CheckConstraint(
            f"release_status IN ({sql_values(ARTIFACT_RELEASE_STATUSES)})",
            name="release_status",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
            "(release_status = 'quarantined' AND release_evidence IS NULL "
            "AND release_evidence_digest IS NULL AND released_at IS NULL "
            "AND revoked_at IS NULL AND destroyed_at IS NULL) OR "
            "(release_status = 'released' AND release_evidence IS NOT NULL "
            "AND release_evidence_digest IS NOT NULL AND released_at IS NOT NULL "
            "AND revoked_at IS NULL AND destroyed_at IS NULL) OR "
            "(release_status = 'revoked' AND release_evidence IS NOT NULL "
            "AND release_evidence_digest IS NOT NULL AND released_at IS NOT NULL "
            "AND revoked_at IS NOT NULL AND destroyed_at IS NULL) OR "
            "(release_status = 'destroyed' AND destroyed_at IS NOT NULL)",
            name="release_lifecycle_shape",
        ),
        UniqueConstraint(
            "compute_run_id", "artifact_no", name="uq_artifacts_run_no"
        ),
        UniqueConstraint(
            "compute_run_id",
            "artifact_type",
            "content_digest",
            name="uq_artifacts_run_type_digest",
        ),
        UniqueConstraint(
            "id", "space_id", "content_digest", name="uq_artifacts_review_scope"
        ),
        Index(
            "ix_artifacts_space_status_created",
            "space_id",
            "release_status",
            text("created_at DESC"),
        ),
        Index("ix_artifacts_job_created", "compute_job_id", "created_at"),
        Index("ix_artifacts_run_no", "compute_run_id", "artifact_no"),
        Index("ix_artifacts_content_digest", "content_digest"),
        Index(
            "ix_artifacts_retention_open",
            "retention_until",
            postgresql_where=text(
                "release_status IN ('quarantined','released','revoked')"
            ),
            sqlite_where=text(
                "release_status IN ('quarantined','released','revoked')"
            ),
        ),
    )


class ArtifactReview(Base):
    __tablename__ = "artifact_reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column()
    artifact_id: Mapped[UUID] = mapped_column()
    target_content_digest: Mapped[str] = mapped_column(Text)
    responsible_organization_id: Mapped[UUID] = mapped_column()
    claimed_by_user_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    routing_rule_digest: Mapped[str] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    decision_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    decision_digest: Mapped[str | None] = mapped_column(Text)
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
            name="fk_artifact_reviews_artifact_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            [
                f"{SCHEMA}.space_participants.space_id",
                f"{SCHEMA}.space_participants.organization_id",
            ],
            name="fk_artifact_reviews_responsible_participant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["responsible_organization_id", "claimed_by_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_artifact_reviews_claimed_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(ARTIFACT_REVIEW_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            f"decision IS NULL OR decision IN ({sql_values(ARTIFACT_REVIEW_DECISIONS)})",
            name="decision",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
            "(status = 'pending' AND claimed_by_user_id IS NULL "
            "AND claimed_at IS NULL AND decision IS NULL AND reason_code IS NULL "
            "AND decision_evidence IS NULL AND decision_digest IS NULL "
            "AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'claimed' AND claimed_by_user_id IS NOT NULL "
            "AND claimed_at IS NOT NULL AND decision IS NULL AND reason_code IS NULL "
            "AND decision_evidence IS NULL AND decision_digest IS NULL "
            "AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'decided' AND claimed_by_user_id IS NOT NULL "
            "AND claimed_at IS NOT NULL AND decision IS NOT NULL "
            "AND reason_code IS NOT NULL AND decision_evidence IS NOT NULL "
            "AND decision_digest IS NOT NULL AND decided_at IS NOT NULL "
            "AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND decision IS NULL "
            "AND decision_evidence IS NULL AND decision_digest IS NULL "
            "AND decided_at IS NULL AND cancelled_at IS NOT NULL)",
            name="lifecycle_shape",
        ),
        UniqueConstraint("artifact_id", name="uq_artifact_reviews_artifact"),
        Index("ix_artifact_reviews_space_status", "space_id", "status"),
        Index(
            "ix_artifact_reviews_responsible_status",
            "responsible_organization_id",
            "status",
        ),
        Index("ix_artifact_reviews_claimed_by", "claimed_by_user_id"),
        Index("ix_artifact_reviews_decided_at", "decided_at"),
    )
