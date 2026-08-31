"""Add Phase 5.7 result review and download rejection audit events.

Revision ID: 20260725_0031
Revises: 20260724_0030
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260725_0031"
down_revision: str | None = "20260724_0030"
branch_labels: str | None = None
depends_on: str | None = None

NEW_EVENT_TYPES = (
    "artifact.review.plan.created",
    "result.download.rejected",
)


def _constraint_values(connection, constraint_name: str, field: str) -> list[str]:
    definition = connection.execute(
        sa.text(
            """
            SELECT pg_get_constraintdef(c.oid)
              FROM pg_constraint c
              JOIN pg_class t ON t.oid=c.conrelid
              JOIN pg_namespace n ON n.oid=t.relnamespace
             WHERE n.nspname='medtrust'
               AND t.relname='audit_events'
               AND c.conname=:constraint_name
            """
        ),
        {"constraint_name": constraint_name},
    ).scalar_one()
    if "ARRAY[" in definition:
        start = definition.index("ARRAY[") + len("ARRAY[")
        end = definition.index("])", start)
    else:
        marker = f"{field} IN ("
        start = definition.index(marker) + len(marker)
        end = definition.index(")", start)
    return [
        item.strip().split("::", 1)[0].strip().strip("'")
        for item in definition[start:end].split(",")
        if item.strip()
    ]


def _replace_check(values: list[str]) -> None:
    rendered = ",".join(f"'{value}'" for value in values)
    name = "ck_audit_events_ck_audit_events_event_type"
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        "ALTER TABLE medtrust.audit_events "
        f"ADD CONSTRAINT {name} CHECK (event_type IN ({rendered}))"
    )


def _function_definition(connection, name: str) -> str:
    return connection.execute(
        sa.text(f"SELECT pg_get_functiondef('medtrust.{name}()'::regprocedure)")
    ).scalar_one()


def _cases() -> str:
    return """
                WHEN 'artifact.review.plan.created' THEN
                    IF NEW.subject_type<>'artifact' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.artifacts a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'result.download.rejected' THEN
                    IF NEW.subject_type<>'result_download_grant' OR NEW.result<>'denied' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.result_download_grants g WHERE g.id=NEW.subject_id AND g.space_id=NEW.space_id) INTO v_subject_ok;
"""


def _extend(enable: bool) -> None:
    connection = op.get_bind()
    name = "ck_audit_events_ck_audit_events_event_type"
    values = _constraint_values(connection, name, "event_type")
    if enable:
        values.extend(value for value in NEW_EVENT_TYPES if value not in values)
    else:
        values = [value for value in values if value not in NEW_EVENT_TYPES]
    _replace_check(values)
    guard = _function_definition(connection, "guard_audit_event_v8")
    cases = _cases()
    marker = "                WHEN 'contract.revision.proposed' THEN"
    if enable:
        if marker not in guard:
            raise RuntimeError("expected audit guard insertion marker was not found")
        op.execute(guard.replace(marker, cases + marker, 1))
    else:
        if cases not in guard:
            raise RuntimeError("expected Phase 5.7 audit cases were not found")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    _extend(True)


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
        raise RuntimeError(
            "cannot remove Phase 5.7 while result-release audit evidence exists"
        )
    _extend(False)
