"""Create the Phase 2-B.4-B Review task and decision tables.

Revision ID: 20260722_0007
Revises: 20260722_0006
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0007"
down_revision: str | None = "20260722_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_applications_id_space",
        "applications",
        ["id", "space_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("review_type", sa.String(length=32), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("application_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_digest", sa.Text(), nullable=False),
        sa.Column("assignee_organization_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "task_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("sequence_no", sa.SmallInteger(), nullable=False),
        sa.Column(
            "is_required",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("routing_rule_digest", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "review_type IN ('application_precheck', 'provider_review', "
            "'compliance_review', 'ethics_review')",
            name="review_type",
        ),
        sa.CheckConstraint(
            "task_status IN ('pending', 'claimed', 'decided', 'cancelled')",
            name="task_status",
        ),
        sa.CheckConstraint(
            "cancel_reason IS NULL OR cancel_reason IN "
            "('application_withdrawn', 'upstream_rejected', "
            "'administrative_termination')",
            name="cancel_reason",
        ),
        sa.CheckConstraint("sequence_no > 0", name="sequence_positive"),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.CheckConstraint(
            "target_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="target_digest_shape",
        ),
        sa.CheckConstraint(
            "routing_rule_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="routing_digest_shape",
        ),
        sa.CheckConstraint(
            "due_at IS NULL OR due_at > created_at", name="due_after_created"
        ),
        sa.CheckConstraint(
            "(task_status = 'pending' AND assignee_user_id IS NULL "
            "AND claimed_at IS NULL AND decided_at IS NULL "
            "AND cancelled_at IS NULL AND cancel_reason IS NULL) OR "
            "(task_status = 'claimed' AND assignee_user_id IS NOT NULL "
            "AND claimed_at IS NOT NULL AND decided_at IS NULL "
            "AND cancelled_at IS NULL AND cancel_reason IS NULL) OR "
            "(task_status = 'decided' AND assignee_user_id IS NOT NULL "
            "AND claimed_at IS NOT NULL AND decided_at IS NOT NULL "
            "AND cancelled_at IS NULL AND cancel_reason IS NULL) OR "
            "(task_status = 'cancelled' AND decided_at IS NULL "
            "AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
            "AND ((assignee_user_id IS NULL AND claimed_at IS NULL) OR "
            "(assignee_user_id IS NOT NULL AND claimed_at IS NOT NULL)))",
            name="lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "space_id"],
            [f"{SCHEMA}.applications.id", f"{SCHEMA}.applications.space_id"],
            name="fk_review_tasks_application_space",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "application_snapshot_id", "target_digest"],
            [
                f"{SCHEMA}.application_snapshots.application_id",
                f"{SCHEMA}.application_snapshots.id",
                f"{SCHEMA}.application_snapshots.snapshot_digest",
            ],
            name="fk_review_tasks_snapshot_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "assignee_organization_id"],
            [
                f"{SCHEMA}.space_participants.space_id",
                f"{SCHEMA}.space_participants.organization_id",
            ],
            name="fk_review_tasks_assignee_participant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_organization_id", "assignee_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_review_tasks_assignee_member",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_review_tasks_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_tasks"),
        sa.UniqueConstraint(
            "application_snapshot_id",
            "review_type",
            name="uq_review_tasks_snapshot_type",
        ),
        sa.UniqueConstraint(
            "id", "target_digest", name="uq_review_tasks_id_digest"
        ),
        sa.UniqueConstraint(
            "id", "assignee_organization_id", name="uq_review_tasks_id_org"
        ),
        sa.UniqueConstraint(
            "id", "assignee_user_id", name="uq_review_tasks_id_user"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_tasks_space_status_sequence",
        "review_tasks",
        ["space_id", "task_status", "sequence_no"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_tasks_assignee_status",
        "review_tasks",
        ["assignee_organization_id", "task_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_tasks_application",
        "review_tasks",
        ["application_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_tasks_snapshot",
        "review_tasks",
        ["application_snapshot_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_tasks_routing_digest",
        "review_tasks",
        ["routing_rule_digest"],
        schema=SCHEMA,
    )

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_task_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("remediation", sa.String(length=32), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("decided_for_organization_id", sa.Uuid(), nullable=False),
        sa.Column("target_digest", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("decision_digest", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="decision"
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('incomplete_materials', 'missing_ethics_material', "
            "'subject_not_eligible', 'policy_conflict', 'purpose_not_justified', "
            "'compliance_requirement_not_met', 'ethics_requirement_not_met', "
            "'conflict_of_interest', 'other')",
            name="reason_code",
        ),
        sa.CheckConstraint(
            "remediation IS NULL OR remediation = 'clone_and_resubmit'",
            name="remediation",
        ),
        sa.CheckConstraint(
            "(decision = 'approved' AND reason_code IS NULL "
            "AND remediation IS NULL) OR "
            "(decision = 'rejected' AND reason_code IS NOT NULL)",
            name="decision_shape",
        ),
        sa.CheckConstraint(
            "target_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="target_digest_shape",
        ),
        sa.CheckConstraint(
            "decision_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="decision_digest_shape",
        ),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'object'", name="evidence_object"),
        sa.ForeignKeyConstraint(
            ["review_task_id", "target_digest"],
            [f"{SCHEMA}.review_tasks.id", f"{SCHEMA}.review_tasks.target_digest"],
            name="fk_review_decisions_task_digest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id", "decided_for_organization_id"],
            [
                f"{SCHEMA}.review_tasks.id",
                f"{SCHEMA}.review_tasks.assignee_organization_id",
            ],
            name="fk_review_decisions_task_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id", "decided_by_user_id"],
            [
                f"{SCHEMA}.review_tasks.id",
                f"{SCHEMA}.review_tasks.assignee_user_id",
            ],
            name="fk_review_decisions_task_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_decisions"),
        sa.UniqueConstraint("review_task_id", name="uq_review_decisions_task"),
        sa.UniqueConstraint("decision_digest", name="uq_review_decisions_digest"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_decisions_decided_by",
        "review_decisions",
        ["decided_by_user_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_decisions_decided_for",
        "review_decisions",
        ["decided_for_organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_decisions_decided_at",
        "review_decisions",
        ["decided_at"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_review_task_lifecycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            app_status text;
            applicant_org uuid;
            provider_org uuid;
            operator_org uuid;
            participant_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'review task cannot be deleted';
            END IF;

            IF TG_OP = 'INSERT' THEN
                IF NEW.task_status <> 'pending' THEN
                    RAISE EXCEPTION 'new review task must start pending';
                END IF;

                SELECT a.status, a.applicant_organization_id,
                       a.provider_organization_id, s.operator_organization_id
                  INTO app_status, applicant_org, provider_org, operator_org
                  FROM medtrust.applications a
                  JOIN medtrust.spaces s ON s.id = a.space_id
                 WHERE a.id = NEW.application_id AND a.space_id = NEW.space_id;
                IF app_status IS NULL OR app_status NOT IN
                    ('submitted', 'prechecking', 'provider_review') THEN
                    RAISE EXCEPTION 'review task requires a submitted reviewable application';
                END IF;
                IF NEW.assignee_organization_id = applicant_org THEN
                    RAISE EXCEPTION 'applicant organization cannot review itself';
                END IF;
                IF NEW.review_type = 'application_precheck' AND
                   (NEW.assignee_organization_id <> operator_org OR NEW.sequence_no <> 10) THEN
                    RAISE EXCEPTION 'application precheck must route to operator at sequence 10';
                END IF;
                IF NEW.review_type = 'provider_review' AND
                   (NEW.assignee_organization_id <> provider_org OR NEW.sequence_no <> 20) THEN
                    RAISE EXCEPTION 'provider review must route to provider at sequence 20';
                END IF;
                IF NEW.review_type IN ('compliance_review', 'ethics_review') AND
                   NEW.sequence_no <> 20 THEN
                    RAISE EXCEPTION 'conditional review must use sequence 20';
                END IF;
                SELECT admission_status INTO participant_status
                  FROM medtrust.space_participants
                 WHERE space_id = NEW.space_id
                   AND organization_id = NEW.assignee_organization_id;
                IF participant_status IS DISTINCT FROM 'admitted' THEN
                    RAISE EXCEPTION 'review organization must be an admitted participant';
                END IF;
                RETURN NEW;
            END IF;

            IF ROW(NEW.space_id, NEW.review_type, NEW.application_id,
                   NEW.application_snapshot_id, NEW.target_digest,
                   NEW.assignee_organization_id, NEW.sequence_no,
                   NEW.is_required, NEW.routing_rule_digest,
                   NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.space_id, OLD.review_type, OLD.application_id,
                   OLD.application_snapshot_id, OLD.target_digest,
                   OLD.assignee_organization_id, OLD.sequence_no,
                   OLD.is_required, OLD.routing_rule_digest,
                   OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'review task target and routing fields are immutable';
            END IF;
            IF OLD.task_status = 'pending' AND NEW.task_status NOT IN ('claimed', 'cancelled') THEN
                RAISE EXCEPTION 'invalid pending review task transition';
            ELSIF OLD.task_status = 'claimed' AND NEW.task_status NOT IN
                  ('pending', 'decided', 'cancelled') THEN
                RAISE EXCEPTION 'invalid claimed review task transition';
            ELSIF OLD.task_status IN ('decided', 'cancelled') THEN
                RAISE EXCEPTION 'terminal review task is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_review_task_lifecycle "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.review_tasks "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_review_task_lifecycle()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_review_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            current_status text;
            current_assignee_user uuid;
            current_assignee_org uuid;
            current_digest text;
            current_claimed_at timestamptz;
            member_status text;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                RAISE EXCEPTION 'review decision is append-only';
            END IF;

            SELECT task_status, assignee_user_id, assignee_organization_id,
                   target_digest, claimed_at
              INTO current_status, current_assignee_user, current_assignee_org,
                   current_digest, current_claimed_at
              FROM medtrust.review_tasks
             WHERE id = NEW.review_task_id;
            IF current_status IS DISTINCT FROM 'claimed' THEN
                RAISE EXCEPTION 'review decision requires a claimed task';
            END IF;
            IF NEW.decided_by_user_id IS DISTINCT FROM current_assignee_user OR
               NEW.decided_for_organization_id IS DISTINCT FROM current_assignee_org OR
               NEW.target_digest IS DISTINCT FROM current_digest THEN
                RAISE EXCEPTION 'review decision does not match task assignment';
            END IF;
            IF NEW.decided_at < current_claimed_at THEN
                RAISE EXCEPTION 'review decision predates task claim';
            END IF;
            SELECT status INTO member_status
              FROM medtrust.organization_members
             WHERE organization_id = current_assignee_org
               AND user_id = current_assignee_user;
            IF member_status IS DISTINCT FROM 'active' THEN
                RAISE EXCEPTION 'reviewer must be an active organization member';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_review_decision_append_only "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.review_decisions "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_review_decision()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.require_review_decision_consistency()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            task_id uuid;
            current_status text;
            decision_count integer;
        BEGIN
            IF TG_TABLE_NAME = 'review_tasks' THEN
                task_id := COALESCE(NEW.id, OLD.id);
            ELSE
                task_id := COALESCE(NEW.review_task_id, OLD.review_task_id);
            END IF;
            SELECT task_status INTO current_status
              FROM medtrust.review_tasks WHERE id = task_id;
            IF current_status IS NULL THEN
                RETURN NULL;
            END IF;
            SELECT count(*) INTO decision_count
              FROM medtrust.review_decisions WHERE review_task_id = task_id;
            IF (current_status = 'decided' AND decision_count <> 1) OR
               (current_status <> 'decided' AND decision_count <> 0) THEN
                RAISE EXCEPTION 'review task and decision terminal state are inconsistent';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_review_task_decision_consistency "
        "AFTER INSERT OR UPDATE OR DELETE ON medtrust.review_tasks "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION "
        "medtrust.require_review_decision_consistency()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_review_decision_task_consistency "
        "AFTER INSERT OR UPDATE OR DELETE ON medtrust.review_decisions "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION "
        "medtrust.require_review_decision_consistency()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_review_decision_task_consistency "
        "ON medtrust.review_decisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_review_task_decision_consistency "
        "ON medtrust.review_tasks"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_review_decision_append_only "
        "ON medtrust.review_decisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_review_task_lifecycle ON medtrust.review_tasks"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.require_review_decision_consistency()"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_review_decision()")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_review_task_lifecycle()")

    op.drop_table("review_decisions", schema=SCHEMA)
    op.drop_table("review_tasks", schema=SCHEMA)
    op.drop_constraint(
        "uq_applications_id_space",
        "applications",
        schema=SCHEMA,
        type_="unique",
    )
