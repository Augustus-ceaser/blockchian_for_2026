"""Create ComputeJob/ComputeRun metadata and fail-closed reservation guards.

Revision ID: 20260722_0011
Revises: 20260722_0010
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0011"
down_revision: str | None = "20260722_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    _create_compute_jobs()
    _create_compute_runs()
    _create_audit_gate()
    _create_compute_job_guard()
    _create_compute_run_guard()


def _create_compute_jobs() -> None:
    op.create_table(
        "compute_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_content_digest", sa.Text(), nullable=False),
        sa.Column("requester_contract_party_id", sa.Uuid(), nullable=False),
        sa.Column("requester_organization_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("contract_object_id", sa.Uuid(), nullable=False),
        sa.Column("purpose_code", sa.String(length=40), nullable=False),
        sa.Column("requested_output_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("algorithm_spec_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("algorithm_spec_digest", sa.Text(), nullable=False),
        sa.Column("compute_input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("compute_input_digest", sa.Text(), nullable=False),
        sa.Column("creation_authorization_evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("creation_authorization_evaluation_digest", sa.Text(), nullable=False),
        sa.Column("creation_request_digest", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="created", nullable=False),
        sa.Column("denial_code", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("interruption_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('created','validating','ready','running','stopping','succeeded','denied','failed','interrupted','cancelled')",
            name="status",
        ),
        sa.CheckConstraint("jsonb_typeof(requested_output_types) = 'array' AND jsonb_array_length(requested_output_types) > 0", name="requested_outputs_array"),
        sa.CheckConstraint("jsonb_typeof(algorithm_spec_snapshot) = 'object'", name="algorithm_snapshot_object"),
        sa.CheckConstraint("jsonb_typeof(compute_input_snapshot) = 'object'", name="input_snapshot_object"),
        sa.CheckConstraint("jsonb_typeof(creation_authorization_evaluation) = 'object'", name="authorization_evaluation_object"),
        sa.CheckConstraint("revision_content_digest ~ '^sha256:[0-9a-f]{64}$'", name="revision_digest_format"),
        sa.CheckConstraint("algorithm_spec_digest ~ '^sha256:[0-9a-f]{64}$'", name="algorithm_spec_digest_format"),
        sa.CheckConstraint("compute_input_digest ~ '^sha256:[0-9a-f]{64}$'", name="compute_input_digest_format"),
        sa.CheckConstraint("creation_authorization_evaluation_digest ~ '^sha256:[0-9a-f]{64}$'", name="authorization_digest_format"),
        sa.CheckConstraint("creation_request_digest ~ '^sha256:[0-9a-f]{64}$'", name="request_digest_format"),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.ForeignKeyConstraint(["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["contract_id", "space_id"],
            ["medtrust.contracts.id", "medtrust.contracts.space_id"],
            name="fk_compute_jobs_contract_space",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "contract_id"],
            ["medtrust.contract_revisions.id", "medtrust.contract_revisions.contract_id"],
            name="fk_compute_jobs_revision_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "revision_content_digest"],
            ["medtrust.contract_revisions.id", "medtrust.contract_revisions.content_digest"],
            name="fk_compute_jobs_revision_digest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "requester_contract_party_id", "requester_organization_id"],
            [
                "medtrust.contract_parties.contract_revision_id",
                "medtrust.contract_parties.id",
                "medtrust.contract_parties.organization_id",
            ],
            name="fk_compute_jobs_requester_party_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "contract_object_id"],
            ["medtrust.contract_objects.contract_revision_id", "medtrust.contract_objects.id"],
            name="fk_compute_jobs_revision_object",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requester_organization_id", "requester_user_id"],
            ["medtrust.organization_members.organization_id", "medtrust.organization_members.user_id"],
            name="fk_compute_jobs_requester_member",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["medtrust.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_compute_jobs"),
        sa.UniqueConstraint(
            "id", "space_id", "contract_id", "contract_revision_id",
            "requester_contract_party_id", "contract_object_id",
            name="uq_compute_jobs_run_scope",
        ),
        sa.UniqueConstraint("creation_request_digest", name="uq_compute_jobs_creation_request_digest"),
        schema=SCHEMA,
    )
    op.create_index("ix_compute_jobs_space_status_created", "compute_jobs", ["space_id", "status", sa.text("created_at DESC")], schema=SCHEMA)
    op.create_index("ix_compute_jobs_revision_status", "compute_jobs", ["contract_revision_id", "status"], schema=SCHEMA)
    op.create_index("ix_compute_jobs_requester_created", "compute_jobs", ["requester_organization_id", sa.text("created_at DESC")], schema=SCHEMA)
    op.create_index("ix_compute_jobs_object_created", "compute_jobs", ["contract_object_id", sa.text("created_at DESC")], schema=SCHEMA)


def _create_compute_runs() -> None:
    op.create_table(
        "compute_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("compute_job_id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("requester_contract_party_id", sa.Uuid(), nullable=False),
        sa.Column("contract_object_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="prepared", nullable=False),
        sa.Column("quota_policy_id", sa.Uuid(), nullable=True),
        sa.Column("run_count_constraint_id", sa.Uuid(), nullable=True),
        sa.Column("run_limit_snapshot", sa.Integer(), nullable=True),
        sa.Column("reservation_ordinal", sa.Integer(), nullable=True),
        sa.Column("quota_scope_digest", sa.Text(), nullable=True),
        sa.Column("quota_reservation_digest", sa.Text(), nullable=True),
        sa.Column("quota_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_authorization_evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("start_authorization_evaluation_digest", sa.Text(), nullable=True),
        sa.Column("compute_binding_id", sa.Uuid(), nullable=True),
        sa.Column("egress_binding_id", sa.Uuid(), nullable=True),
        sa.Column("audit_binding_id", sa.Uuid(), nullable=True),
        sa.Column("execution_environment_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("execution_environment_digest", sa.Text(), nullable=True),
        sa.Column("execution_reference", sa.Text(), nullable=True),
        sa.Column("dispatch_receipt_digest", sa.Text(), nullable=True),
        sa.Column("start_receipt_digest", sa.Text(), nullable=True),
        sa.Column("completion_receipt_digest", sa.Text(), nullable=True),
        sa.Column("audit_receipt_digest", sa.Text(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("interruption_code", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('prepared','reserved','dispatched','running','succeeded','failed','interrupted','cancelled','timed_out')",
            name="status",
        ),
        sa.CheckConstraint("attempt_no > 0", name="attempt_no_positive"),
        sa.CheckConstraint("run_limit_snapshot IS NULL OR run_limit_snapshot > 0", name="run_limit_positive"),
        sa.CheckConstraint("reservation_ordinal IS NULL OR reservation_ordinal > 0", name="reservation_ordinal_positive"),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.ForeignKeyConstraint(
            ["compute_job_id", "space_id", "contract_id", "contract_revision_id", "requester_contract_party_id", "contract_object_id"],
            [
                "medtrust.compute_jobs.id", "medtrust.compute_jobs.space_id",
                "medtrust.compute_jobs.contract_id", "medtrust.compute_jobs.contract_revision_id",
                "medtrust.compute_jobs.requester_contract_party_id", "medtrust.compute_jobs.contract_object_id",
            ],
            name="fk_compute_runs_job_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "quota_policy_id"],
            ["medtrust.policies.contract_revision_id", "medtrust.policies.id"],
            name="fk_compute_runs_quota_policy_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["run_count_constraint_id"], ["medtrust.policy_constraints.id"], name="fk_compute_runs_run_count_constraint", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["compute_binding_id"], ["medtrust.policy_execution_bindings.id"], name="fk_compute_runs_compute_binding", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["egress_binding_id"], ["medtrust.policy_execution_bindings.id"], name="fk_compute_runs_egress_binding", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["audit_binding_id"], ["medtrust.policy_execution_bindings.id"], name="fk_compute_runs_audit_binding", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["medtrust.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_compute_runs"),
        sa.UniqueConstraint("compute_job_id", "attempt_no", name="uq_compute_runs_job_attempt"),
        sa.UniqueConstraint("id", "compute_job_id", "space_id", name="uq_compute_runs_artifact_scope"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_compute_runs_quota_ordinal",
        "compute_runs",
        ["contract_revision_id", "quota_policy_id", "requester_contract_party_id", "contract_object_id", "reservation_ordinal"],
        unique=True,
        postgresql_where=sa.text("reservation_ordinal IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_compute_runs_job_nonterminal",
        "compute_runs",
        ["compute_job_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('prepared','reserved','dispatched','running')"),
        schema=SCHEMA,
    )
    op.create_index("ix_compute_runs_job_attempt_desc", "compute_runs", ["compute_job_id", sa.text("attempt_no DESC")], schema=SCHEMA)
    op.create_index("ix_compute_runs_space_status_prepared", "compute_runs", ["space_id", "status", sa.text("prepared_at DESC")], schema=SCHEMA)
    op.create_index("ix_compute_runs_revision_status", "compute_runs", ["contract_revision_id", "status"], schema=SCHEMA)
    op.create_index("ix_compute_runs_quota_ordinal_desc", "compute_runs", ["quota_policy_id", sa.text("reservation_ordinal DESC")], schema=SCHEMA)
    op.create_index("ix_compute_runs_compute_binding", "compute_runs", ["compute_binding_id"], schema=SCHEMA)
    op.create_index("ix_compute_runs_egress_binding", "compute_runs", ["egress_binding_id"], schema=SCHEMA)
    op.create_index("ix_compute_runs_audit_binding", "compute_runs", ["audit_binding_id"], schema=SCHEMA)


def _create_audit_gate() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.assert_compute_audit_ready_v7()
        RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'AuditEvidenceUnavailable';
        END;
        $$;
        """
    )


