"""Phase 5.12.3B2 metadata-only external model product links.

Revision ID: 20260727_0044
Revises: 20260727_0043
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260727_0044"
down_revision = "20260727_0043"
branch_labels = None
depends_on = None
SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "model_product_external_source_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("model_product_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("model_version_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("external_model_record_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("external_model_version_id", sa.Uuid(), nullable=False),
        sa.Column("external_catalog_source_id", sa.Uuid(), nullable=False),
        sa.Column("external_model_id", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("source_record_digest", sa.String(64), nullable=False),
        sa.Column("governance_profile_id", sa.Uuid(), nullable=False),
        sa.Column("governance_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("review_ids", postgresql.JSONB(), nullable=False),
        sa.Column("upstream_official_url", sa.Text(), nullable=False),
        sa.Column("upstream_provider", sa.Text()),
        sa.Column("curator_organization_id", sa.Uuid(), nullable=False),
        sa.Column("materialization_status", sa.String(32), nullable=False, server_default="metadata_only"),
        sa.Column("weight_holder_status", sa.String(32), nullable=False, server_default="external_upstream"),
        sa.Column("execution_readiness", sa.String(24), nullable=False, server_default="not_ready"),
        sa.Column("platform_validation", sa.String(24), nullable=False, server_default="not_validated"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["model_product_id"], [f"{SCHEMA}.model_products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_version_id"], [f"{SCHEMA}.model_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_model_record_id"], [f"{SCHEMA}.external_model_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_model_version_id"], [f"{SCHEMA}.external_model_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_catalog_source_id"], [f"{SCHEMA}.external_catalog_sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["governance_profile_id"], [f"{SCHEMA}.external_model_governance_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["curator_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("materialization_status = 'metadata_only'", name="ck_model_product_external_source_links_materialization_status"),
        sa.CheckConstraint("weight_holder_status = 'external_upstream'", name="ck_model_product_external_source_links_weight_holder_status"),
        sa.CheckConstraint("execution_readiness = 'not_ready'", name="ck_model_product_external_source_links_execution_readiness"),
        sa.CheckConstraint("platform_validation = 'not_validated'", name="ck_model_product_external_source_links_platform_validation"),
        schema=SCHEMA,
    )
    op.create_index("ix_model_external_links_record", "model_product_external_source_links", ["external_model_record_id"], schema=SCHEMA)
    op.create_index("ix_model_external_links_governance", "model_product_external_source_links", ["governance_profile_id"], schema=SCHEMA)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_model_external_link_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'external model source linkage is immutable' USING ERRCODE='55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_model_external_link_immutable
        BEFORE UPDATE OR DELETE ON medtrust.model_product_external_source_links
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_model_external_link_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_model_external_link_immutable ON medtrust.model_product_external_source_links")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_model_external_link_immutable()")
    op.drop_index("ix_model_external_links_governance", table_name="model_product_external_source_links", schema=SCHEMA)
    op.drop_index("ix_model_external_links_record", table_name="model_product_external_source_links", schema=SCHEMA)
    op.drop_table("model_product_external_source_links", schema=SCHEMA)
