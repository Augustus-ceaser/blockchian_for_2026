"""Create the Phase 2-B.2.2-A Spaces tables.

Revision ID: 20260722_0002
Revises: 20260722_0001
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0002"
down_revision: str | None = "20260722_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "spaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("space_type", sa.String(length=16), nullable=False),
        sa.Column("operator_organization_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("ruleset_version", sa.Text(), nullable=False),
        sa.Column("classification_scheme_version", sa.Text(), nullable=False),
        sa.Column(
            "default_retention_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "space_type IN ('industry', 'enterprise', 'city')",
            name="ck_spaces_space_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'suspended', 'closed')",
            name="ck_spaces_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_spaces_row_version_positive"),
        sa.ForeignKeyConstraint(
            ["operator_organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_spaces_operator_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_spaces_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_spaces"),
        sa.UniqueConstraint("code", name="uq_spaces_code"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_spaces_operator_status",
        "spaces",
        ["operator_organization_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_spaces_type_status",
        "spaces",
        ["space_type", "status"],
        schema=SCHEMA,
    )
    op.create_index("ix_spaces_created_by", "spaces", ["created_by"], schema=SCHEMA)

    op.create_table(
        "space_participants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "admission_status",
            sa.String(length=16),
            server_default="applied",
            nullable=False,
        ),
        sa.Column("ruleset_accepted_version", sa.Text(), nullable=True),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "admission_status IN ('applied', 'reviewing', 'admitted', 'rejected', "
            "'suspended', 'exited')",
            name="ck_space_participants_admission_status",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_space_participants_row_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_space_participants_space_id_spaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_space_participants_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_space_participants_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_space_participants"),
        sa.UniqueConstraint(
            "space_id",
            "organization_id",
            name="uq_space_participants_space_organization",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_space_participants_organization_status",
        "space_participants",
        ["organization_id", "admission_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_space_participants_admitted",
        "space_participants",
        ["space_id", "admitted_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("admission_status = 'admitted'"),
    )
    op.create_index(
        "ix_space_participants_created_by",
        "space_participants",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "space_participant_roles",
        sa.Column("space_participant_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.String(length=32), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "role_code IN ('provider', 'consumer', 'service_provider', 'operator')",
            name="ck_space_participant_roles_role_code",
        ),
        sa.ForeignKeyConstraint(
            ["space_participant_id"],
            [f"{SCHEMA}.space_participants.id"],
            name="fk_space_roles_participant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_space_participant_roles_granted_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "space_participant_id",
            "role_code",
            name="pk_space_participant_roles",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_space_participant_roles_role_participant",
        "space_participant_roles",
        ["role_code", "space_participant_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_space_participant_roles_granted_by",
        "space_participant_roles",
        ["granted_by"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("space_participant_roles", schema=SCHEMA)
    op.drop_table("space_participants", schema=SCHEMA)
    op.drop_table("spaces", schema=SCHEMA)
