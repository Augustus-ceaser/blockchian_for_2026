"""Add immutable external-source links for Phase 5.11.3B2 drafts.

Revision ID: 20260727_0039
Revises: 20260727_0038
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0039"
down_revision = "20260727_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_product_external_source_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_product_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("external_dataset_record_id", sa.Uuid(), nullable=False),
        sa.Column("external_dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("external_catalog_source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("source_record_digest", sa.String(length=64), nullable=False),
        sa.Column("governance_profile_id", sa.Uuid(), nullable=False),
        sa.Column("governance_snapshot_digest", sa.String(length=71), nullable=False),
        sa.Column("source_review_id", sa.Uuid(), nullable=False),
        sa.Column("license_review_id", sa.Uuid(), nullable=False),
        sa.Column("access_review_id", sa.Uuid(), nullable=False),
        sa.Column("productization_review_id", sa.Uuid(), nullable=False),
        sa.Column("upstream_official_url", sa.Text(), nullable=False),
        sa.Column("upstream_rights_holder", sa.Text(), nullable=True),
        sa.Column("curator_organization_id", sa.Uuid(), nullable=False),
        sa.Column("materialization_status", sa.String(length=32), nullable=False, server_default="metadata_only"),
        sa.Column("data_holder_status", sa.String(length=32), nullable=False, server_default="external_upstream"),
        sa.Column("redistribution_status", sa.String(length=24), nullable=False),
        sa.Column("execution_readiness", sa.String(length=24), nullable=False, server_default="not_ready"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["data_product_id"], ["medtrust.data_products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["data_product_version_id"], ["medtrust.data_product_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_dataset_record_id"], ["medtrust.external_dataset_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_dataset_version_id"], ["medtrust.external_dataset_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_catalog_source_id"], ["medtrust.external_catalog_sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["governance_profile_id"], ["medtrust.external_dataset_governance_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_review_id"], ["medtrust.external_dataset_governance_reviews.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["license_review_id"], ["medtrust.external_dataset_governance_reviews.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["access_review_id"], ["medtrust.external_dataset_governance_reviews.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["productization_review_id"], ["medtrust.external_dataset_governance_reviews.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["curator_organization_id"], ["medtrust.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["medtrust.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("data_product_id", name="uq_external_source_links_product"),
        sa.UniqueConstraint("data_product_version_id", name="uq_external_source_links_version"),
        sa.UniqueConstraint("external_dataset_record_id", name="uq_external_source_links_record"),
        sa.CheckConstraint("materialization_status = 'metadata_only'", name="materialization_status"),
        sa.CheckConstraint("data_holder_status = 'external_upstream'", name="data_holder_status"),
        sa.CheckConstraint("redistribution_status IN ('allowed','restricted','prohibited','unknown')", name="redistribution_status"),
        sa.CheckConstraint("execution_readiness = 'not_ready'", name="execution_readiness"),
        schema="medtrust",
    )
    op.create_index(
        "ix_external_source_links_record",
        "data_product_external_source_links",
        ["external_dataset_record_id"],
        schema="medtrust",
    )
    op.create_index(
        "ix_external_source_links_governance",
        "data_product_external_source_links",
        ["governance_profile_id"],
        schema="medtrust",
    )


def downgrade() -> None:
    op.drop_index("ix_external_source_links_governance", table_name="data_product_external_source_links", schema="medtrust")
    op.drop_index("ix_external_source_links_record", table_name="data_product_external_source_links", schema="medtrust")
    op.drop_table("data_product_external_source_links", schema="medtrust")
