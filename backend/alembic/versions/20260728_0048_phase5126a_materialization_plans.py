"""Phase 5.12.6A controlled materialization plans.

Revision ID: 20260728_0048
Revises: 20260727_0047
"""

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260728_0048"
down_revision = "20260727_0047"
branch_labels = None
depends_on = None
SCHEMA = "medtrust"
EVENT_RESULTS = {
    "asset_materialization.plan.created": "success",
    "asset_materialization.plan.submitted": "success",
    "asset_materialization.plan.approved": "success",
    "asset_materialization.plan.rejected": "denied",
    "asset_materialization.plan.cancelled": "success",
    "asset_materialization.plan.superseded": "success",
}


def _constraint_values(name: str) -> list[str]:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname='medtrust' AND t.relname='audit_events' "
            "AND c.conname=:name"
        ),
        {"name": name},
    ).scalar_one()
    return list(dict.fromkeys(re.findall(r"'([^']+)'", definition)))


def _replace_values(name: str, column: str, values: list[str]) -> None:
    rendered = ",".join(repr(value) for value in values)
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        f"ALTER TABLE medtrust.audit_events ADD CONSTRAINT {name} "
        f"CHECK ({column} IN ({rendered}))"
    )


def _change_audit(enable: bool) -> None:
    event_name = "ck_audit_events_ck_audit_events_event_type"
    subject_name = "ck_audit_events_ck_audit_events_subject_type"
    events = _constraint_values(event_name)
    subjects = _constraint_values(subject_name)
    if enable:
        events.extend(value for value in EVENT_RESULTS if value not in events)
        if "asset_materialization_plan" not in subjects:
            subjects.append("asset_materialization_plan")
    else:
        events = [value for value in events if value not in EVENT_RESULTS]
        subjects = [value for value in subjects if value != "asset_materialization_plan"]
    _replace_values(event_name, "event_type", events)
    _replace_values(subject_name, "subject_type", subjects)
    guard = op.get_bind().execute(
        sa.text(
            "SELECT pg_get_functiondef("
            "'medtrust.guard_audit_event_v8()'::regprocedure)"
        )
    ).scalar_one()
    cases = "".join(
        f"""
                WHEN '{event}' THEN
                    IF NEW.subject_type<>'asset_materialization_plan'
                      OR NEW.result<>'{result}' THEN
                      RAISE EXCEPTION 'invalid materialization plan event shape'
                        USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(
                      SELECT 1 FROM medtrust.asset_materialization_plans p
                      WHERE p.id=NEW.subject_id AND p.space_id=NEW.space_id
                    ) INTO v_subject_ok;
"""
        for event, result in EVENT_RESULTS.items()
    )
    marker = "                WHEN 'contract.revision.proposed' THEN"
    op.execute(
        guard.replace(marker, cases + marker, 1)
        if enable
        else guard.replace(cases, "", 1)
    )


