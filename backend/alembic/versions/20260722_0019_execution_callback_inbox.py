"""Add the dedicated external Executor Callback Inbox.

Revision ID: 20260722_0019
Revises: 20260722_0018
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260722_0019"
down_revision: str | None = "20260722_0018"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "execution_callback_inbox_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compute_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("executor_namespace", sa.String(96), nullable=False),
        sa.Column("external_execution_id", sa.String(256), nullable=False),
        sa.Column("callback_id", sa.String(160), nullable=False),
        sa.Column("callback_type", sa.String(32), nullable=False),
        sa.Column("callback_schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column("normalized_fact_digest", sa.String(71), nullable=False),
        sa.Column("execution_evidence_digest", sa.String(71), nullable=False),
        sa.Column("authentication_evidence_digest", sa.String(71), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="received", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_owner", sa.String(96), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_code", sa.String(64), nullable=True),
        sa.Column("outcome_reference_type", sa.String(32), nullable=True),
        sa.Column("outcome_reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("processing_error", sa.String(1024), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "callback_type IN ('execution.started','execution.completed','execution.failed','execution.interrupted')",
            name="ck_execution_callback_inbox_entries_callback_type",
        ),
        sa.CheckConstraint("callback_schema_version=1", name="ck_execution_callback_inbox_entries_schema_version"),
        sa.CheckConstraint(
            "status IN ('received','processing','completed','dead_letter')",
            name="ck_execution_callback_inbox_entries_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="ck_execution_callback_inbox_entries_attempt_count_range",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_execution_callback_inbox_entries_row_version_positive"),
        sa.CheckConstraint("payload_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_execution_callback_inbox_entries_payload_digest_format"),
        sa.CheckConstraint("normalized_fact_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_execution_callback_inbox_entries_fact_digest_format"),
        sa.CheckConstraint("execution_evidence_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_execution_callback_inbox_entries_execution_digest_format"),
        sa.CheckConstraint("authentication_evidence_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_execution_callback_inbox_entries_authentication_digest_format"),
        sa.CheckConstraint(
            "outcome_code IS NULL OR outcome_code IN ('run_started','run_completed','run_failed','run_interrupted','duplicate_fact','terminal_noop','non_retryable_rejection')",
            name="ck_execution_callback_inbox_entries_outcome_code",
        ),
        sa.CheckConstraint(
            "(outcome_reference_type IS NULL) = (outcome_reference_id IS NULL)",
            name="ck_execution_callback_inbox_entries_outcome_reference_shape",
        ),
        sa.CheckConstraint(
            "(status='received' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND completed_at IS NULL AND terminal_at IS NULL AND outcome_code IS NULL AND outcome_reference_type IS NULL AND outcome_reference_id IS NULL) OR "
            "(status='processing' AND locked_at IS NOT NULL AND lock_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND processing_started_at IS NOT NULL AND completed_at IS NULL AND terminal_at IS NULL AND outcome_code IS NULL AND outcome_reference_type IS NULL AND outcome_reference_id IS NULL) OR "
            "(status='completed' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND completed_at IS NOT NULL AND terminal_at IS NOT NULL AND outcome_code IS NOT NULL) OR "
            "(status='dead_letter' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND completed_at IS NULL AND terminal_at IS NOT NULL AND processing_error IS NOT NULL)",
            name="ck_execution_callback_inbox_entries_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["compute_run_id"],
            ["medtrust.compute_runs.id"],
            name="fk_execution_callback_inbox_entries_compute_run_id_compute_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_callback_inbox_entries"),
        sa.UniqueConstraint(
            "executor_namespace",
            "callback_id",
            name="uq_execution_callback_namespace_id",
        ),
        sa.UniqueConstraint(
            "executor_namespace",
            "compute_run_id",
            "callback_type",
            "normalized_fact_digest",
            name="uq_execution_callback_semantic_fact",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_callback_received_claim",
        "execution_callback_inbox_entries",
        ["available_at", "received_at", "id"],
        schema=SCHEMA,
        postgresql_where=sa.text("status='received'"),
    )
    op.create_index(
        "ix_execution_callback_processing_lease",
        "execution_callback_inbox_entries",
        ["lease_expires_at", "id"],
        schema=SCHEMA,
        postgresql_where=sa.text("status='processing'"),
    )
    op.create_index(
        "ix_execution_callback_run_timeline",
        "execution_callback_inbox_entries",
        ["compute_run_id", "occurred_at", "id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_callback_space_received",
        "execution_callback_inbox_entries",
        ["space_id", sa.text("received_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_callback_external_execution",
        "execution_callback_inbox_entries",
        ["executor_namespace", "external_execution_id"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.execution_callback_fact_digest_v1(
            p_space_id uuid,
            p_compute_run_id uuid,
            p_executor_namespace text,
            p_external_execution_id text,
            p_callback_type text,
            p_occurred_at timestamptz,
            p_payload_digest text,
            p_execution_evidence_digest text
        ) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT medtrust.sha256_canonical_jsonb_v1(
                jsonb_build_object(
                    'schema_version','execution-callback-fact/v1',
                    'space_id',p_space_id::text,
                    'compute_run_id',p_compute_run_id::text,
                    'executor_namespace',p_executor_namespace,
                    'external_execution_id',p_external_execution_id,
                    'callback_type',p_callback_type,
                    'occurred_at',to_char(p_occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    'payload_digest',p_payload_digest,
                    'execution_evidence_digest',p_execution_evidence_digest
                )
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_execution_callback_inbox_v10()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_space_id uuid;
            v_payload_digest text;
            v_fact_digest text;
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'ExecutionCallbackInboxEntry cannot be deleted' USING ERRCODE='55000';
            END IF;

            SELECT space_id INTO v_space_id FROM medtrust.compute_runs WHERE id=NEW.compute_run_id;
            IF NOT FOUND OR v_space_id<>NEW.space_id THEN
                RAISE EXCEPTION 'Callback Inbox Run and Space are inconsistent' USING ERRCODE='23514';
            END IF;
            v_payload_digest := medtrust.sha256_canonical_jsonb_v1(NEW.payload_snapshot);
            IF v_payload_digest<>NEW.payload_digest THEN
                RAISE EXCEPTION 'Callback payload digest is inconsistent' USING ERRCODE='23514';
            END IF;
            v_fact_digest := medtrust.execution_callback_fact_digest_v1(
                NEW.space_id,NEW.compute_run_id,NEW.executor_namespace,
                NEW.external_execution_id,NEW.callback_type,NEW.occurred_at,
                NEW.payload_digest,NEW.execution_evidence_digest
            );
            IF v_fact_digest<>NEW.normalized_fact_digest THEN
                RAISE EXCEPTION 'Callback semantic fact digest is inconsistent' USING ERRCODE='23514';
            END IF;

            IF TG_OP='INSERT' THEN
                IF NEW.status<>'received' OR NEW.attempt_count<>0 OR NEW.row_version<>1 THEN
                    RAISE EXCEPTION 'ExecutionCallbackInboxEntry must start as received' USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status IN ('completed','dead_letter') THEN
                RAISE EXCEPTION 'terminal ExecutionCallbackInboxEntry is immutable' USING ERRCODE='55000';
            END IF;
            IF ROW(
                NEW.space_id,NEW.compute_run_id,NEW.executor_namespace,
                NEW.external_execution_id,NEW.callback_id,NEW.callback_type,
                NEW.callback_schema_version,NEW.occurred_at,NEW.payload_snapshot,
                NEW.payload_digest,NEW.normalized_fact_digest,
                NEW.execution_evidence_digest,NEW.authentication_evidence_digest,
                NEW.correlation_id,NEW.causation_id,NEW.received_at,NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.space_id,OLD.compute_run_id,OLD.executor_namespace,
                OLD.external_execution_id,OLD.callback_id,OLD.callback_type,
                OLD.callback_schema_version,OLD.occurred_at,OLD.payload_snapshot,
                OLD.payload_digest,OLD.normalized_fact_digest,
                OLD.execution_evidence_digest,OLD.authentication_evidence_digest,
                OLD.correlation_id,OLD.causation_id,OLD.received_at,OLD.created_at
            ) THEN
                RAISE EXCEPTION 'ExecutionCallbackInboxEntry source evidence is immutable' USING ERRCODE='55000';
            END IF;
            IF NEW.row_version<>OLD.row_version+1 THEN
                RAISE EXCEPTION 'ExecutionCallbackInboxEntry row_version must advance once' USING ERRCODE='40001';
            END IF;
            IF OLD.status='received' AND NEW.status='processing' THEN
                IF NEW.attempt_count<>OLD.attempt_count+1 THEN
                    RAISE EXCEPTION 'Callback claim must consume one attempt' USING ERRCODE='23514';
                END IF;
            ELSIF OLD.status='processing' AND NEW.status='processing' THEN
                IF OLD.lease_expires_at>statement_timestamp() OR NEW.attempt_count<>OLD.attempt_count+1 THEN
                    RAISE EXCEPTION 'Callback lease can only be reclaimed after expiry' USING ERRCODE='55000';
                END IF;
            ELSIF OLD.status='processing' AND NEW.status IN ('received','completed','dead_letter') THEN
                IF NEW.attempt_count<>OLD.attempt_count THEN
                    RAISE EXCEPTION 'Callback settlement cannot change attempt_count' USING ERRCODE='23514';
                END IF;
            ELSE
                RAISE EXCEPTION 'illegal ExecutionCallbackInboxEntry transition' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_execution_callback_inbox_v10
        BEFORE INSERT OR UPDATE OR DELETE ON medtrust.execution_callback_inbox_entries
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_execution_callback_inbox_v10()
        """
    )


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM medtrust.execution_callback_inbox_entries")
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade 0019 while Callback Inbox entries exist")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_execution_callback_inbox_v10 "
        "ON medtrust.execution_callback_inbox_entries"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_execution_callback_inbox_v10()")
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.execution_callback_fact_digest_v1(uuid,uuid,text,text,text,timestamptz,text,text)"
    )
    op.drop_table("execution_callback_inbox_entries", schema=SCHEMA)
