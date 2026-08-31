"""Require transactional Audit/outbox evidence for ComputeRun reservation.

Revision ID: 20260722_0015
Revises: 20260722_0014
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0015"
down_revision: str | None = "20260722_0014"
branch_labels: str | None = None
depends_on: str | None = None

OLD_GATE_CALL = "PERFORM medtrust.assert_compute_audit_ready_v7();"
NEW_GATE_CALL = (
    "PERFORM medtrust.assert_compute_run_reservation_audit_v8(NEW.id);"
)


def _replace_first_guard_call(old: str, new: str) -> None:
    bind = op.get_bind()
    definition = bind.execute(
        sa.text(
            "SELECT pg_get_functiondef('medtrust.guard_compute_run_v7()'::regprocedure)"
        )
    ).scalar_one()
    if old not in definition:
        raise RuntimeError("expected ComputeRun audit gate call was not found")
    bind.exec_driver_sql(definition.replace(old, new, 1))


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.assert_compute_run_reservation_audit_v8(
            p_run_id uuid
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            matching_events integer;
            matching_targets integer;
        BEGIN
            SELECT count(*) INTO matching_events
              FROM medtrust.audit_events ae
             WHERE ae.event_type='compute.run.reserved'
               AND ae.subject_type='compute_run'
               AND ae.subject_id=p_run_id
               AND ae.result='success'
               AND ae.evidence_snapshot->>'compute_run_id'=p_run_id::text;
            IF matching_events <> 1 THEN
                RAISE EXCEPTION 'AuditEvidenceUnavailable';
            END IF;

            SELECT count(*) INTO matching_targets
              FROM medtrust.outbox_messages om
              JOIN medtrust.audit_events ae ON ae.event_id=om.audit_event_id
             WHERE ae.event_type='compute.run.reserved'
               AND ae.subject_type='compute_run'
               AND ae.subject_id=p_run_id
               AND ((om.topic='medtrust.audit.v1' AND om.destination='audit.timeline')
                 OR (om.topic='medtrust.compute.dispatch.v1'
                     AND om.destination='compute.dispatch'));
            IF matching_targets <> 2 THEN
                RAISE EXCEPTION 'AuditEvidenceUnavailable';
            END IF;
        END;
        $$;
        """
    )
    # Only the prepared -> reserved call is replaced. The later
    # dispatched/running/succeeded gate remains the original fail-closed stub.
    _replace_first_guard_call(OLD_GATE_CALL, NEW_GATE_CALL)


def downgrade() -> None:
    _replace_first_guard_call(NEW_GATE_CALL, OLD_GATE_CALL)
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "medtrust.assert_compute_run_reservation_audit_v8(uuid)"
    )