def _create_compute_job_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_compute_job_v7()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE old_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'ComputeJob cannot be deleted';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'created' THEN
                    RAISE EXCEPTION 'new ComputeJob must start as created';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                      FROM medtrust.contract_revisions r
                      JOIN medtrust.contracts c ON c.id=r.contract_id
                      JOIN medtrust.contract_parties cp
                        ON cp.contract_revision_id=r.id
                       AND cp.id=NEW.requester_contract_party_id
                       AND cp.organization_id=NEW.requester_organization_id
                      JOIN medtrust.contract_objects co
                        ON co.contract_revision_id=r.id
                       AND co.id=NEW.contract_object_id
                      JOIN medtrust.organization_members om
                        ON om.organization_id=NEW.requester_organization_id
                       AND om.user_id=NEW.requester_user_id
                      JOIN medtrust.users u ON u.id=om.user_id
                      JOIN medtrust.organizations o ON o.id=om.organization_id
                      JOIN medtrust.spaces s ON s.id=c.space_id
                      JOIN medtrust.space_participants sp
                        ON sp.space_id=c.space_id AND sp.organization_id=om.organization_id
                      JOIN medtrust.data_product_versions v ON v.id=co.data_product_version_id
                      JOIN medtrust.data_products p ON p.id=v.data_product_id
                     WHERE r.id=NEW.contract_revision_id
                       AND r.contract_id=NEW.contract_id
                       AND c.space_id=NEW.space_id
                       AND r.content_digest=NEW.revision_content_digest
                       AND r.status='active'
                       AND (r.effective_from IS NULL OR r.effective_from <= clock_timestamp())
                       AND (r.effective_until IS NULL OR r.effective_until > clock_timestamp())
                       AND cp.party_role='consumer'
                       AND om.status='active'
                       AND (om.valid_from IS NULL OR om.valid_from <= clock_timestamp())
                       AND (om.valid_until IS NULL OR om.valid_until > clock_timestamp())
                       AND u.status='active' AND o.status='active'
                       AND s.status='active' AND sp.admission_status='admitted'
                       AND v.status='approved' AND p.lifecycle_status='active'
                       AND v.snapshot_digest=co.product_snapshot_digest
                ) THEN
                    RAISE EXCEPTION 'ComputeJob current authorization scope is unavailable';
                END IF;
                IF NEW.creation_authorization_evaluation->>'decision' <> 'permit' OR
                   NEW.creation_authorization_evaluation->>'contract_revision_id' <> NEW.contract_revision_id::text OR
                   NEW.creation_authorization_evaluation->>'requester_contract_party_id' <> NEW.requester_contract_party_id::text OR
                   NEW.creation_authorization_evaluation->>'requester_organization_id' <> NEW.requester_organization_id::text OR
                   NEW.creation_authorization_evaluation->>'requester_user_id' <> NEW.requester_user_id::text OR
                   NEW.creation_authorization_evaluation->>'contract_object_id' <> NEW.contract_object_id::text THEN
                    RAISE EXCEPTION 'ComputeJob authorization evidence does not match scope';
                END IF;
                RETURN NEW;
            END IF;

            IF ROW(NEW.space_id, NEW.contract_id, NEW.contract_revision_id,
                   NEW.revision_content_digest, NEW.requester_contract_party_id,
                   NEW.requester_organization_id, NEW.requester_user_id,
                   NEW.contract_object_id, NEW.purpose_code, NEW.requested_output_types,
                   NEW.algorithm_spec_snapshot, NEW.algorithm_spec_digest,
                   NEW.compute_input_snapshot, NEW.compute_input_digest,
                   NEW.creation_authorization_evaluation,
                   NEW.creation_authorization_evaluation_digest,
                   NEW.creation_request_digest, NEW.created_at, NEW.created_by)
               IS DISTINCT FROM
               ROW(OLD.space_id, OLD.contract_id, OLD.contract_revision_id,
                   OLD.revision_content_digest, OLD.requester_contract_party_id,
                   OLD.requester_organization_id, OLD.requester_user_id,
                   OLD.contract_object_id, OLD.purpose_code, OLD.requested_output_types,
                   OLD.algorithm_spec_snapshot, OLD.algorithm_spec_digest,
                   OLD.compute_input_snapshot, OLD.compute_input_digest,
                   OLD.creation_authorization_evaluation,
                   OLD.creation_authorization_evaluation_digest,
                   OLD.creation_request_digest, OLD.created_at, OLD.created_by) THEN
                RAISE EXCEPTION 'ComputeJob intent and evidence are immutable';
            END IF;
            old_status := OLD.status;
            IF old_status IN ('succeeded','denied','failed','interrupted','cancelled') THEN
                RAISE EXCEPTION 'terminal ComputeJob is immutable';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1 THEN
                RAISE EXCEPTION 'ComputeJob transition requires row_version increment';
            END IF;
            IF NOT (
                (old_status='created' AND NEW.status IN ('validating','cancelled')) OR
                (old_status='validating' AND NEW.status IN ('ready','denied','failed')) OR
                (old_status='ready' AND NEW.status IN ('validating','running','cancelled')) OR
                (old_status='running' AND NEW.status IN ('stopping','succeeded','failed','interrupted')) OR
                (old_status='stopping' AND NEW.status IN ('cancelled','failed','interrupted'))
            ) THEN RAISE EXCEPTION 'illegal ComputeJob status transition'; END IF;
            IF NEW.status='running' AND NOT EXISTS (
                SELECT 1 FROM medtrust.compute_runs cr
                 WHERE cr.compute_job_id=NEW.id AND cr.status IN ('dispatched','running')
            ) THEN RAISE EXCEPTION 'running Job requires an executing Run'; END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_compute_job_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON medtrust.compute_jobs FOR EACH ROW EXECUTE FUNCTION medtrust.guard_compute_job_v7()"
    )


