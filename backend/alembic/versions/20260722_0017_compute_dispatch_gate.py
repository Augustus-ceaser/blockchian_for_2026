"""Permit only an audited Coordinator transition from reserved to dispatched.

Revision ID: 20260722_0017
Revises: 20260722_0016
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0017"
down_revision: str | None = "20260722_0016"
branch_labels: str | None = None
depends_on: str | None = None

OLD_GATE = """IF NEW.status IN ('dispatched','running','succeeded') THEN
                PERFORM medtrust.assert_compute_audit_ready_v7();
            END IF;"""
NEW_GATE = """IF NEW.status IN ('running','succeeded') THEN
                PERFORM medtrust.assert_compute_audit_ready_v7();
            END IF;"""


def _replace_gate(old: str, new: str) -> None:
    bind = op.get_bind()
    definition = bind.execute(
        sa.text(
            "SELECT pg_get_functiondef('medtrust.guard_compute_run_v7()'::regprocedure)"
        )
    ).scalar_one()
    if old not in definition:
        raise RuntimeError("expected ComputeRun execution gate was not found")
    bind.exec_driver_sql(definition.replace(old, new, 1))


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.assert_compute_run_dispatched_v9(p_run_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            v_run medtrust.compute_runs%ROWTYPE;
            v_event medtrust.audit_events%ROWTYPE;
            v_target_count integer;
            v_inbox_count integer;
        BEGIN
            SELECT * INTO v_run FROM medtrust.compute_runs WHERE id=p_run_id;
            IF NOT FOUND OR v_run.status<>'dispatched' OR v_run.execution_reference IS NULL
               OR v_run.dispatch_receipt_digest IS NULL OR v_run.dispatched_at IS NULL THEN
                RAISE EXCEPTION 'dispatched Run evidence is incomplete' USING ERRCODE='23514';
            END IF;
            SELECT * INTO v_event FROM medtrust.audit_events
             WHERE event_type='compute.run.dispatched'
               AND subject_type='compute_run' AND subject_id=p_run_id
               AND space_id=v_run.space_id AND result='success';
            IF NOT FOUND OR v_event.evidence_snapshot->>'compute_run_id'<>p_run_id::text
               OR v_event.evidence_snapshot->>'dispatch_receipt_digest'<>v_run.dispatch_receipt_digest THEN
                RAISE EXCEPTION 'dispatched Run requires matching AuditEvent' USING ERRCODE='23514';
            END IF;
            SELECT count(*) INTO v_target_count FROM medtrust.outbox_messages
             WHERE audit_event_id=v_event.event_id
               AND topic='medtrust.audit.v1' AND destination='audit.timeline';
            IF v_target_count<>1 THEN
                RAISE EXCEPTION 'dispatched Run requires one audit.timeline Outbox target' USING ERRCODE='23514';
            END IF;
            SELECT count(*) INTO v_inbox_count
              FROM medtrust.consumer_inbox_entries i
              JOIN medtrust.audit_events source_event ON source_event.event_id=i.event_id
             WHERE i.consumer_name='execution-coordinator'
               AND i.space_id=v_run.space_id
               AND source_event.event_type='compute.run.reserved'
               AND source_event.subject_type='compute_run'
               AND source_event.subject_id=p_run_id
               AND i.status='completed'
               AND i.outcome_code IN ('executor_submitted','already_dispatched')
               AND i.outcome_reference_type='compute_run'
               AND i.outcome_reference_id=p_run_id;
            IF v_inbox_count<>1 THEN
                RAISE EXCEPTION 'dispatched Run requires completed Coordinator Inbox evidence' USING ERRCODE='23514';
            END IF;
        END;
        $$;
        """
    )
    _replace_gate(OLD_GATE, NEW_GATE)
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_compute_run_dispatched_v9()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM medtrust.assert_compute_run_dispatched_v9(NEW.id);
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_compute_run_dispatched_v9
        AFTER UPDATE ON medtrust.compute_runs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW WHEN (OLD.status='reserved' AND NEW.status='dispatched')
        EXECUTE FUNCTION medtrust.guard_compute_run_dispatched_v9()
        """
    )


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM medtrust.compute_runs "
            "WHERE status IN ('dispatched','running','succeeded')"
        )
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade dispatch gate while dispatched Runs exist")
    op.execute("DROP TRIGGER IF EXISTS trg_compute_run_dispatched_v9 ON medtrust.compute_runs")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_compute_run_dispatched_v9()")
    op.execute("DROP FUNCTION IF EXISTS medtrust.assert_compute_run_dispatched_v9(uuid)")
    _replace_gate(NEW_GATE, OLD_GATE)
