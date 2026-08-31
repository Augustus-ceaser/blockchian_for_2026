"""Add contract readiness and multi-party Artifact review evidence.

Revision ID: 20260723_0022
Revises: 20260723_0021
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0022"
down_revision: str | None = "20260723_0021"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "contract_readiness_confirmations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("readiness_type", sa.String(24), nullable=False),
        sa.Column("responsible_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_snapshot", JSONB, nullable=False),
        sa.Column("target_digest", sa.String(71), nullable=False),
        sa.Column("evidence_snapshot", JSONB, nullable=False),
        sa.Column("evidence_digest", sa.String(71), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "readiness_type IN ('data_ready','model_ready','platform_ready')",
            name="ck_contract_readiness_confirmations_readiness_type",
        ),
        sa.CheckConstraint(
            "target_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "evidence_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_contract_readiness_confirmations_digest_formats",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id"], [f"{SCHEMA}.contract_revisions.id"],
            name="fk_contract_readiness_revision", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            [f"{SCHEMA}.space_participants.space_id", f"{SCHEMA}.space_participants.organization_id"],
            name="fk_contract_readiness_responsible_participant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_organization_id", "confirmed_by_user_id"],
            [f"{SCHEMA}.organization_members.organization_id", f"{SCHEMA}.organization_members.user_id"],
            name="fk_contract_readiness_confirmer_member", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contract_readiness_confirmations"),
        sa.UniqueConstraint(
            "contract_revision_id", "readiness_type", "target_digest",
            name="uq_contract_readiness_revision_type_target",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_readiness_revision_type_time",
        "contract_readiness_confirmations",
        ["contract_revision_id", "readiness_type", sa.text("confirmed_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "artifact_review_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_content_digest", sa.String(71), nullable=False),
        sa.Column("review_type", sa.String(40), nullable=False),
        sa.Column("responsible_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("routing_rule_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "review_type IN ('data_provider_egress_review','platform_compliance_review',"
            "'model_provider_quality_review')",
            name="ck_artifact_review_tasks_review_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','claimed','decided','cancelled')",
            name="ck_artifact_review_tasks_status",
        ),
        sa.CheckConstraint(
            "routing_rule_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_artifact_review_tasks_routing_rule_digest_format",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_artifact_review_tasks_row_version_positive"),
        sa.CheckConstraint(
            "(status = 'pending' AND assigned_user_id IS NULL AND claimed_at IS NULL "
            "AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'claimed' AND assigned_user_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'decided' AND assigned_user_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND decided_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND decided_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_artifact_review_tasks_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "space_id", "target_content_digest"],
            [f"{SCHEMA}.artifacts.id", f"{SCHEMA}.artifacts.space_id", f"{SCHEMA}.artifacts.content_digest"],
            name="fk_artifact_review_tasks_artifact_scope", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            [f"{SCHEMA}.space_participants.space_id", f"{SCHEMA}.space_participants.organization_id"],
            name="fk_artifact_review_tasks_responsible_participant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_organization_id", "assigned_user_id"],
            [f"{SCHEMA}.organization_members.organization_id", f"{SCHEMA}.organization_members.user_id"],
            name="fk_artifact_review_tasks_assigned_member", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_review_tasks"),
        sa.UniqueConstraint("artifact_id", "review_type", name="uq_artifact_review_tasks_artifact_type"),
        sa.UniqueConstraint("id", "target_content_digest", name="uq_artifact_review_tasks_id_digest"),
        sa.UniqueConstraint("id", "responsible_organization_id", name="uq_artifact_review_tasks_id_org"),
        sa.UniqueConstraint(
            "id", "target_content_digest", "responsible_organization_id",
            name="uq_artifact_review_tasks_decision_scope",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_review_tasks_org_status",
        "artifact_review_tasks",
        ["responsible_organization_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_review_tasks_artifact",
        "artifact_review_tasks",
        ["artifact_id", "review_type"],
        schema=SCHEMA,
    )

    op.create_table(
        "artifact_review_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_review_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("responsible_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_content_digest", sa.String(71), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("evidence_snapshot", JSONB, nullable=False),
        sa.Column("evidence_digest", sa.String(71), nullable=False),
        sa.Column("decision_digest", sa.String(71), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_artifact_review_decisions_decision",
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "decision_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_artifact_review_decisions_digest_formats",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_review_task_id", "target_content_digest", "responsible_organization_id"],
            [
                f"{SCHEMA}.artifact_review_tasks.id",
                f"{SCHEMA}.artifact_review_tasks.target_content_digest",
                f"{SCHEMA}.artifact_review_tasks.responsible_organization_id",
            ],
            name="fk_artifact_review_decisions_task_scope", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_organization_id", "decided_by_user_id"],
            [f"{SCHEMA}.organization_members.organization_id", f"{SCHEMA}.organization_members.user_id"],
            name="fk_artifact_review_decisions_decider_member", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_review_decisions"),
        sa.UniqueConstraint("artifact_review_task_id", name="uq_artifact_review_decisions_task"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_review_decisions_decided_at",
        "artifact_review_decisions",
        [sa.text("decided_at DESC")],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_phase4_immutable_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('UPDATE','DELETE') THEN
                RAISE EXCEPTION 'Phase 4 evidence is immutable' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_contract_readiness_immutable
        BEFORE UPDATE OR DELETE ON medtrust.contract_readiness_confirmations
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_phase4_immutable_evidence()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_artifact_review_decisions_immutable
        BEFORE UPDATE OR DELETE ON medtrust.artifact_review_decisions
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_phase4_immutable_evidence()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_artifact_review_decisions_immutable ON medtrust.artifact_review_decisions")
    op.execute("DROP TRIGGER IF EXISTS trg_contract_readiness_immutable ON medtrust.contract_readiness_confirmations")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_phase4_immutable_evidence()")
    op.drop_table("artifact_review_decisions", schema=SCHEMA)
    op.drop_table("artifact_review_tasks", schema=SCHEMA)
    op.drop_table("contract_readiness_confirmations", schema=SCHEMA)
