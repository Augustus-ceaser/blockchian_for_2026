"""Add Phase 5.3 application lifecycle audit vocabulary.

Revision ID: 20260724_0028
Revises: 20260724_0027
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0028"
down_revision: str | None = "20260724_0027"
branch_labels: str | None = None
depends_on: str | None = None

NEW_EVENT_TYPES = (
    "application.created",
    "application.updated",
    "application.compatibility.checked",
    "application.returned",
    "application.rejected",
    "application.approved",
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


def _function_definition(connection, name: str) -> str:
    return connection.execute(
        sa.text(
            f"SELECT pg_get_functiondef('medtrust.{name}()'::regprocedure)"
        )
    ).scalar_one()


def _audit_cases() -> str:
    return """
                WHEN 'application.created' THEN
                    IF NEW.subject_type<>'application' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.applications a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'application.updated' THEN
                    IF NEW.subject_type<>'application' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.applications a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'application.compatibility.checked' THEN
                    IF NEW.subject_type<>'application' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.applications a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'application.returned' THEN
                    IF NEW.subject_type<>'application' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.applications a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'application.rejected' THEN
                    IF NEW.subject_type<>'application' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.applications a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'application.approved' THEN
                    IF NEW.subject_type<>'application' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.applications a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
"""


def _set_model_review_sequence(definition: str, old: int, new: int) -> str:
    old_condition = (
        "NEW.review_type='model_provider_review' AND "
        f"(model_provider_org IS NULL OR NEW.assignee_organization_id<>model_provider_org OR NEW.sequence_no<>{old})"
    )
    new_condition = (
        "NEW.review_type='model_provider_review' AND "
        f"(model_provider_org IS NULL OR NEW.assignee_organization_id<>model_provider_org OR NEW.sequence_no<>{new})"
    )
    old_message = f"model provider review must route to model provider at sequence {old}"
    new_message = f"model provider review must route to model provider at sequence {new}"
    if old_condition not in definition or old_message not in definition:
        raise RuntimeError("expected model-provider review routing guard was not found")
    return definition.replace(old_condition, new_condition).replace(
        old_message, new_message
    )


def _create_model_selection_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_application_model_selection_draft()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT status INTO parent_status
                  FROM medtrust.applications WHERE id=OLD.application_id;
                IF parent_status IS NULL THEN RETURN OLD; END IF;
                IF parent_status <> 'draft' THEN
                    RAISE EXCEPTION 'application model selection can only change in draft';
                END IF;
                RETURN OLD;
            END IF;
            SELECT status INTO parent_status
              FROM medtrust.applications WHERE id=NEW.application_id;
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'application model selection can only change in draft';
            END IF;
            IF TG_OP = 'UPDATE' AND
               ROW(NEW.id,NEW.application_id,NEW.space_id,NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id,OLD.application_id,OLD.space_id,OLD.created_at) THEN
                RAISE EXCEPTION 'application model selection identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_application_model_selection_draft "
        "BEFORE INSERT OR UPDATE OR DELETE "
        "ON medtrust.application_model_selections "
        "FOR EACH ROW EXECUTE FUNCTION "
        "medtrust.guard_application_model_selection_draft()"
    )


def _set_application_item_scope_deferrable(*, deferred: bool) -> None:
    op.drop_constraint(
        "fk_application_items_application_scope",
        "application_items",
        schema="medtrust",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_application_items_application_scope",
        "application_items",
        "applications",
        ["application_id", "space_id", "provider_organization_id"],
        ["id", "space_id", "provider_organization_id"],
        source_schema="medtrust",
        referent_schema="medtrust",
        ondelete="CASCADE",
        deferrable=deferred,
        initially="DEFERRED" if deferred else None,
    )


def upgrade() -> None:
    connection = op.get_bind()
    values = _event_check_values(connection)
    for event_type in NEW_EVENT_TYPES:
        if event_type not in values:
            values.append(event_type)
    _replace_event_constraint(values)

    audit_guard = _function_definition(connection, "guard_audit_event_v8")
    marker = "                WHEN 'application.submitted' THEN"
    if marker not in audit_guard:
        raise RuntimeError("expected application audit guard branch was not found")
    op.execute(audit_guard.replace(marker, _audit_cases() + marker, 1))

    review_guard = _function_definition(connection, "guard_review_task_lifecycle")
    op.execute(_set_model_review_sequence(review_guard, 20, 30))
    _set_application_item_scope_deferrable(deferred=True)
    _create_model_selection_guard()


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
        raise RuntimeError("cannot remove Phase 5.3 audit vocabulary while events exist")

    values = [
        value for value in _event_check_values(connection) if value not in NEW_EVENT_TYPES
    ]
    _replace_event_constraint(values)

    audit_guard = _function_definition(connection, "guard_audit_event_v8")
    cases = _audit_cases()
    if cases not in audit_guard:
        raise RuntimeError("expected Phase 5.3 audit guard branches were not found")
    op.execute(audit_guard.replace(cases, "", 1))

    review_guard = _function_definition(connection, "guard_review_task_lifecycle")
    op.execute(_set_model_review_sequence(review_guard, 30, 20))
    op.execute(
        "DROP TRIGGER IF EXISTS trg_application_model_selection_draft "
        "ON medtrust.application_model_selections"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.guard_application_model_selection_draft()"
    )
    _set_application_item_scope_deferrable(deferred=False)
