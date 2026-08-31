"""Create the Phase 2-B.3-B2 Application extension tables.

Revision ID: 20260722_0006
Revises: 20260722_0005
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0006"
down_revision: str | None = "20260722_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "application_requested_actions",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{\"schema_version\":\"1.0\"}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_code IN ('ai_training', 'model_validation', "
            "'research_analysis', 'drug_development')",
            name="action_code",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'",
            name="parameters_object",
        ),
        sa.CheckConstraint(
            "parameters ? 'schema_version' "
            "AND jsonb_typeof(parameters -> 'schema_version') = 'string' "
            "AND length(trim(parameters ->> 'schema_version')) > 0",
            name="parameters_schema_version",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            [f"{SCHEMA}.applications.id"],
            name="fk_application_requested_actions_application",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "application_id",
            "action_code",
            name="pk_application_requested_actions",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_requested_actions_code_application",
        "application_requested_actions",
        ["action_code", "application_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "application_requested_output_types",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("requires_manual_review", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "output_type IN ('aggregate_statistics', 'model_artifact', "
            "'feature_dataset', 'risk_scoring_model')",
            name="output_type",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            [f"{SCHEMA}.applications.id"],
            name="fk_application_requested_outputs_application",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "application_id",
            "output_type",
            name="pk_application_requested_output_types",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_requested_outputs_type_application",
        "application_requested_output_types",
        ["output_type", "application_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "application_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "scan_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "attachment_type IN ('research_protocol', 'ethics', "
            "'authorization', 'algorithm_document', 'compliance_evidence', 'other')",
            name="attachment_type",
        ),
        sa.CheckConstraint(
            "scan_status IN ('pending', 'clean', 'rejected')",
            name="scan_status",
        ),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0",
            name="display_name_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(storage_ref)) > 0",
            name="storage_ref_nonempty",
        ),
        sa.CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="content_digest_shape",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="size_bytes_nonnegative"),
        sa.ForeignKeyConstraint(
            ["application_id"],
            [f"{SCHEMA}.applications.id"],
            name="fk_application_attachments_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_application_attachments_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_attachments"),
        sa.UniqueConstraint(
            "application_id",
            "content_digest",
            name="uq_application_attachments_application_digest",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_attachments_application_type",
        "application_attachments",
        ["application_id", "attachment_type"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_attachments_scan_application",
        "application_attachments",
        ["scan_status", "application_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_attachments_created_by",
        "application_attachments",
        ["created_by"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_application_component_draft()
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
                    RAISE EXCEPTION 'application components can only change in draft';
                END IF;
                RETURN OLD;
            END IF;
            SELECT status INTO parent_status
              FROM medtrust.applications WHERE id = NEW.application_id;
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'application components can only change in draft';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    for table_name, trigger_name in (
        ("application_requested_actions", "trg_application_action_draft"),
        ("application_requested_output_types", "trg_application_output_draft"),
        ("application_attachments", "trg_application_attachment_draft"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} "
            f"BEFORE INSERT OR UPDATE OR DELETE ON medtrust.{table_name} "
            "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_application_component_draft()"
        )


def downgrade() -> None:
    for table_name, trigger_name in (
        ("application_attachments", "trg_application_attachment_draft"),
        ("application_requested_output_types", "trg_application_output_draft"),
        ("application_requested_actions", "trg_application_action_draft"),
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON medtrust.{table_name}"
        )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.guard_application_component_draft()"
    )

    op.drop_table("application_attachments", schema=SCHEMA)
    op.drop_table("application_requested_output_types", schema=SCHEMA)
    op.drop_table("application_requested_actions", schema=SCHEMA)
