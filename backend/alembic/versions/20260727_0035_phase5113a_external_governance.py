"""Add the Phase 5.11.3A external dataset governance overlay.

Revision ID: 20260727_0035
Revises: 20260727_0034
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_0035"
down_revision = "20260727_0034"
branch_labels = None
depends_on = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "external_dataset_governance_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("primary_status", sa.String(40), nullable=False),
        sa.Column("source_review_status", sa.String(40), nullable=False),
        sa.Column("license_review_status", sa.String(40), nullable=False),
        sa.Column("access_review_status", sa.String(40), nullable=False),
        sa.Column("metadata_completeness_score", sa.Integer(), nullable=False),
        sa.Column("metadata_missing_fields", postgresql.JSONB(), nullable=False),
        sa.Column("link_review_status", sa.String(48), nullable=False),
        sa.Column("duplicate_review_status", sa.String(40), nullable=False),
        sa.Column("productization_eligible", sa.Boolean(), nullable=False),
        sa.Column("blocking_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("warning_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "primary_status IN ('unreviewed','needs_source_review','needs_license_review',"
            "'needs_access_review','metadata_incomplete','duplicate_pending','in_review',"
            "'eligible_for_draft','blocked','rejected','archived')",
            name="ck_external_dataset_governance_profiles_primary_status",
        ),
        sa.CheckConstraint(
            "source_review_status IN ('unreviewed','official_source_confirmed','aggregator_only',"
            "'source_missing','source_malformed','source_disputed')",
            name="ck_external_dataset_governance_profiles_source_review_status",
        ),
        sa.CheckConstraint(
            "license_review_status IN ('unknown','permissive','research_only','noncommercial',"
            "'controlled','custom_terms','redistribution_prohibited','unverified','not_applicable')",
            name="ck_external_dataset_governance_profiles_license_review_status",
        ),
        sa.CheckConstraint(
            "access_review_status IN ('unknown','open_download','registration_required',"
            "'application_required','controlled_access','request_author','metadata_only','unavailable')",
            name="ck_external_dataset_governance_profiles_access_review_status",
        ),
        sa.CheckConstraint(
            "duplicate_review_status IN ('not_duplicate','duplicate_unresolved','canonical_candidate',"
            "'alias_candidate','separate_valid_record','duplicate_resolved')",
            name="ck_external_dataset_governance_profiles_duplicate_review_status",
        ),
        sa.CheckConstraint(
            "metadata_completeness_score BETWEEN 0 AND 100",
            name="ck_external_dataset_governance_profiles_metadata_score",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"], [f"{SCHEMA}.external_dataset_records.id"],
            name="fk_external_governance_profile_record", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_reviewed_by"], [f"{SCHEMA}.users.id"],
            name="fk_external_governance_profile_reviewer", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", name="uq_external_dataset_governance_profiles_record_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_external_governance_primary",
        "external_dataset_governance_profiles",
        ["primary_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_external_governance_dimensions",
        "external_dataset_governance_profiles",
        ["source_review_status", "license_review_status", "access_review_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_external_governance_duplicate",
        "external_dataset_governance_profiles",
        ["duplicate_review_status"],
        schema=SCHEMA,
    )

    op.create_table(
        "external_dataset_governance_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("review_dimension", sa.String(32), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("decision_payload", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_type", sa.String(40), nullable=False),
        sa.Column("evidence_reference", sa.Text(), nullable=True),
        sa.Column("evidence_note", sa.Text(), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_organization_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_record_digest", sa.String(64), nullable=False),
        sa.Column("supersedes_review_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "review_dimension IN ('source','license','access','metadata','link','duplicate','productization')",
            name="ck_external_dataset_governance_reviews_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"], [f"{SCHEMA}.external_dataset_records.id"],
            name="fk_external_governance_review_record", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"], [f"{SCHEMA}.users.id"],
            name="fk_external_governance_review_user", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_organization_id"], [f"{SCHEMA}.organizations.id"],
            name="fk_external_governance_review_organization", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_review_id"], [f"{SCHEMA}.external_dataset_governance_reviews.id"],
            name="fk_external_governance_review_supersedes", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_digest", name="uq_external_governance_review_idempotency"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_external_governance_reviews_record_time",
        "external_dataset_governance_reviews",
        ["record_id", "reviewed_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_external_governance_reviews_dimension",
        "external_dataset_governance_reviews",
        ["review_dimension", "decision"],
        schema=SCHEMA,
    )

    op.create_table(
        "external_dataset_duplicate_resolutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("duplicate_group_id", sa.Text(), nullable=False),
        sa.Column("resolution_status", sa.String(24), nullable=False),
        sa.Column("canonical_record_id", sa.Uuid(), nullable=True),
        sa.Column("resolution_type", sa.String(40), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["canonical_record_id"], [f"{SCHEMA}.external_dataset_records.id"],
            name="fk_external_duplicate_resolution_canonical", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], [f"{SCHEMA}.users.id"],
            name="fk_external_duplicate_resolution_user", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_digest", name="uq_external_duplicate_resolution_idempotency"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_external_duplicate_resolution_group",
        "external_dataset_duplicate_resolutions",
        ["duplicate_group_id", "resolved_at"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.reject_external_governance_review_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'external dataset governance reviews are append-only'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_external_governance_reviews_append_only
        BEFORE UPDATE OR DELETE ON medtrust.external_dataset_governance_reviews
        FOR EACH ROW EXECUTE FUNCTION medtrust.reject_external_governance_review_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_external_governance_reviews_append_only "
        "ON medtrust.external_dataset_governance_reviews"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.reject_external_governance_review_mutation()")
    op.drop_index(
        "ix_external_duplicate_resolution_group",
        table_name="external_dataset_duplicate_resolutions",
        schema=SCHEMA,
    )
    op.drop_table("external_dataset_duplicate_resolutions", schema=SCHEMA)
    op.drop_index(
        "ix_external_governance_reviews_dimension",
        table_name="external_dataset_governance_reviews",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_external_governance_reviews_record_time",
        table_name="external_dataset_governance_reviews",
        schema=SCHEMA,
    )
    op.drop_table("external_dataset_governance_reviews", schema=SCHEMA)
    op.drop_index(
        "ix_external_governance_duplicate",
        table_name="external_dataset_governance_profiles",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_external_governance_dimensions",
        table_name="external_dataset_governance_profiles",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_external_governance_primary",
        table_name="external_dataset_governance_profiles",
        schema=SCHEMA,
    )
    op.drop_table("external_dataset_governance_profiles", schema=SCHEMA)
