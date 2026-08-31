"""Create the Phase 2-B.2.1 Identity tables.

Revision ID: 20260722_0001
Revises:
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))

    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("organization_type", sa.String(length=32), nullable=False),
        sa.Column("verification_status", sa.String(length=16), server_default="unverified", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("external_identity_ref", sa.Text(), nullable=True),
        sa.Column(
            "contact_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "organization_type IN ('hospital', 'research_institute', 'ai_company', "
            "'service_provider', 'operator')",
            name="ck_organizations_organization_type",
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'pending', 'verified', 'failed')",
            name="ck_organizations_verification_status",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'withdrawn')",
            name="ck_organizations_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_organizations_row_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint(
            "external_identity_ref",
            name="uq_organizations_external_identity_ref",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organizations_type_status",
        "organizations",
        ["organization_type", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organizations_verification_status",
        "organizations",
        ["verification_status", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organizations_created_by",
        "organizations",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_issuer", sa.Text(), nullable=False),
        sa.Column("identity_subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="invited", nullable=False),
        sa.Column("mfa_status", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'disabled')",
            name="ck_users_status",
        ),
        sa.CheckConstraint(
            "mfa_status IN ('unknown', 'disabled', 'enabled')",
            name="ck_users_mfa_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_users_row_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint(
            "identity_issuer",
            "identity_subject",
            name="uq_users_issuer_subject",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_index(
        "ix_users_status_last_authenticated",
        "users",
        ["status", "last_authenticated_at"],
        schema=SCHEMA,
    )

    op.create_foreign_key(
        "fk_organizations_created_by_users",
        "organizations",
        "users",
        ["created_by"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "organization_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="invited", nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'removed')",
            name="ck_organization_members_status",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_organization_members_valid_period",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_organization_members_row_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_organization_members_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            name="fk_organization_members_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_organization_members_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_members"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_members_organization_user",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organization_members_user_status",
        "organization_members",
        ["user_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organization_members_organization_status",
        "organization_members",
        ["organization_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organization_members_created_by",
        "organization_members",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "organization_member_roles",
        sa.Column("organization_member_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.String(length=48), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "role_code IN ('provider_data_admin', 'provider_output_reviewer', "
            "'consumer_researcher', 'consumer_ai_developer', 'contract_signer', "
            "'connector_operator', 'auditor')",
            name="ck_organization_member_roles_role_code",
        ),
        sa.ForeignKeyConstraint(
            ["organization_member_id"],
            [f"{SCHEMA}.organization_members.id"],
            name="fk_member_roles_member",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_organization_member_roles_granted_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "organization_member_id",
            "role_code",
            name="pk_organization_member_roles",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organization_member_roles_role_member",
        "organization_member_roles",
        ["role_code", "organization_member_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organization_member_roles_granted_by",
        "organization_member_roles",
        ["granted_by"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("organization_member_roles", schema=SCHEMA)
    op.drop_table("organization_members", schema=SCHEMA)
    op.drop_constraint(
        "fk_organizations_created_by_users",
        "organizations",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("users", schema=SCHEMA)
    op.drop_table("organizations", schema=SCHEMA)