def upgrade() -> None:
    op.create_table(
        "asset_materialization_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("relation_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("model_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("relation_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("plan_status", sa.String(16), nullable=False),
        sa.Column("data_plan", postgresql.JSONB(), nullable=False),
        sa.Column("model_plan", postgresql.JSONB(), nullable=False),
        sa.Column("transformation_plan", postgresql.JSONB(), nullable=False),
        sa.Column("execution_goal", postgresql.JSONB(), nullable=False),
        sa.Column("data_estimated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("model_estimated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("derived_estimated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_estimated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("hardware_requirements", postgresql.JSONB(), nullable=False),
        sa.Column("network_allowlist", postgresql.JSONB(), nullable=False),
        sa.Column("asset_file_allowlist", postgresql.JSONB(), nullable=False),
        sa.Column("license_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("access_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("security_preflight", postgresql.JSONB(), nullable=False),
        sa.Column("blocking_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("data_version_digest", sa.String(71), nullable=False),
        sa.Column("model_version_digest", sa.String(71), nullable=False),
        sa.Column("data_source_digest", sa.String(64), nullable=False),
        sa.Column("model_source_digest", sa.String(64), nullable=False),
        sa.Column("data_governance_digest", sa.String(71), nullable=False),
        sa.Column("model_governance_digest", sa.String(71), nullable=False),
        sa.Column("relation_evidence_digest", sa.String(71), nullable=False),
        sa.Column("plan_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("create_idempotency_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("submit_idempotency_digest", sa.String(71), unique=True),
        sa.Column("decision_idempotency_digest", sa.String(71), unique=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("creator_organization_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by", sa.Uuid()),
        sa.Column("approved_by", sa.Uuid()),
        sa.Column("approver_organization_id", sa.Uuid()),
        sa.Column("rejection_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("supersedes_plan_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["space_id"], [f"{SCHEMA}.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["relation_id"], [f"{SCHEMA}.dataset_model_relations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["data_product_version_id"], [f"{SCHEMA}.data_product_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_product_version_id"], [f"{SCHEMA}.model_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["relation_evidence_id"], [f"{SCHEMA}.dataset_model_evidence.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["creator_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submitted_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approver_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_plan_id"], [f"{SCHEMA}.asset_materialization_plans.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "plan_status IN ('draft','submitted','approved','rejected','expired','superseded','cancelled')",
            name="ck_asset_materialization_plans_status",
        ),
        sa.CheckConstraint(
            "data_estimated_bytes>=0 AND model_estimated_bytes>=0 AND "
            "derived_estimated_bytes>=0 AND total_estimated_bytes="
            "data_estimated_bytes+model_estimated_bytes+derived_estimated_bytes",
            name="ck_asset_materialization_plans_byte_budget",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_asset_materialization_relation",
        "asset_materialization_plans",
        ["relation_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_asset_materialization_status",
        "asset_materialization_plans",
        ["space_id", "plan_status", "created_at"],
        schema=SCHEMA,
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_asset_materialization_plan_v1()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' AND OLD.plan_status IN ('approved','rejected') THEN
            RAISE EXCEPTION 'terminal materialization plans cannot be deleted'
              USING ERRCODE='55000';
          END IF;
          IF TG_OP='UPDATE' AND OLD.plan_status='approved'
            AND NEW.plan_status<>'superseded' THEN
            RAISE EXCEPTION 'approved materialization plans are immutable'
              USING ERRCODE='55000';
          END IF;
          IF TG_OP='UPDATE' AND (
            NEW.space_id IS DISTINCT FROM OLD.space_id OR
            NEW.relation_id IS DISTINCT FROM OLD.relation_id OR
            NEW.data_product_version_id IS DISTINCT FROM OLD.data_product_version_id OR
            NEW.model_product_version_id IS DISTINCT FROM OLD.model_product_version_id OR
            NEW.relation_evidence_id IS DISTINCT FROM OLD.relation_evidence_id OR
            NEW.data_plan IS DISTINCT FROM OLD.data_plan OR
            NEW.model_plan IS DISTINCT FROM OLD.model_plan OR
            NEW.transformation_plan IS DISTINCT FROM OLD.transformation_plan OR
            NEW.execution_goal IS DISTINCT FROM OLD.execution_goal OR
            NEW.data_estimated_bytes IS DISTINCT FROM OLD.data_estimated_bytes OR
            NEW.model_estimated_bytes IS DISTINCT FROM OLD.model_estimated_bytes OR
            NEW.derived_estimated_bytes IS DISTINCT FROM OLD.derived_estimated_bytes OR
            NEW.total_estimated_bytes IS DISTINCT FROM OLD.total_estimated_bytes OR
            NEW.hardware_requirements IS DISTINCT FROM OLD.hardware_requirements OR
            NEW.network_allowlist IS DISTINCT FROM OLD.network_allowlist OR
            NEW.asset_file_allowlist IS DISTINCT FROM OLD.asset_file_allowlist OR
            NEW.license_snapshot IS DISTINCT FROM OLD.license_snapshot OR
            NEW.access_snapshot IS DISTINCT FROM OLD.access_snapshot OR
            NEW.security_preflight IS DISTINCT FROM OLD.security_preflight OR
            NEW.data_version_digest IS DISTINCT FROM OLD.data_version_digest OR
            NEW.model_version_digest IS DISTINCT FROM OLD.model_version_digest OR
            NEW.data_source_digest IS DISTINCT FROM OLD.data_source_digest OR
            NEW.model_source_digest IS DISTINCT FROM OLD.model_source_digest OR
            NEW.data_governance_digest IS DISTINCT FROM OLD.data_governance_digest OR
            NEW.model_governance_digest IS DISTINCT FROM OLD.model_governance_digest OR
            NEW.relation_evidence_digest IS DISTINCT FROM OLD.relation_evidence_digest OR
            NEW.plan_digest IS DISTINCT FROM OLD.plan_digest OR
            NEW.created_by IS DISTINCT FROM OLD.created_by OR
            NEW.creator_organization_id IS DISTINCT FROM OLD.creator_organization_id OR
            NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'materialization plan locks are immutable'
              USING ERRCODE='55000';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_asset_materialization_plan_guard
        BEFORE UPDATE OR DELETE ON medtrust.asset_materialization_plans
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_asset_materialization_plan_v1();
        """
    )
    _change_audit(True)


def downgrade() -> None:
    _change_audit(False)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_asset_materialization_plan_guard "
        "ON medtrust.asset_materialization_plans"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_asset_materialization_plan_v1()")
    op.drop_index("ix_asset_materialization_status", table_name="asset_materialization_plans", schema=SCHEMA)
    op.drop_index("ix_asset_materialization_relation", table_name="asset_materialization_plans", schema=SCHEMA)
    op.drop_table("asset_materialization_plans", schema=SCHEMA)
