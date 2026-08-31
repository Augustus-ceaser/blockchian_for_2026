"""Phase 5.13D signed control-only policy workflow.

Revision ID: 20260729_0052
Revises: 20260729_0051
"""

from alembic import op

from app.modules.policy_control.models import (
    ConnectorOrderDecision, ConnectorOrderReceipt, ControlReadinessSnapshot,
    ExecutionOrder, ExecutionOrderDeliveryAttempt, PolicyBundle,
    PolicyBundleVersion, PolicyRevocation, PolicySigningKey,
)

revision = "20260729_0052"
down_revision = "20260729_0051"
branch_labels = None
depends_on = None

TABLES = [
    PolicySigningKey.__table__,
    ControlReadinessSnapshot.__table__,
    PolicyBundle.__table__,
    PolicyBundleVersion.__table__,
    PolicyRevocation.__table__,
    ExecutionOrder.__table__,
    ExecutionOrderDeliveryAttempt.__table__,
    ConnectorOrderReceipt.__table__,
    ConnectorOrderDecision.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    # SQLAlchemy sorts the named circular PolicyBundle/Version foreign keys.
    PolicySigningKey.metadata.create_all(bind=bind, tables=TABLES, checkfirst=True)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_signed_policy_immutability()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'signed policy protocol records are append-only';
          END IF;
          IF TG_TABLE_NAME = 'policy_bundle_versions'
             AND OLD.signature <> '' THEN
            RAISE EXCEPTION 'signed policy bundle versions are immutable';
          END IF;
          IF TG_TABLE_NAME IN ('policy_revocations','connector_order_receipts','connector_order_decisions') THEN
            RAISE EXCEPTION 'signed policy protocol records are immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        """
    )
    for table in (
        "policy_bundle_versions", "policy_revocations",
        "connector_order_receipts", "connector_order_decisions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER guard_{table}_immutability
            BEFORE UPDATE OR DELETE ON medtrust.{table}
            FOR EACH ROW EXECUTE FUNCTION medtrust.guard_signed_policy_immutability()
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        table.drop(bind=op.get_bind(), checkfirst=True)
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_signed_policy_immutability()")
