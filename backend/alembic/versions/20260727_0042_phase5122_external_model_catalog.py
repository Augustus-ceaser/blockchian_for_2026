"""phase 5.12.2 external model catalog

Revision ID: 20260727_0042
Revises: 20260727_0041
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_0042"
down_revision = "20260727_0041"
branch_labels = None
depends_on = None
SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("external_catalog_sources", sa.Column("resource_kind", sa.String(16), nullable=False, server_default="dataset"), schema=SCHEMA)
    op.create_check_constraint("ck_external_catalog_sources_resource_kind", "external_catalog_sources", "resource_kind IN ('dataset','model')", schema=SCHEMA)
    op.add_column("external_catalog_sync_runs", sa.Column("resource_kind", sa.String(16), nullable=False, server_default="dataset"), schema=SCHEMA)
    op.add_column("external_catalog_sync_runs", sa.Column("models_digest", sa.String(64)), schema=SCHEMA)
    op.create_check_constraint("ck_external_catalog_sync_runs_resource_kind", "external_catalog_sync_runs", "resource_kind IN ('dataset','model')", schema=SCHEMA)

    op.create_table(
        "external_model_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.external_catalog_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_model_id", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("display_name_cn", sa.Text()),
        sa.Column("display_name_en", sa.Text()),
        sa.Column("source_catalog", sa.Text(), nullable=False),
        sa.Column("model_categories", JSONB, nullable=False, server_default="[]"),
        sa.Column("modalities", JSONB, nullable=False, server_default="[]"),
        sa.Column("task_types", JSONB, nullable=False, server_default="[]"),
        sa.Column("disease_areas", JSONB, nullable=False, server_default="[]"),
        sa.Column("organs", JSONB, nullable=False, server_default="[]"),
        sa.Column("species", JSONB, nullable=False, server_default="[]"),
        sa.Column("paper_title", sa.Text()),
        sa.Column("paper_doi", sa.Text()),
        sa.Column("paper_url", sa.Text()),
        sa.Column("code_repository_url", sa.Text()),
        sa.Column("model_card_url", sa.Text()),
        sa.Column("upstream_provider", sa.Text()),
        sa.Column("framework", sa.Text()),
        sa.Column("library_name", sa.Text()),
        sa.Column("architecture", sa.Text()),
        sa.Column("pipeline_tag", sa.Text()),
        sa.Column("input_schema", sa.Text()),
        sa.Column("output_schema", sa.Text()),
        sa.Column("preprocessing_summary", sa.Text()),
        sa.Column("training_dataset_references", JSONB, nullable=False, server_default="[]"),
        sa.Column("evaluation_dataset_references", JSONB, nullable=False, server_default="[]"),
        sa.Column("metrics_summary", JSONB, nullable=False, server_default="[]"),
        sa.Column("license_name", sa.Text()),
        sa.Column("license_url", sa.Text()),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("access_status", sa.String(32), nullable=False),
        sa.Column("weights_status", sa.String(32), nullable=False),
        sa.Column("weights_files", JSONB, nullable=False, server_default="[]"),
        sa.Column("estimated_weights_size_bytes", sa.BigInteger()),
        sa.Column("revision", sa.Text()),
        sa.Column("commit_sha", sa.Text()),
        sa.Column("release_tag", sa.Text()),
        sa.Column("gated", sa.Boolean()),
        sa.Column("clinical_use_status", sa.String(40), nullable=False),
        sa.Column("intended_use_summary", sa.Text()),
        sa.Column("limitations_summary", sa.Text()),
        sa.Column("execution_status", sa.String(32), nullable=False, server_default="not_materialized"),
        sa.Column("quality_flags", JSONB, nullable=False, server_default="[]"),
        sa.Column("raw_record_digest", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint("source_id", "external_model_id", name="uq_external_model_source_external"),
        sa.CheckConstraint("status IN ('active','stale')", name="ck_external_model_records_status"),
        sa.CheckConstraint("execution_status = 'not_materialized'", name="ck_external_model_records_execution_status"),
        schema=SCHEMA,
    )
    op.create_index("ix_external_model_source_status", "external_model_records", ["source_id", "status"], schema=SCHEMA)
    for name, column in (
        ("framework", "framework"), ("license", "license_status"),
        ("access", "access_status"), ("weights", "weights_status"),
        ("execution", "execution_status"),
    ):
        op.create_index(f"ix_external_model_{name}", "external_model_records", [column], schema=SCHEMA)
    for name, column in (("categories", "model_categories"), ("modalities", "modalities"), ("tasks", "task_types")):
        op.create_index(f"ix_external_model_{name}", "external_model_records", [column], schema=SCHEMA, postgresql_using="gin")

    op.create_table(
        "external_model_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("record_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
        sa.Column("normalized_payload", JSONB, nullable=False),
        sa.Column("source_evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("record_id", "record_digest", name="uq_external_model_version_digest"),
        schema=SCHEMA,
    )
    op.create_index("ix_external_model_versions_record_current", "external_model_versions", ["record_id", "is_current"], schema=SCHEMA)
    op.create_foreign_key("fk_external_model_records_current_version", "external_model_records", "external_model_versions", ["current_version_id"], ["id"], source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="RESTRICT", use_alter=True)


def downgrade() -> None:
    op.drop_constraint("fk_external_model_records_current_version", "external_model_records", schema=SCHEMA, type_="foreignkey")
    op.drop_table("external_model_versions", schema=SCHEMA)
    op.drop_table("external_model_records", schema=SCHEMA)
    op.drop_constraint("ck_external_catalog_sync_runs_resource_kind", "external_catalog_sync_runs", schema=SCHEMA, type_="check")
    op.drop_column("external_catalog_sync_runs", "models_digest", schema=SCHEMA)
    op.drop_column("external_catalog_sync_runs", "resource_kind", schema=SCHEMA)
    op.drop_constraint("ck_external_catalog_sources_resource_kind", "external_catalog_sources", schema=SCHEMA, type_="check")
    op.drop_column("external_catalog_sources", "resource_kind", schema=SCHEMA)
