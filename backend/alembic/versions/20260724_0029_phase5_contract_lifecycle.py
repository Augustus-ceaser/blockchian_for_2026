"""Add Phase 5.4 contract lifecycle audit vocabulary.

Revision ID: 20260724_0029
Revises: 20260724_0028
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0029"
down_revision: str | None = "20260724_0028"
branch_labels: str | None = None
depends_on: str | None = None

NEW_EVENT_TYPES = ("contract.draft.generated", "contract.policy.converged")


def _event_check_values(connection) -> list[str]:
    definition = connection.execute(
        sa.text(
            """
            SELECT pg_get_constraintdef(c.oid)
              FROM pg_constraint c
              JOIN pg_class t ON t.oid=c.conrelid
              JOIN pg_namespace n ON n.oid=t.relnamespace
             WHERE n.nspname='medtrust'
               AND t.relname='audit_events'
               AND c.conname='ck_audit_events_ck_audit_events_event_type'
            """
        )
    ).scalar_one()
    start = definition.index("ARRAY[") + len("ARRAY[")
    end = definition.index("])", start)
    return [
        item.strip().split("::", 1)[0].strip().strip("'")
        for item in definition[start:end].split(",")
        if item.strip()
    ]


def _replace_event_constraint(values: list[str]) -> None:
    rendered = ",".join(f"'{value}'" for value in values)
    op.execute(
        "ALTER TABLE medtrust.audit_events "
        "DROP CONSTRAINT ck_audit_events_ck_audit_events_event_type"
    )
    op.execute(
        "ALTER TABLE medtrust.audit_events "
        "ADD CONSTRAINT ck_audit_events_ck_audit_events_event_type "
        f"CHECK (event_type IN ({rendered}))"
    )


def _function_definition(connection, name: str) -> str:
    return connection.execute(
        sa.text(f"SELECT pg_get_functiondef('medtrust.{name}()'::regprocedure)")
    ).scalar_one()


def _audit_cases() -> str:
    return """
                WHEN 'contract.draft.generated' THEN
                    IF NEW.subject_type<>'contract_revision' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_revisions r JOIN medtrust.contracts c ON c.id=r.contract_id WHERE r.id=NEW.subject_id AND c.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'contract.policy.converged' THEN
                    IF NEW.subject_type<>'contract_revision' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_revisions r JOIN medtrust.contracts c ON c.id=r.contract_id WHERE r.id=NEW.subject_id AND c.space_id=NEW.space_id) INTO v_subject_ok;
"""


def _defer_phase54_execution_bindings(definition: str, *, enable: bool) -> str:
    original = """            IF EXISTS (
                SELECT 1 FROM medtrust.policies p
                WHERE p.contract_revision_id=NEW.id AND (
                    (p.action_code='execute_controlled_compute'"""
    deferred = """            IF NEW.terms_schema_version <> 'phase5.4/structured-contract/v1' AND EXISTS (
                SELECT 1 FROM medtrust.policies p
                WHERE p.contract_revision_id=NEW.id AND (
                    (p.action_code='execute_controlled_compute'"""
    source, target = (original, deferred) if enable else (deferred, original)
    if source not in definition:
        if not enable and original in definition:
            return definition
        raise RuntimeError("expected Contract execution-binding guard was not found")
    return definition.replace(source, target, 1)


def upgrade() -> None:
    connection = op.get_bind()
    values = _event_check_values(connection)
    for event_type in NEW_EVENT_TYPES:
        if event_type not in values:
            values.append(event_type)
    _replace_event_constraint(values)
    audit_guard = _function_definition(connection, "guard_audit_event_v8")
    marker = "                WHEN 'contract.revision.proposed' THEN"
    if marker not in audit_guard:
        raise RuntimeError("expected Contract audit guard branch was not found")
    op.execute(audit_guard.replace(marker, _audit_cases() + marker, 1))
    revision_guard = _function_definition(connection, "guard_contract_revision_core")
    op.execute(_defer_phase54_execution_bindings(revision_guard, enable=True))


def downgrade() -> None:
    connection = op.get_bind()
    count = connection.execute(
        sa.text(
            "SELECT count(*) FROM medtrust.audit_events "
            "WHERE event_type = ANY(:event_types)"
        ),
        {"event_types": list(NEW_EVENT_TYPES)},
    ).scalar_one()
    if count:
        raise RuntimeError("cannot remove Phase 5.4 audit vocabulary while events exist")
    values = [
        value for value in _event_check_values(connection) if value not in NEW_EVENT_TYPES
    ]
    _replace_event_constraint(values)
    audit_guard = _function_definition(connection, "guard_audit_event_v8")
    cases = _audit_cases()
    if cases not in audit_guard:
        raise RuntimeError("expected Phase 5.4 audit guard branches were not found")
    op.execute(audit_guard.replace(cases, "", 1))
    revision_guard = _function_definition(connection, "guard_contract_revision_core")
    op.execute(_defer_phase54_execution_bindings(revision_guard, enable=False))
