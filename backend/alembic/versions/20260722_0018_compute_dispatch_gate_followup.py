"""Keep dispatch evidence valid when a later state is committed in the same transaction.

Revision ID: 20260722_0018
Revises: 20260722_0017
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op


revision: str = "20260722_0018"
down_revision: str | None = "20260722_0017"
branch_labels: str | None = None
depends_on: str | None = None


def _create_assertion(*, allow_later_states: bool) -> None:
    status_check = (
        "v_run.status NOT IN ('dispatched','running','succeeded','failed','interrupted','timed_out')"
        if allow_later_states
        else "v_run.status<>'dispatched'"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION medtrust.assert_compute_run_dispatched_v9(p_run_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            v_run medtrust.compute_runs%ROWTYPE;
            v_event medtrust.audit_events%ROWTYPE;
            v_target_count integer;
            v_inbox_count integer;
        BEGIN
            SELECT * INTO v_run FROM medtrust.compute_runs WHERE id=p_run_id;
            IF NOT FOUND OR {status_check} OR v_run.execution_reference IS NULL
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


def upgrade() -> None:
    _create_assertion(allow_later_states=True)


def downgrade() -> None:
    _create_assertion(allow_later_states=False)
