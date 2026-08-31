"""Create the Phase 2-B.3-B1 Application aggregate tables.

Revision ID: 20260722_0005
Revises: 20260722_0004
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0005"
down_revision: str | None = "20260722_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_data_products_space_provider_id_pair",
        "data_products",
        ["space_id", "provider_organization_id", "id"],
        schema=SCHEMA,
    )

    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("application_number", sa.Text(), nullable=False),
        sa.Column("applicant_organization_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("legal_or_ethics_basis", sa.Text(), nullable=True),
        sa.Column("algorithm_name", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("algorithm_digest", sa.Text(), nullable=False),
        sa.Column("requested_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("requested_run_limit", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="draft", nullable=False
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_summary", sa.Text(), nullable=True),
        sa.Column(
            "is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'prechecking', 'provider_review', "
            "'approved', 'rejected', 'withdrawn')",
            name="status",
        ),
        sa.CheckConstraint(
            "applicant_organization_id <> provider_organization_id",
            name="applicant_provider_distinct",
        ),
        sa.CheckConstraint(
            "requested_duration_seconds > 0", name="duration_positive"
        ),
        sa.CheckConstraint("requested_run_limit > 0", name="run_limit_positive"),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.CheckConstraint(
            "(status = 'draft' AND submitted_at IS NULL) OR "
            "(status <> 'draft' AND submitted_at IS NOT NULL)",
            name="submission_timeline",
        ),
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected') AND decided_at IS NOT NULL) OR "
            "(status NOT IN ('approved', 'rejected') AND decided_at IS NULL)",
            name="decision_timeline",
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn' AND withdrawn_at IS NOT NULL) OR "
            "(status <> 'withdrawn' AND withdrawn_at IS NULL)",
            name="withdrawal_timeline",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_applications_space_id_spaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_applications_applicant_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_user_id"],
            [f"{SCHEMA}.users.id"],
            name="fk_applications_applicant_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_applications_provider_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_applications_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_applications"),
        sa.UniqueConstraint(
            "space_id", "application_number", name="uq_applications_space_number"
        ),
        sa.UniqueConstraint(
            "id",
            "space_id",
            "provider_organization_id",
            name="uq_applications_id_space_provider",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_applications_space_status_submitted",
        "applications",
        ["space_id", "status", sa.text("submitted_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_applications_applicant_status_created",
        "applications",
        ["applicant_organization_id", "status", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_applications_provider_status_submitted",
        "applications",
        ["provider_organization_id", "status", sa.text("submitted_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_applications_applicant_user",
        "applications",
        ["applicant_user_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_applications_created_by",
        "applications",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "application_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("position_no", sa.Integer(), nullable=False),
        sa.Column("requested_product_snapshot_digest", sa.Text(), nullable=False),
        sa.Column("requested_policy_digest", sa.Text(), nullable=False),
        sa.Column(
            "requested_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("position_no > 0", name="position_no_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(requested_scope) = 'object'",
            name="requested_scope_object",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "space_id", "provider_organization_id"],
            [
                f"{SCHEMA}.applications.id",
                f"{SCHEMA}.applications.space_id",
                f"{SCHEMA}.applications.provider_organization_id",
            ],
            name="fk_application_items_application_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "provider_organization_id", "data_product_id"],
            [
                f"{SCHEMA}.data_products.space_id",
                f"{SCHEMA}.data_products.provider_organization_id",
                f"{SCHEMA}.data_products.id",
            ],
            name="fk_application_items_product_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_product_id", "data_product_version_id"],
            [
                f"{SCHEMA}.data_product_versions.data_product_id",
                f"{SCHEMA}.data_product_versions.id",
            ],
            name="fk_application_items_product_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_items"),
        sa.UniqueConstraint(
            "application_id",
            "data_product_version_id",
            name="uq_application_items_application_version",
        ),
        sa.UniqueConstraint(
            "application_id", "position_no", name="uq_application_items_position"
        ),
        sa.UniqueConstraint(
            "application_id",
            "id",
            "data_product_version_id",
            name="uq_application_items_application_id_version",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_items_version_application",
        "application_items",
        ["data_product_version_id", "application_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_items_product",
        "application_items",
        ["data_product_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "application_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("snapshot_digest", sa.Text(), nullable=False),
        sa.Column(
            "digest_algorithm",
            sa.String(length=16),
            server_default="sha256",
            nullable=False,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("captured_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("schema_version <> ''", name="schema_version_nonempty"),
        sa.CheckConstraint("digest_algorithm IN ('sha256')", name="digest_algorithm"),
        sa.CheckConstraint("jsonb_typeof(manifest) = 'object'", name="manifest_object"),
        sa.ForeignKeyConstraint(
            ["application_id"],
            [f"{SCHEMA}.applications.id"],
            name="fk_application_snapshots_application_id_applications",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["captured_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_application_snapshots_captured_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_snapshots"),
        sa.UniqueConstraint(
            "application_id", name="uq_application_snapshots_application"
        ),
        sa.UniqueConstraint(
            "application_id",
            "id",
            "snapshot_digest",
            name="uq_application_snapshots_application_id_digest",
        ),
        sa.UniqueConstraint(
            "application_id",
            "snapshot_digest",
            name="uq_application_snapshots_application_digest",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_snapshots_digest",
        "application_snapshots",
        ["snapshot_digest"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_snapshots_captured_by",
        "application_snapshots",
        ["captured_by"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_application_lifecycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'draft' THEN
                    RAISE EXCEPTION 'new application must start as draft';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION 'only a draft application can be deleted';
                END IF;
                RETURN OLD;
            END IF;
            IF NOT (
                (OLD.status = 'draft' AND NEW.status IN ('draft', 'submitted', 'withdrawn')) OR
                (OLD.status = 'submitted' AND NEW.status IN ('prechecking', 'withdrawn')) OR
                (OLD.status = 'prechecking' AND NEW.status IN ('provider_review', 'rejected', 'withdrawn')) OR
                (OLD.status = 'provider_review' AND NEW.status IN ('approved', 'rejected', 'withdrawn'))
            ) THEN
                RAISE EXCEPTION 'invalid application lifecycle transition';
            END IF;
            IF OLD.status <> 'draft' AND
               ROW(OLD.id, OLD.space_id, OLD.application_number,
                   OLD.applicant_organization_id, OLD.applicant_user_id,
                   OLD.provider_organization_id, OLD.purpose,
                   OLD.legal_or_ethics_basis, OLD.algorithm_name,
                   OLD.algorithm_version, OLD.algorithm_digest,
                   OLD.requested_duration_seconds, OLD.requested_run_limit,
                   OLD.is_demo, OLD.created_at, OLD.created_by)
               IS DISTINCT FROM
               ROW(NEW.id, NEW.space_id, NEW.application_number,
                   NEW.applicant_organization_id, NEW.applicant_user_id,
                   NEW.provider_organization_id, NEW.purpose,
                   NEW.legal_or_ethics_basis, NEW.algorithm_name,
                   NEW.algorithm_version, NEW.algorithm_digest,
                   NEW.requested_duration_seconds, NEW.requested_run_limit,
                   NEW.is_demo, NEW.created_at, NEW.created_by) THEN
                RAISE EXCEPTION 'submitted application content is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_application_lifecycle "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.applications "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_application_lifecycle()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_application_item_draft()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT status INTO parent_status
                  FROM medtrust.applications WHERE id = OLD.application_id;
                IF parent_status IS NULL THEN
                    RETURN OLD;
                END IF;
                IF parent_status <> 'draft' THEN
                    RAISE EXCEPTION 'application items can only change in draft';
                END IF;
                RETURN OLD;
            END IF;
            SELECT status INTO parent_status
              FROM medtrust.applications WHERE id = NEW.application_id;
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'application items can only change in draft';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_application_item_draft "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.application_items "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_application_item_draft()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_application_snapshot_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                RAISE EXCEPTION 'application snapshot is immutable';
            END IF;
            SELECT status INTO parent_status
              FROM medtrust.applications WHERE id = NEW.application_id;
            IF parent_status IS DISTINCT FROM 'submitted' THEN
                RAISE EXCEPTION 'snapshot can only be created during submission';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_application_snapshot_immutable "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.application_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_application_snapshot_immutable()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.require_application_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IN ('submitted', 'prechecking', 'provider_review', 'approved', 'rejected')
               AND NOT EXISTS (
                   SELECT 1 FROM medtrust.application_snapshots
                    WHERE application_id = NEW.id
               ) THEN
                RAISE EXCEPTION 'submitted application requires an immutable snapshot';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_application_requires_snapshot "
        "AFTER INSERT OR UPDATE OF status ON medtrust.applications "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION medtrust.require_application_snapshot()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_application_requires_snapshot "
        "ON medtrust.applications"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.require_application_snapshot()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_application_snapshot_immutable "
        "ON medtrust.application_snapshots"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.guard_application_snapshot_immutable()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_application_item_draft "
        "ON medtrust.application_items"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_application_item_draft()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_application_lifecycle ON medtrust.applications"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_application_lifecycle()")

    op.drop_table("application_snapshots", schema=SCHEMA)
    op.drop_table("application_items", schema=SCHEMA)
    op.drop_table("applications", schema=SCHEMA)
    op.drop_constraint(
        "uq_data_products_space_provider_id_pair",
        "data_products",
        schema=SCHEMA,
        type_="unique",
    )
