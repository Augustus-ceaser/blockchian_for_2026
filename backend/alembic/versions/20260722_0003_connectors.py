"""Create the Phase 2-B.2.2-B Connector tables.

Revision ID: 20260722_0003
Revises: 20260722_0002
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0003"
down_revision: str | None = "20260722_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("owner_organization_id", sa.Uuid(), nullable=False),
        sa.Column("external_connector_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "runtime_status",
            sa.String(length=16),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column(
            "endpoint_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("certificate_fingerprint", sa.Text(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_policy_ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'verified', 'failed', 'revoked')",
            name="ck_connectors_verification_status",
        ),
        sa.CheckConstraint(
            "runtime_status IN ('unknown', 'online', 'degraded', 'offline', 'maintenance')",
            name="ck_connectors_runtime_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_connectors_row_version_positive"),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_connectors_space_id_spaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_connectors_owner_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_connectors_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connectors"),
        sa.UniqueConstraint(
            "space_id",
            "owner_organization_id",
            "name",
            name="uq_connectors_space_owner_name",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_connectors_space_external_id",
        "connectors",
        ["space_id", "external_connector_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("external_connector_id IS NOT NULL"),
    )
    op.create_index(
        "ix_connectors_space_verification_runtime",
        "connectors",
        ["space_id", "verification_status", "runtime_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_connectors_owner_runtime",
        "connectors",
        ["owner_organization_id", "runtime_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_connectors_unhealthy_heartbeat",
        "connectors",
        ["space_id", "last_heartbeat_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("runtime_status IN ('degraded', 'offline')"),
    )
    op.create_index(
        "ix_connectors_created_by",
        "connectors",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "connector_capabilities",
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("capability_code", sa.Text(), nullable=False),
        sa.Column("capability_version", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="declared", nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('declared', 'verified', 'disabled')",
            name="ck_connector_capabilities_status",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            [f"{SCHEMA}.connectors.id"],
            name="fk_connector_capabilities_connector",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "connector_id",
            "capability_code",
            "capability_version",
            name="pk_connector_capabilities",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_connector_capabilities_code_status",
        "connector_capabilities",
        ["capability_code", "status", "connector_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("connector_capabilities", schema=SCHEMA)
    op.drop_table("connectors", schema=SCHEMA)