def _create_compute_run_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_compute_run_v7()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            job_row medtrust.compute_jobs%ROWTYPE;
            quota_row medtrust.policies%ROWTYPE;
            constraint_row medtrust.policy_constraints%ROWTYPE;
            parsed_limit integer;
            next_ordinal integer;
            binding_count integer;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'prepared' THEN
                    RAISE EXCEPTION 'new ComputeRun must start as prepared';
                END IF;
                SELECT * INTO job_row FROM medtrust.compute_jobs
                 WHERE id=NEW.compute_job_id FOR UPDATE;
                IF NOT FOUND OR job_row.status <> 'ready' THEN
                    RAISE EXCEPTION 'prepared Run requires a ready Job';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM medtrust.compute_runs
                     WHERE compute_job_id=NEW.compute_job_id
                       AND status IN ('prepared','reserved','dispatched','running')
                ) THEN RAISE EXCEPTION 'Job already has a nonterminal Run'; END IF;
                IF NEW.attempt_no <> COALESCE((
                    SELECT max(attempt_no)+1 FROM medtrust.compute_runs
                     WHERE compute_job_id=NEW.compute_job_id
                ), 1) THEN RAISE EXCEPTION 'Run attempt_no must be monotonic'; END IF;
                IF NEW.quota_policy_id IS NOT NULL OR NEW.run_count_constraint_id IS NOT NULL OR
                   NEW.run_limit_snapshot IS NOT NULL OR NEW.reservation_ordinal IS NOT NULL OR
                   NEW.quota_scope_digest IS NOT NULL OR NEW.quota_reservation_digest IS NOT NULL OR
                   NEW.quota_consumed_at IS NOT NULL OR NEW.start_authorization_evaluation IS NOT NULL OR
                   NEW.compute_binding_id IS NOT NULL OR NEW.egress_binding_id IS NOT NULL OR
                   NEW.audit_binding_id IS NOT NULL OR NEW.execution_environment_snapshot IS NOT NULL OR
                   NEW.reserved_at IS NOT NULL THEN
                    RAISE EXCEPTION 'prepared Run cannot contain reservation evidence';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'ComputeRun cannot be deleted';
            END IF;
            IF ROW(NEW.space_id, NEW.compute_job_id, NEW.contract_id,
                   NEW.contract_revision_id, NEW.requester_contract_party_id,
                   NEW.contract_object_id, NEW.attempt_no, NEW.prepared_at, NEW.created_by)
               IS DISTINCT FROM
               ROW(OLD.space_id, OLD.compute_job_id, OLD.contract_id,
                   OLD.contract_revision_id, OLD.requester_contract_party_id,
                   OLD.contract_object_id, OLD.attempt_no, OLD.prepared_at, OLD.created_by) THEN
                RAISE EXCEPTION 'ComputeRun attempt identity is immutable';
            END IF;
            IF OLD.status IN ('succeeded','failed','interrupted','cancelled','timed_out') THEN
                RAISE EXCEPTION 'terminal ComputeRun is immutable';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1 THEN
                RAISE EXCEPTION 'ComputeRun transition requires row_version increment';
            END IF;

            IF OLD.status='prepared' AND NEW.status='cancelled' THEN
                IF NEW.finished_at IS NULL THEN RAISE EXCEPTION 'cancelled Run requires finished_at'; END IF;
                RETURN NEW;
            END IF;

            IF OLD.status='prepared' AND NEW.status='reserved' THEN
                PERFORM medtrust.assert_compute_audit_ready_v7();
                IF NEW.reservation_ordinal IS NOT NULL OR NEW.quota_consumed_at IS NOT NULL OR NEW.reserved_at IS NOT NULL THEN
                    RAISE EXCEPTION 'reservation ordinal and timestamps are database assigned';
                END IF;
                IF NEW.quota_policy_id IS NULL OR NEW.run_count_constraint_id IS NULL OR
                   NEW.run_limit_snapshot IS NULL OR NEW.quota_scope_digest IS NULL OR
                   NEW.quota_reservation_digest IS NULL OR NEW.start_authorization_evaluation IS NULL OR
                   NEW.start_authorization_evaluation_digest IS NULL OR
                   NEW.compute_binding_id IS NULL OR NEW.egress_binding_id IS NULL OR
                   NEW.audit_binding_id IS NULL OR NEW.execution_environment_snapshot IS NULL OR
                   NEW.execution_environment_digest IS NULL THEN
                    RAISE EXCEPTION 'reserved Run requires complete authorization evidence';
                END IF;
                SELECT * INTO job_row FROM medtrust.compute_jobs
                 WHERE id=NEW.compute_job_id FOR UPDATE;
                IF NOT FOUND OR job_row.status <> 'ready' THEN
                    RAISE EXCEPTION 'Run reservation requires a ready Job';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM medtrust.contract_revisions r
                    JOIN medtrust.contracts c ON c.id=r.contract_id
                    JOIN medtrust.contract_objects co
                      ON co.contract_revision_id=r.id AND co.id=NEW.contract_object_id
                    JOIN medtrust.data_product_versions v ON v.id=co.data_product_version_id
                    JOIN medtrust.data_products dp ON dp.id=v.data_product_id
                    WHERE r.id=NEW.contract_revision_id AND r.status='active'
                      AND c.id=NEW.contract_id AND c.space_id=NEW.space_id
                      AND (r.effective_from IS NULL OR r.effective_from <= clock_timestamp())
                      AND (r.effective_until IS NULL OR r.effective_until > clock_timestamp())
                      AND v.status='approved' AND dp.lifecycle_status='active'
                      AND v.snapshot_digest=co.product_snapshot_digest
                ) THEN RAISE EXCEPTION 'Run current Contract scope is unavailable'; END IF;

                SELECT * INTO quota_row FROM medtrust.policies
                 WHERE id=NEW.quota_policy_id FOR UPDATE;
                IF NOT FOUND OR quota_row.contract_revision_id <> NEW.contract_revision_id OR
                   quota_row.subject_contract_party_id <> NEW.requester_contract_party_id OR
                   quota_row.contract_object_id <> NEW.contract_object_id OR
                   quota_row.policy_type <> 'permission' OR quota_row.effect <> 'permit' OR
                   quota_row.action_code <> 'execute_controlled_compute' OR quota_row.policy_digest IS NULL THEN
                    RAISE EXCEPTION 'invalid governing permit Policy';
                END IF;
                IF (SELECT count(*) FROM medtrust.policies p
                     WHERE p.contract_revision_id=NEW.contract_revision_id
                       AND p.subject_contract_party_id=NEW.requester_contract_party_id
                       AND p.contract_object_id=NEW.contract_object_id
                       AND p.policy_type='permission' AND p.effect='permit'
                       AND p.action_code='execute_controlled_compute') <> 1 THEN
                    RAISE EXCEPTION 'ambiguous_permit_policy';
                END IF;
                SELECT * INTO constraint_row FROM medtrust.policy_constraints
                 WHERE id=NEW.run_count_constraint_id;
                IF NOT FOUND OR constraint_row.policy_id <> quota_row.id OR
                   constraint_row.constraint_name <> 'run_count' OR
                   constraint_row.operator <> 'lte' OR constraint_row.unit <> 'count' OR
                   jsonb_typeof(constraint_row.value) <> 'number' THEN
                    RAISE EXCEPTION 'invalid run_count constraint';
                END IF;
                parsed_limit := (constraint_row.value #>> '{}')::integer;
                IF parsed_limit <= 0 OR NEW.run_limit_snapshot <> parsed_limit THEN
                    RAISE EXCEPTION 'run_count limit snapshot mismatch';
                END IF;
                IF NEW.start_authorization_evaluation->>'decision' <> 'permit' OR
                   NEW.start_authorization_evaluation->>'contract_revision_id' <> NEW.contract_revision_id::text OR
                   NEW.start_authorization_evaluation->>'quota_policy_id' <> NEW.quota_policy_id::text OR
                   NEW.start_authorization_evaluation->>'contract_object_id' <> NEW.contract_object_id::text THEN
                    RAISE EXCEPTION 'Run authorization evidence does not match scope';
                END IF;

                SELECT count(*) INTO binding_count
                  FROM (
                    SELECT b.id
                      FROM medtrust.policy_execution_bindings b
                      JOIN medtrust.policies p ON p.id=b.policy_id
                      JOIN medtrust.connectors cn ON cn.id=b.connector_id
                      JOIN medtrust.connector_capabilities cap
                        ON cap.connector_id=b.connector_id
                       AND cap.capability_code=b.required_capability_code
                       AND cap.capability_version=b.required_capability_version
                     WHERE b.id=NEW.compute_binding_id
                       AND p.id=NEW.quota_policy_id
                       AND p.contract_revision_id=NEW.contract_revision_id
                       AND b.execution_role='compute_executor'
                       AND b.required_capability_code='controlled_compute_execution'
                       AND b.required_capability_version='1.0'
                       AND b.deployment_status='accepted' AND b.receipt_digest IS NOT NULL
                       AND cn.space_id=NEW.space_id AND cn.verification_status='verified'
                       AND cn.runtime_status='online' AND cn.last_heartbeat_at >= clock_timestamp() - interval '5 minutes'
                       AND cap.status='verified' AND cap.verified_at IS NOT NULL
                    UNION ALL
                    SELECT b.id
                      FROM medtrust.policy_execution_bindings b
                      JOIN medtrust.policies p ON p.id=b.policy_id
                      JOIN medtrust.connectors cn ON cn.id=b.connector_id
                      JOIN medtrust.connector_capabilities cap
                        ON cap.connector_id=b.connector_id
                       AND cap.capability_code=b.required_capability_code
                       AND cap.capability_version=b.required_capability_version
                     WHERE b.id=NEW.egress_binding_id
                       AND p.contract_revision_id=NEW.contract_revision_id
                       AND p.subject_contract_party_id=NEW.requester_contract_party_id
                       AND p.contract_object_id=NEW.contract_object_id
                       AND p.policy_type='permission' AND p.effect='permit' AND p.action_code='export_artifact'
                       AND b.execution_role='egress_controller'
                       AND b.required_capability_code='egress_policy_enforcement'
                       AND b.required_capability_version='1.0'
                       AND b.deployment_status='accepted' AND b.receipt_digest IS NOT NULL
                       AND cn.space_id=NEW.space_id AND cn.verification_status='verified'
                       AND cn.runtime_status='online' AND cn.last_heartbeat_at >= clock_timestamp() - interval '5 minutes'
                       AND cap.status='verified' AND cap.verified_at IS NOT NULL
                    UNION ALL
                    SELECT b.id
                      FROM medtrust.policy_execution_bindings b
                      JOIN medtrust.policies p ON p.id=b.policy_id
                      JOIN medtrust.connectors cn ON cn.id=b.connector_id
                      JOIN medtrust.connector_capabilities cap
                        ON cap.connector_id=b.connector_id
                       AND cap.capability_code=b.required_capability_code
                       AND cap.capability_version=b.required_capability_version
                     WHERE b.id=NEW.audit_binding_id
                       AND p.contract_revision_id=NEW.contract_revision_id
                       AND p.subject_contract_party_id=NEW.requester_contract_party_id
                       AND p.contract_object_id=NEW.contract_object_id
                       AND p.policy_type='obligation' AND p.effect='require' AND p.action_code='write_audit_log'
                       AND b.execution_role='audit_evidence_emitter'
                       AND b.required_capability_code='audit_evidence_emit'
                       AND b.required_capability_version='1.0'
                       AND b.deployment_status='accepted' AND b.receipt_digest IS NOT NULL
                       AND cn.space_id=NEW.space_id AND cn.verification_status='verified'
                       AND cn.runtime_status='online' AND cn.last_heartbeat_at >= clock_timestamp() - interval '5 minutes'
                       AND cap.status='verified' AND cap.verified_at IS NOT NULL
                  ) valid_bindings;
                IF binding_count <> 3 THEN
                    RAISE EXCEPTION 'Run Binding or Connector Capability is inconsistent';
                END IF;

                SELECT COALESCE(max(reservation_ordinal), 0) + 1 INTO next_ordinal
                  FROM medtrust.compute_runs
                 WHERE contract_revision_id=NEW.contract_revision_id
                   AND quota_policy_id=NEW.quota_policy_id
                   AND requester_contract_party_id=NEW.requester_contract_party_id
                   AND contract_object_id=NEW.contract_object_id
                   AND reservation_ordinal IS NOT NULL;
                IF next_ordinal > parsed_limit THEN
                    RAISE EXCEPTION 'run_count quota is exhausted';
                END IF;
                NEW.reservation_ordinal := next_ordinal;
                NEW.quota_consumed_at := clock_timestamp();
                NEW.reserved_at := clock_timestamp();
                RETURN NEW;
            END IF;

            IF NEW.status IN ('dispatched','running','succeeded') THEN
                PERFORM medtrust.assert_compute_audit_ready_v7();
            END IF;
            IF NOT (
                (OLD.status='reserved' AND NEW.status IN ('dispatched','failed','interrupted')) OR
                (OLD.status='dispatched' AND NEW.status IN ('running','failed','interrupted','timed_out')) OR
                (OLD.status='running' AND NEW.status IN ('succeeded','failed','interrupted','cancelled','timed_out'))
            ) THEN RAISE EXCEPTION 'illegal ComputeRun status transition'; END IF;
            IF OLD.status <> 'prepared' AND ROW(
                NEW.quota_policy_id, NEW.run_count_constraint_id, NEW.run_limit_snapshot,
                NEW.reservation_ordinal, NEW.quota_scope_digest, NEW.quota_reservation_digest,
                NEW.quota_consumed_at, NEW.start_authorization_evaluation,
                NEW.start_authorization_evaluation_digest, NEW.compute_binding_id,
                NEW.egress_binding_id, NEW.audit_binding_id,
                NEW.execution_environment_snapshot, NEW.execution_environment_digest,
                NEW.reserved_at)
              IS DISTINCT FROM ROW(
                OLD.quota_policy_id, OLD.run_count_constraint_id, OLD.run_limit_snapshot,
                OLD.reservation_ordinal, OLD.quota_scope_digest, OLD.quota_reservation_digest,
                OLD.quota_consumed_at, OLD.start_authorization_evaluation,
                OLD.start_authorization_evaluation_digest, OLD.compute_binding_id,
                OLD.egress_binding_id, OLD.audit_binding_id,
                OLD.execution_environment_snapshot, OLD.execution_environment_digest,
                OLD.reserved_at) THEN
                RAISE EXCEPTION 'ComputeRun reservation evidence is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_compute_run_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON medtrust.compute_runs FOR EACH ROW EXECUTE FUNCTION medtrust.guard_compute_run_v7()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_compute_run_guard ON medtrust.compute_runs")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_compute_run_v7()")
    op.execute("DROP TRIGGER IF EXISTS trg_compute_job_guard ON medtrust.compute_jobs")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_compute_job_v7()")
    op.execute("DROP FUNCTION IF EXISTS medtrust.assert_compute_audit_ready_v7()")
    op.drop_table("compute_runs", schema=SCHEMA)
    op.drop_table("compute_jobs", schema=SCHEMA)
