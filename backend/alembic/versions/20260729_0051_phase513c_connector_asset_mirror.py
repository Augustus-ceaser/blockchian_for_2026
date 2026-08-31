"""Add metadata-only Connector asset mirror.

Revision ID: 20260729_0051
Revises: 20260729_0050
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260729_0051"
down_revision = "20260729_0050"
branch_labels = None
depends_on = None
S = "medtrust"


def upgrade() -> None:
    op.add_column("connector_capability_manifests", sa.Column("metadata_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()), schema=S)
    op.add_column("connector_capability_manifests", sa.Column("data_quality_summary_enabled", sa.Boolean(), nullable=False, server_default=sa.false()), schema=S)
    op.execute("""
    DO $$ DECLARE constraint_name text;
    BEGIN
      SELECT c.conname INTO constraint_name
      FROM pg_constraint c
      WHERE c.conrelid='medtrust.connector_capability_manifests'::regclass
        AND c.contype='c'
        AND pg_get_constraintdef(c.oid) ILIKE '%local_asset_registry_enabled%';
      IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE medtrust.connector_capability_manifests DROP CONSTRAINT %I', constraint_name);
      END IF;
    END $$;
    """)
    op.create_check_constraint(
        "ck_connector_capability_manifests_alpha_capabilities_disabled",
        "connector_capability_manifests",
        "NOT execution_enabled AND NOT data_transfer_enabled AND NOT model_transfer_enabled AND NOT artifact_egress_enabled AND NOT hard_isolation",
        schema=S,
    )
    op.create_table(
        "connector_asset_mirrors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connector_id", sa.Uuid(), sa.ForeignKey(f"{S}.hospital_connectors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("space_id", sa.Uuid(), sa.ForeignKey(f"{S}.spaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey(f"{S}.organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("local_asset_key", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("asset_kind", sa.String(32), nullable=False),
        sa.Column("modality", sa.String(64), nullable=False),
        sa.Column("source_category", sa.String(40), nullable=False),
        sa.Column("sensitivity_classification", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("first_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connector_id", "local_asset_key", name="uq_connector_asset_mirror_key"),
        sa.CheckConstraint("status IN ('synced','paused','unavailable','archived')", name="ck_connector_asset_mirrors_status"),
        schema=S,
    )
    op.create_index("ix_connector_asset_mirrors_space_status", "connector_asset_mirrors", ["space_id", "status"], schema=S)
    op.create_table(
        "connector_asset_mirror_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mirror_id", sa.Uuid(), sa.ForeignKey(f"{S}.connector_asset_mirrors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("connector_id", sa.Uuid(), sa.ForeignKey(f"{S}.hospital_connectors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bundle_id", sa.String(80), nullable=False),
        sa.Column("bundle_sequence", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("metadata_digest", sa.String(71), nullable=False),
        sa.Column("schema_digest", sa.String(71), nullable=False),
        sa.Column("quality_digest", sa.String(71), nullable=False),
        sa.Column("bundle_digest", sa.String(71), nullable=False),
        sa.Column("disclosure_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("quality_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deidentification_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("known_limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warning_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connector_id", "bundle_id", name="uq_connector_asset_bundle_id"),
        sa.UniqueConstraint("connector_id", "bundle_sequence", name="uq_connector_asset_bundle_sequence"),
        sa.CheckConstraint("bundle_sequence > 0", name="ck_connector_asset_mirror_versions_bundle_sequence_positive"),
        schema=S,
    )
    op.create_index("ix_connector_asset_mirror_versions_mirror_received", "connector_asset_mirror_versions", ["mirror_id", "received_at"], schema=S)
    op.create_foreign_key(
        "fk_connector_asset_mirrors_current_version",
        "connector_asset_mirrors", "connector_asset_mirror_versions",
        ["current_version_id"], ["id"], source_schema=S, referent_schema=S, ondelete="RESTRICT",
    )
    op.execute("""
    CREATE FUNCTION medtrust.guard_connector_asset_mirror_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'connector asset mirror history is immutable' USING ERRCODE='23514';
      END IF;
      IF TG_TABLE_NAME='connector_asset_mirror_versions' THEN
        RAISE EXCEPTION 'connector asset mirror versions are append-only' USING ERRCODE='23514';
      END IF;
      IF OLD.connector_id<>NEW.connector_id OR OLD.space_id<>NEW.space_id
         OR OLD.organization_id<>NEW.organization_id OR OLD.local_asset_key<>NEW.local_asset_key THEN
        RAISE EXCEPTION 'connector asset mirror identity is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    """)
    for table in ("connector_asset_mirrors", "connector_asset_mirror_versions"):
        op.execute(f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {S}.{table} FOR EACH ROW EXECUTE FUNCTION {S}.guard_connector_asset_mirror_immutable()")


def downgrade() -> None:
    for table in ("connector_asset_mirror_versions", "connector_asset_mirrors"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {S}.{table}")
    op.execute(f"DROP FUNCTION IF EXISTS {S}.guard_connector_asset_mirror_immutable()")
    op.drop_constraint("fk_connector_asset_mirrors_current_version", "connector_asset_mirrors", schema=S)
    op.drop_table("connector_asset_mirror_versions", schema=S)
    op.drop_table("connector_asset_mirrors", schema=S)
    op.execute("""
    DO $$ DECLARE constraint_name text;
    BEGIN
      SELECT c.conname INTO constraint_name
      FROM pg_constraint c
      WHERE c.conrelid='medtrust.connector_capability_manifests'::regclass
        AND c.contype='c'
        AND pg_get_constraintdef(c.oid) ILIKE '%artifact_egress_enabled%'
        AND pg_get_constraintdef(c.oid) NOT ILIKE '%local_asset_registry_enabled%';
      IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE medtrust.connector_capability_manifests DROP CONSTRAINT %I', constraint_name);
      END IF;
    END $$;
    """)
    op.create_check_constraint(
        "ck_connector_capability_manifests_alpha_capabilities_disabled",
        "connector_capability_manifests",
        "NOT execution_enabled AND NOT data_transfer_enabled AND NOT model_transfer_enabled AND NOT local_asset_registry_enabled AND NOT artifact_egress_enabled AND NOT hard_isolation",
        schema=S,
    )
    op.drop_column("connector_capability_manifests", "data_quality_summary_enabled", schema=S)
    op.drop_column("connector_capability_manifests", "metadata_sync_enabled", schema=S)
