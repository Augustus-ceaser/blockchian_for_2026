"""Phase 5.13E-2C-R1 fixed execution readiness sources.

Revision ID: 20260729_0056
Revises: 20260729_0055
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0056"
down_revision = "20260729_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    additions = (
        (
            "source_executor_status_event_id",
            sa.Column("source_executor_status_event_id", sa.Uuid(), nullable=True),
        ),
        (
            "source_executor_status_event_digest",
            sa.Column(
                "source_executor_status_event_digest",
                sa.String(71),
                nullable=True,
            ),
        ),
        (
            "source_attestation_expires_at",
            sa.Column(
                "source_attestation_expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        ),
        (
            "source_asset_metadata_digest",
            sa.Column("source_asset_metadata_digest", sa.String(71), nullable=True),
        ),
        (
            "source_asset_version_id",
            sa.Column("source_asset_version_id", sa.Uuid(), nullable=True),
        ),
        (
            "source_quality_digest",
            sa.Column("source_quality_digest", sa.String(71), nullable=True),
        ),
        (
            "source_model_reference_digest",
            sa.Column(
                "source_model_reference_digest",
                sa.String(71),
                nullable=True,
            ),
        ),
        (
            "source_contract_digest",
            sa.Column("source_contract_digest", sa.String(71), nullable=True),
        ),
        (
            "source_application_digest",
            sa.Column("source_application_digest", sa.String(71), nullable=True),
        ),
        (
            "expires_at",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        ),
        (
            "computed_at",
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    inspector = sa.inspect(op.get_bind())
    current = {
        item["name"]
        for item in inspector.get_columns(
            "control_readiness_snapshots", schema="medtrust"
        )
    }
    for name, column in additions:
        if name not in current:
            op.add_column(
                "control_readiness_snapshots", column, schema="medtrust"
            )
    foreign_keys = inspector.get_foreign_keys(
        "control_readiness_snapshots", schema="medtrust"
    )
    fk_name = "fk_readiness_executor_status_event"
    if not any(
        item.get("constrained_columns")
        == ["source_executor_status_event_id"]
        for item in foreign_keys
    ):
        op.create_foreign_key(
            fk_name,
            "control_readiness_snapshots",
            "hospital_executor_status_events",
            ["source_executor_status_event_id"],
            ["id"],
            source_schema="medtrust",
            referent_schema="medtrust",
            ondelete="RESTRICT",
        )
    asset_fk_name = "fk_readiness_source_asset_version"
    if not any(
        item.get("constrained_columns") == ["source_asset_version_id"]
        for item in foreign_keys
    ):
        op.create_foreign_key(
            asset_fk_name,
            "control_readiness_snapshots",
            "connector_asset_mirror_versions",
            ["source_asset_version_id"],
            ["id"],
            source_schema="medtrust",
            referent_schema="medtrust",
            ondelete="RESTRICT",
        )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION
          medtrust.guard_execution_readiness_snapshot_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'execution readiness snapshots are immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS guard_execution_readiness_snapshot_immutable
        ON medtrust.control_readiness_snapshots
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_execution_readiness_snapshot_immutable
        BEFORE UPDATE OR DELETE ON medtrust.control_readiness_snapshots
        FOR EACH ROW
        WHEN (OLD.readiness_mode = 'FIXED_REFERENCE_EXECUTION')
        EXECUTE FUNCTION
          medtrust.guard_execution_readiness_snapshot_immutable()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_fixed_execution_order_signed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.order_mode = 'FIXED_REFERENCE_EXECUTION'
             AND (
               NEW.canonical_payload IS DISTINCT FROM OLD.canonical_payload
               OR NEW.payload_digest IS DISTINCT FROM OLD.payload_digest
               OR NEW.signature IS DISTINCT FROM OLD.signature
               OR NEW.execution_authorized IS DISTINCT FROM OLD.execution_authorized
               OR NEW.execution_scope IS DISTINCT FROM OLD.execution_scope
               OR NEW.task_type IS DISTINCT FROM OLD.task_type
               OR NEW.max_execution_count IS DISTINCT FROM OLD.max_execution_count
               OR NEW.executor_id IS DISTINCT FROM OLD.executor_id
             ) THEN
            RAISE EXCEPTION 'signed fixed execution order is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS guard_fixed_execution_order_signed
        ON medtrust.execution_orders
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_fixed_execution_order_signed
        BEFORE UPDATE ON medtrust.execution_orders
        FOR EACH ROW EXECUTE FUNCTION
          medtrust.guard_fixed_execution_order_signed()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 5.13E-2C-R1 readiness records are append-only"
    )
