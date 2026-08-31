"""Phase 5.13E-2C-R1 signed execution consumption receipts.

Revision ID: 20260729_0057
Revises: 20260729_0056
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0057"
down_revision = "20260729_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_order_consumption_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "execution_order_id",
            sa.Uuid(),
            sa.ForeignKey("medtrust.execution_orders.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "connector_id",
            sa.Uuid(),
            sa.ForeignKey(
                "medtrust.hospital_connectors.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "authorization_snapshot_id", sa.String(36),
            nullable=False, unique=True,
        ),
        sa.Column("task_manifest_id", sa.String(36), nullable=False, unique=True),
        sa.Column("runtime_session_id", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "reference_execution_id", sa.String(36),
            nullable=False, unique=True,
        ),
        sa.Column("consumption_payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("connector_key_id", sa.String(100), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        schema="medtrust",
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_execution_consumption_receipt_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'execution consumption receipts are append-only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_execution_consumption_receipt_immutable
        BEFORE UPDATE OR DELETE
        ON medtrust.execution_order_consumption_receipts
        FOR EACH ROW EXECUTE FUNCTION
          medtrust.guard_execution_consumption_receipt_immutable()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Execution consumption receipts are append-only")
