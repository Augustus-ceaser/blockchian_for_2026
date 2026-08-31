"""Replace the temporary Run audit stub with callback evidence guards.

Revision ID: 20260722_0020
Revises: 20260722_0019
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0020"
down_revision: str | None = "20260722_0019"
branch_labels: str | None = None
depends_on: str | None = None

OLD_GATE = """IF NEW.status IN ('running','succeeded') THEN
                PERFORM medtrust.assert_compute_audit_ready_v7();
            END IF;"""
CALLBACK_GATE_MARKER = "-- callback evidence is enforced by trg_compute_run_callback_v11"


def _replace_gate(old: str, new: str) -> None:
    bind = op.get_bind()
    definition = bind.execute(
        sa.text(
            "SELECT pg_get_functiondef('medtrust.guard_compute_run_v7()'::regprocedure)"
        )
    ).scalar_one()
    if old not in definition:
        raise RuntimeError("expected ComputeRun audit placeholder gate was not found")
    bind.exec_driver_sql(definition.replace(old, new, 1))


def upgrade() -> None:
    _replace_gate(OLD_GATE, CALLBACK_GATE_MARKER)
    op.execute(
        """
        CREATE FUNCTION medtrust.assert_compute_run_callback_v11(
            p_run_id uuid,
            p_callback_type text,
            p_event_type text,
            p_outcome_code text,
            p_expected_statuses text[]
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            v_run medtrust.compute_runs%ROWTYPE;
            v_callback medtrust.execution_callback_inbox_entries%ROWTYPE;
            v_event medtrust.audit_events%ROWTYPE;
            v_outbox_count integer;
        BEGIN
            SELECT * INTO v_run FROM medtrust.compute_runs WHERE id=p_run_id;
            IF NOT FOUND OR NOT (v_run.status=ANY(p_expected_statuses)) THEN
                RAISE EXCEPTION 'callback Run status evidence is incomplete' USING ERRCODE='23514';
            END IF;
            SELECT * INTO v_callback
              FROM medtrust.execution_callback_inbox_entries i
             WHERE i.compute_run_id=p_run_id AND i.space_id=v_run.space_id
               AND i.callback_type=p_callback_type AND i.status='completed'
               AND i.outcome_code=p_outcome_code
               AND i.external_execution_id=v_run.execution_reference
             ORDER BY i.completed_at DESC LIMIT 1;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Run transition requires completed callback Inbox evidence' USING ERRCODE='23514';
            END IF;
            SELECT * INTO v_event FROM medtrust.audit_events e
             WHERE e.event_type=p_event_type AND e.subject_type='compute_run'
               AND e.subject_id=p_run_id AND e.space_id=v_run.space_id
               AND e.evidence_snapshot->>'callback_entry_id'=v_callback.id::text
               AND e.evidence_snapshot->>'callback_fact_digest'=v_callback.normalized_fact_digest
             ORDER BY e.stream_sequence DESC LIMIT 1;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Run transition requires matching callback AuditEvent' USING ERRCODE='23514';
            END IF;
            SELECT count(*) INTO v_outbox_count FROM medtrust.outbox_messages m
             WHERE m.audit_event_id=v_event.event_id
               AND m.topic='medtrust.audit.v1' AND m.destination='audit.timeline';
            IF v_outbox_count<>1 THEN
                RAISE EXCEPTION 'Run callback AuditEvent requires audit.timeline Outbox' USING ERRCODE='23514';
            END IF;
            IF p_callback_type='execution.started' AND
               (v_run.start_receipt_digest IS NULL OR v_run.started_at IS NULL) THEN
                RAISE EXCEPTION 'started Run receipt is incomplete' USING ERRCODE='23514';
            END IF;
            IF p_callback_type='execution.completed' THEN
                IF v_run.completion_receipt_digest IS NULL OR v_run.finished_at IS NULL THEN
                    RAISE EXCEPTION 'completed Run receipt is incomplete' USING ERRCODE='23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM medtrust.artifacts a
                     WHERE a.compute_run_id=p_run_id AND a.space_id=v_run.space_id
                       AND a.release_status='quarantined'
                ) THEN
                    RAISE EXCEPTION 'completed Run requires a quarantined Artifact' USING ERRCODE='23514';
                END IF;
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_compute_run_callback_v11()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status='dispatched' AND NEW.status='running' THEN
                PERFORM medtrust.assert_compute_run_callback_v11(
                    NEW.id,'execution.started','compute.run.started','run_started',
                    ARRAY['running','succeeded','failed','interrupted','cancelled','timed_out']);
            ELSIF OLD.status='running' AND NEW.status='succeeded' THEN
                PERFORM medtrust.assert_compute_run_callback_v11(
                    NEW.id,'execution.completed','compute.run.completed','run_completed',
                    ARRAY['succeeded']);
            ELSIF NEW.status='failed' AND OLD.status IN ('reserved','dispatched','running') THEN
                PERFORM medtrust.assert_compute_run_callback_v11(
                    NEW.id,'execution.failed','compute.run.failed','run_failed',ARRAY['failed']);
            ELSIF NEW.status='interrupted' AND OLD.status IN ('reserved','dispatched','running') THEN
                PERFORM medtrust.assert_compute_run_callback_v11(
                    NEW.id,'execution.interrupted','compute.run.interrupted','run_interrupted',
                    ARRAY['interrupted']);
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_compute_run_callback_v11
        AFTER UPDATE ON medtrust.compute_runs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_compute_run_callback_v11()
        """
    )


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM medtrust.compute_runs "
            "WHERE status IN ('running','succeeded','failed','interrupted')"
        )
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade callback evidence while callback-driven Runs exist")
    op.execute("DROP TRIGGER IF EXISTS trg_compute_run_callback_v11 ON medtrust.compute_runs")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_compute_run_callback_v11()")
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.assert_compute_run_callback_v11(uuid,text,text,text,text[])"
    )
    _replace_gate(CALLBACK_GATE_MARKER, OLD_GATE)
