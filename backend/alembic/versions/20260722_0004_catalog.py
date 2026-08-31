"""Create the Phase 2-B.2.3 Catalog tables and immutability guards.

Revision ID: 20260722_0004
Revises: 20260722_0003
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0004"
down_revision: str | None = "20260722_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "data_products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("product_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("product_type", sa.String(length=32), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column(
            "lifecycle_status", sa.String(length=16), server_default="draft", nullable=False
        ),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "product_type IN ('controlled_compute', 'api', 'file', 'model_service')",
            name="product_type",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('draft', 'active', 'suspended', 'expired', 'archived')",
            name="lifecycle_status",
        ),
        sa.CheckConstraint(
            "row_version >= 1", name="row_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_data_products_space_id_spaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_data_products_provider_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_data_products_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_products"),
        sa.UniqueConstraint("space_id", "product_code", name="uq_data_products_space_code"),
        sa.UniqueConstraint("space_id", "id", name="uq_data_products_space_id_pair"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_data_products_space_status_domain",
        "data_products",
        ["space_id", "lifecycle_status", "domain"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_data_products_provider_status",
        "data_products",
        ["provider_organization_id", "lifecycle_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_data_products_created_by", "data_products", ["created_by"], schema=SCHEMA
    )

    op.create_table(
        "data_product_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("content_summary", sa.Text(), nullable=False),
        sa.Column(
            "scope_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "linkage_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "quality_report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("classification_level", sa.Text(), nullable=False),
        sa.Column("default_use_mode", sa.Text(), nullable=False),
        sa.Column(
            "default_policy_template", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("default_policy_digest", sa.Text(), nullable=False),
        sa.Column(
            "provenance_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("snapshot_digest", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "version_no > 0", name="version_no_positive"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'under_review', 'approved', 'retired')",
            name="status",
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL AND approved_by IS NULL) OR "
            "(approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="approval_pair",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR snapshot_digest IS NOT NULL",
            name="snapshot_required_after_draft",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_data_product_versions_space_id_spaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "data_product_id"],
            [f"{SCHEMA}.data_products.space_id", f"{SCHEMA}.data_products.id"],
            name="fk_product_versions_space_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_data_product_versions_approved_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_data_product_versions_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_product_versions"),
        sa.UniqueConstraint(
            "data_product_id", "version_no", name="uq_product_versions_product_no"
        ),
        sa.UniqueConstraint(
            "data_product_id", "version_label", name="uq_product_versions_product_label"
        ),
        sa.UniqueConstraint(
            "data_product_id", "id", name="uq_product_versions_product_id_pair"
        ),
        sa.UniqueConstraint("space_id", "id", name="uq_product_versions_space_id_pair"),
        sa.UniqueConstraint(
            "data_product_id",
            "snapshot_digest",
            name="uq_product_versions_product_digest",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_versions_space_status_created",
        "data_product_versions",
        ["space_id", "status", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_versions_product_status_no",
        "data_product_versions",
        ["data_product_id", "status", sa.text("version_no DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_versions_approved_by",
        "data_product_versions",
        ["approved_by"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_versions_created_by",
        "data_product_versions",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "data_resources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("resource_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("modality", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column(
            "schema_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "scope_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "quality_report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("classification_level", sa.Text(), nullable=False),
        sa.Column("resource_digest", sa.Text(), nullable=True),
        sa.Column("position_no", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "position_no > 0", name="position_no_positive"
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_data_resources_space_id_spaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "data_product_version_id"],
            [
                f"{SCHEMA}.data_product_versions.space_id",
                f"{SCHEMA}.data_product_versions.id",
            ],
            name="fk_data_resources_space_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_data_resources_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_resources"),
        sa.UniqueConstraint(
            "data_product_version_id",
            "resource_code",
            name="uq_data_resources_version_code",
        ),
        sa.UniqueConstraint(
            "data_product_version_id",
            "position_no",
            name="uq_data_resources_version_position",
        ),
        sa.UniqueConstraint(
            "data_product_version_id", "id", name="uq_data_resources_version_id_pair"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_data_resources_space_version_position",
        "data_resources",
        ["space_id", "data_product_version_id", "position_no"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_data_resources_created_by", "data_resources", ["created_by"], schema=SCHEMA
    )

    op.create_table(
        "product_sources",
        sa.Column("data_resource_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("local_resource_alias", sa.Text(), nullable=False),
        sa.Column("source_digest", sa.Text(), nullable=False),
        sa.Column("source_role", sa.String(length=16), nullable=False),
        sa.Column("source_snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_role IN ('primary', 'secondary')",
            name="source_role",
        ),
        sa.ForeignKeyConstraint(
            ["data_resource_id"],
            [f"{SCHEMA}.data_resources.id"],
            name="fk_product_sources_resource",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            [f"{SCHEMA}.connectors.id"],
            name="fk_product_sources_connector",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "data_resource_id",
            "connector_id",
            "local_resource_alias",
            name="pk_product_sources",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_sources_connector_resource",
        "product_sources",
        ["connector_id", "data_resource_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "data_product_publications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("visibility", sa.String(length=24), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_by", sa.Uuid(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by", sa.Uuid(), nullable=True),
        sa.Column("withdrawal_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'withdrawn', 'expired')",
            name="status",
        ),
        sa.CheckConstraint(
            "visibility IN ('space', 'restricted', 'invitation_only')",
            name="visibility",
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL AND withdrawn_by IS NULL) OR "
            "(withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)",
            name="withdrawal_pair",
        ),
        sa.CheckConstraint(
            "status != 'withdrawn' OR "
            "(withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)",
            name="withdrawn_requires_actor",
        ),
        sa.CheckConstraint(
            "status = 'withdrawn' OR "
            "(withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawal_reason IS NULL)",
            name="nonwithdrawn_has_no_withdrawal",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_data_product_publications_space_id_spaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "data_product_id"],
            [f"{SCHEMA}.data_products.space_id", f"{SCHEMA}.data_products.id"],
            name="fk_publications_space_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_product_id", "data_product_version_id"],
            [
                f"{SCHEMA}.data_product_versions.data_product_id",
                f"{SCHEMA}.data_product_versions.id",
            ],
            name="fk_publications_product_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_data_product_publications_published_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_data_product_publications_withdrawn_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_product_publications"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_publications_active_product",
        "data_product_publications",
        ["data_product_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_publications_active_version",
        "data_product_publications",
        ["data_product_version_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_publications_space_status_published",
        "data_product_publications",
        ["space_id", "status", sa.text("published_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_publications_version_published",
        "data_product_publications",
        ["data_product_version_id", sa.text("published_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_publications_published_by",
        "data_product_publications",
        ["published_by"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_publications_withdrawn_by",
        "data_product_publications",
        ["withdrawn_by"],
        schema=SCHEMA,
    )

    _create_catalog_guard_triggers()


def _create_catalog_guard_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_product_version_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION 'only a draft product version can be deleted';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.status = 'draft' THEN
                IF NEW.status NOT IN ('draft', 'under_review') THEN
                    RAISE EXCEPTION 'invalid product version transition: % -> %', OLD.status, NEW.status;
                END IF;
            ELSIF OLD.status = 'under_review' THEN
                IF NEW.status NOT IN ('draft', 'approved') THEN
                    RAISE EXCEPTION 'invalid product version transition: % -> %', OLD.status, NEW.status;
                END IF;
                IF ROW(OLD.id, OLD.space_id, OLD.data_product_id, OLD.version_no,
                       OLD.version_label, OLD.content_summary, OLD.scope_metadata,
                       OLD.linkage_metadata, OLD.quality_report, OLD.classification_level,
                       OLD.default_use_mode, OLD.default_policy_template,
                       OLD.default_policy_digest, OLD.provenance_summary,
                       OLD.snapshot_digest, OLD.created_at, OLD.created_by)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.space_id, NEW.data_product_id, NEW.version_no,
                       NEW.version_label, NEW.content_summary, NEW.scope_metadata,
                       NEW.linkage_metadata, NEW.quality_report, NEW.classification_level,
                       NEW.default_use_mode, NEW.default_policy_template,
                       NEW.default_policy_digest, NEW.provenance_summary,
                       NEW.snapshot_digest, NEW.created_at, NEW.created_by) THEN
                    RAISE EXCEPTION 'under_review product version content is immutable';
                END IF;
                IF NEW.status = 'approved' AND
                   (NEW.approved_at IS NULL OR NEW.approved_by IS NULL) THEN
                    RAISE EXCEPTION 'approval actor and timestamp are required';
                END IF;
                IF NEW.status = 'draft' AND
                   (NEW.approved_at IS NOT NULL OR NEW.approved_by IS NOT NULL) THEN
                    RAISE EXCEPTION 'draft product version cannot retain approval data';
                END IF;
            ELSIF OLD.status = 'approved' THEN
                IF NEW.status <> 'retired' THEN
                    RAISE EXCEPTION 'approved product version can only be retired';
                END IF;
                IF ROW(OLD.id, OLD.space_id, OLD.data_product_id, OLD.version_no,
                       OLD.version_label, OLD.content_summary, OLD.scope_metadata,
                       OLD.linkage_metadata, OLD.quality_report, OLD.classification_level,
                       OLD.default_use_mode, OLD.default_policy_template,
                       OLD.default_policy_digest, OLD.provenance_summary,
                       OLD.snapshot_digest, OLD.approved_at, OLD.approved_by,
                       OLD.created_at, OLD.created_by)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.space_id, NEW.data_product_id, NEW.version_no,
                       NEW.version_label, NEW.content_summary, NEW.scope_metadata,
                       NEW.linkage_metadata, NEW.quality_report, NEW.classification_level,
                       NEW.default_use_mode, NEW.default_policy_template,
                       NEW.default_policy_digest, NEW.provenance_summary,
                       NEW.snapshot_digest, NEW.approved_at, NEW.approved_by,
                       NEW.created_at, NEW.created_by) THEN
                    RAISE EXCEPTION 'approved product version content is immutable';
                END IF;
            ELSE
                RAISE EXCEPTION 'retired product version is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_product_version_immutable "
        "BEFORE UPDATE OR DELETE ON medtrust.data_product_versions "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_product_version_immutable()"
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_catalog_resource_draft()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                SELECT status INTO parent_status
                  FROM medtrust.data_product_versions
                 WHERE id = OLD.data_product_version_id;
                IF parent_status IS NOT NULL AND parent_status <> 'draft' THEN
                    RAISE EXCEPTION 'resources can only change in a draft version';
                END IF;
                IF parent_status IS NULL AND TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'resource parent version does not exist';
                END IF;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT status INTO parent_status
                  FROM medtrust.data_product_versions
                 WHERE id = NEW.data_product_version_id;
                IF parent_status IS DISTINCT FROM 'draft' THEN
                    RAISE EXCEPTION 'resources can only change in a draft version';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_catalog_resource_draft "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.data_resources "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_catalog_resource_draft()"
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_catalog_source_draft()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                SELECT v.status INTO parent_status
                  FROM medtrust.data_resources r
                  JOIN medtrust.data_product_versions v
                    ON v.id = r.data_product_version_id
                 WHERE r.id = OLD.data_resource_id;
                IF parent_status IS NOT NULL AND parent_status <> 'draft' THEN
                    RAISE EXCEPTION 'sources can only change in a draft version';
                END IF;
                IF parent_status IS NULL AND TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'source parent resource does not exist';
                END IF;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT v.status INTO parent_status
                  FROM medtrust.data_resources r
                  JOIN medtrust.data_product_versions v
                    ON v.id = r.data_product_version_id
                 WHERE r.id = NEW.data_resource_id;
                IF parent_status IS DISTINCT FROM 'draft' THEN
                    RAISE EXCEPTION 'sources can only change in a draft version';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_catalog_source_draft "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.product_sources "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_catalog_source_draft()"
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_catalog_publication()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE version_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'publication history cannot be deleted';
            END IF;
            IF TG_OP = 'INSERT' THEN
                SELECT status INTO version_status
                  FROM medtrust.data_product_versions
                 WHERE id = NEW.data_product_version_id;
                IF version_status IS DISTINCT FROM 'approved' OR NEW.status <> 'active' THEN
                    RAISE EXCEPTION 'only an approved version can create an active publication';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'active' OR NEW.status NOT IN ('withdrawn', 'expired') THEN
                RAISE EXCEPTION 'invalid publication lifecycle transition';
            END IF;
            IF ROW(OLD.id, OLD.space_id, OLD.data_product_id,
                   OLD.data_product_version_id, OLD.visibility,
                   OLD.published_at, OLD.published_by)
               IS DISTINCT FROM
               ROW(NEW.id, NEW.space_id, NEW.data_product_id,
                   NEW.data_product_version_id, NEW.visibility,
                   NEW.published_at, NEW.published_by) THEN
                RAISE EXCEPTION 'publication identity and visibility are immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_catalog_publication "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.data_product_publications "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_catalog_publication()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_catalog_publication "
        "ON medtrust.data_product_publications"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_catalog_publication()")
    op.execute("DROP TRIGGER IF EXISTS trg_catalog_source_draft ON medtrust.product_sources")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_catalog_source_draft()")
    op.execute("DROP TRIGGER IF EXISTS trg_catalog_resource_draft ON medtrust.data_resources")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_catalog_resource_draft()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_product_version_immutable "
        "ON medtrust.data_product_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_product_version_immutable()")

    op.drop_table("data_product_publications", schema=SCHEMA)
    op.drop_table("product_sources", schema=SCHEMA)
    op.drop_table("data_resources", schema=SCHEMA)
    op.drop_table("data_product_versions", schema=SCHEMA)
    op.drop_table("data_products", schema=SCHEMA)
