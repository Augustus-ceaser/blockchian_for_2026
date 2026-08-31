"""Add the missing Phase 5.1 data-product lifecycle audit events.

Revision ID: 20260724_0026
Revises: 20260723_0025
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0026"
down_revision: str | None = "20260723_0025"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"
NEW_EVENT_TYPES = (
    "data_product.version.created",
    "data_product.version.updated",
    "data_product.version.returned",
)


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
    values = []
    for item in definition[start:end].split(","):
        value = item.strip().split("::", 1)[0].strip().strip("'")
        if value:
            values.append(value)
    return values


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


def _guard_definition(connection) -> str:
    return connection.execute(
        sa.text(
            "SELECT pg_get_functiondef("
            "'medtrust.guard_audit_event_v8()'::regprocedure)"
        )
    ).scalar_one()


def _new_cases() -> str:
    return """
                WHEN 'data_product.version.created' THEN
                    IF NEW.subject_type<>'data_product_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.data_product_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'data_product.version.updated' THEN
                    IF NEW.subject_type<>'data_product_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.data_product_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'data_product.version.returned' THEN
                    IF NEW.subject_type<>'data_product_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.data_product_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
"""


def upgrade() -> None:
    connection = op.get_bind()
    values = _event_check_values(connection)
    for event_type in NEW_EVENT_TYPES:
        if event_type not in values:
            values.append(event_type)
    _replace_event_constraint(values)
    definition = _guard_definition(connection)
    marker = "                WHEN 'data_product.version.submitted' THEN"
    if marker not in definition:
        raise RuntimeError("expected Phase 4 data-product audit guard branch was not found")
    op.execute(definition.replace(marker, _new_cases() + marker, 1))


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
        raise RuntimeError("cannot remove Phase 5.1 audit vocabulary while events exist")
    values = [
        value for value in _event_check_values(connection) if value not in NEW_EVENT_TYPES
    ]
    _replace_event_constraint(values)
    definition = _guard_definition(connection)
    cases = _new_cases()
    if cases not in definition:
        raise RuntimeError("expected Phase 5.1 audit guard branches were not found")
    op.execute(definition.replace(cases, "", 1))
