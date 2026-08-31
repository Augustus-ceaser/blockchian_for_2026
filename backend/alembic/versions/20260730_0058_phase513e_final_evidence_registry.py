"""Phase 5.13E Final hospital evidence summary registry.

Revision ID: 20260730_0058
Revises: 20260729_0057
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0058"
down_revision = "20260729_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospital_evidence_bundle_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bundle_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "connector_id", sa.Uuid(),
            sa.ForeignKey(
                "medtrust.hospital_connectors.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "space_id", sa.Uuid(),
            sa.ForeignKey("medtrust.spaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organization_id", sa.Uuid(),
            sa.ForeignKey("medtrust.organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("bundle_version", sa.Integer(), nullable=False),
        sa.Column(
            "local_artifact_ref", sa.String(36), nullable=False, unique=True
        ),
        sa.Column(
            "reference_execution_id", sa.String(36), nullable=False,
            unique=True,
        ),
        sa.Column(
            "policy_bundle_id", sa.Uuid(),
            sa.ForeignKey("medtrust.policy_bundles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_bundle_version_id", sa.Uuid(),
            sa.ForeignKey(
                "medtrust.policy_bundle_versions.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "execution_order_id", sa.Uuid(),
            sa.ForeignKey("medtrust.execution_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "artifact_digest", sa.String(71), nullable=False, unique=True
        ),
        sa.Column(
            "review_digest", sa.String(71), nullable=False, unique=True
        ),
        sa.Column(
            "causal_validation_digest", sa.String(71), nullable=False,
            unique=True,
        ),
        sa.Column("local_audit_head", sa.String(71), nullable=False),
        sa.Column(
            "bundle_digest", sa.String(71), nullable=False, unique=True
        ),
        sa.Column("signing_key_id", sa.String(100), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.JSON(), nullable=False),
        sa.Column(
            "verification_status", sa.String(16), nullable=False,
            server_default="verified",
        ),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "bundle_version = 1",
            name="ck_hospital_evidence_bundle_receipts_bundle_version",
        ),
        sa.CheckConstraint(
            "schema_version = "
            "'phase5.13E-Final/evidence-bundle/v1'",
            name="ck_hospital_evidence_bundle_receipts_schema_version",
        ),
        sa.CheckConstraint(
            "verification_status = 'verified'",
            name="ck_hospital_evidence_bundle_receipts_verification_status",
        ),
        schema="medtrust",
    )
    op.create_index(
        "ix_hospital_evidence_bundle_connector_received",
        "hospital_evidence_bundle_receipts",
        ["connector_id", "received_at"],
        schema="medtrust",
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_hospital_evidence_receipt_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'hospital evidence receipts are append-only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_hospital_evidence_receipt_immutable
        BEFORE UPDATE OR DELETE
        ON medtrust.hospital_evidence_bundle_receipts
        FOR EACH ROW EXECUTE FUNCTION
          medtrust.guard_hospital_evidence_receipt_immutable()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Hospital evidence receipts are append-only")
