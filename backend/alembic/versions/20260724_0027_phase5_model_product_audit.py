"""Add Phase 5.2 model lifecycle events and reusable registry bindings.

Revision ID: 20260724_0027
Revises: 20260724_0026
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0027"
down_revision: str | None = "20260724_0026"
branch_labels: str | None = None
depends_on: str | None = None

NEW_EVENT_TYPES = (
    "model_product.version.created",
    "model_product.version.updated",
    "model_product.version.returned",
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


def _guard_definition(connection) -> str:
    return connection.execute(
        sa.text(
            "SELECT pg_get_functiondef("
            "'medtrust.guard_audit_event_v8()'::regprocedure)"
        )
    ).scalar_one()


def _new_cases() -> str:
    return """
                WHEN 'model_product.version.created' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'model_product.version.updated' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'model_product.version.returned' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
"""


def upgrade() -> None:
    connection = op.get_bind()
    op.drop_constraint(
        "uq_model_versions_registry_binding",
        "model_versions",
        schema="medtrust",
        type_="unique",
    )
    values = _event_check_values(connection)
    for event_type in NEW_EVENT_TYPES:
        if event_type not in values:
            values.append(event_type)
    _replace_event_constraint(values)
    definition = _guard_definition(connection)
    marker = "                WHEN 'model_product.version.submitted' THEN"
    if marker not in definition:
        raise RuntimeError("expected model-product audit guard branch was not found")
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
        raise RuntimeError("cannot remove Phase 5.2 audit vocabulary while events exist")
    values = [
        value for value in _event_check_values(connection) if value not in NEW_EVENT_TYPES
    ]
    _replace_event_constraint(values)
    definition = _guard_definition(connection)
    cases = _new_cases()
    if cases not in definition:
        raise RuntimeError("expected Phase 5.2 audit guard branches were not found")
    op.execute(definition.replace(cases, "", 1))
    duplicate_count = connection.execute(
        sa.text(
            """
            SELECT count(*)
              FROM (
                    SELECT entrypoint_id, model_digest, registry_digest
                      FROM medtrust.model_versions
                     GROUP BY entrypoint_id, model_digest, registry_digest
                    HAVING count(*) > 1
                   ) duplicated
            """
        )
    ).scalar_one()
    if duplicate_count:
        raise RuntimeError(
            "cannot restore the historical registry-binding uniqueness while "
            "multiple product versions reuse one allowlisted asset"
        )
    op.create_unique_constraint(
        "uq_model_versions_registry_binding",
        "model_versions",
        ["entrypoint_id", "model_digest", "registry_digest"],
        schema="medtrust",
    )
