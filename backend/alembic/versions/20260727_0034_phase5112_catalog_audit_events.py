"""Add complete Phase 5.11.2 catalog audit event vocabulary.

Revision ID: 20260727_0034
Revises: 20260727_0033
"""

from alembic import op
import sqlalchemy as sa

revision = "20260727_0034"
down_revision = "20260727_0033"
branch_labels = None
depends_on = None

EVENTS = ("external_catalog.source.created", "external_catalog.sync.started")
SUBJECT = "external_catalog_source"


def _values(name: str) -> list[str]:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname='medtrust' AND t.relname='audit_events' AND c.conname=:name"
        ),
        {"name": name},
    ).scalar_one()
    start = definition.index("ARRAY[") + 6
    end = definition.index("])", start)
    return [
        item.strip().split("::", 1)[0].strip().strip("'")
        for item in definition[start:end].split(",")
        if item.strip()
    ]


def _replace(name: str, field: str, values: list[str]) -> None:
    rendered = ",".join(f"'{value}'" for value in values)
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        f"ALTER TABLE medtrust.audit_events ADD CONSTRAINT {name} "
        f"CHECK ({field} IN ({rendered}))"
    )


def _change(enable: bool) -> None:
    event_name = "ck_audit_events_ck_audit_events_event_type"
    subject_name = "ck_audit_events_ck_audit_events_subject_type"
    events = _values(event_name)
    subjects = _values(subject_name)
    if enable:
        events.extend(value for value in EVENTS if value not in events)
        if SUBJECT not in subjects:
            subjects.append(SUBJECT)
    else:
        events = [value for value in events if value not in EVENTS]
        subjects = [value for value in subjects if value != SUBJECT]
    _replace(event_name, "event_type", events)
    _replace(subject_name, "subject_type", subjects)

    connection = op.get_bind()
    guard = connection.execute(
        sa.text("SELECT pg_get_functiondef('medtrust.guard_audit_event_v8()'::regprocedure)")
    ).scalar_one()
    cases = """
                WHEN 'external_catalog.source.created' THEN
                    IF NEW.subject_type<>'external_catalog_source' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.external_catalog_sources s WHERE s.id=NEW.subject_id AND s.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'external_catalog.sync.started' THEN
                    IF NEW.subject_type<>'external_catalog_sync_run' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.external_catalog_sync_runs r JOIN medtrust.external_catalog_sources s ON s.id=r.source_id WHERE r.id=NEW.subject_id AND s.space_id=NEW.space_id) INTO v_subject_ok;
"""
    marker = "                WHEN 'contract.revision.proposed' THEN"
    if enable:
        if marker not in guard:
            raise RuntimeError("audit guard insertion marker missing")
        op.execute(guard.replace(marker, cases + marker, 1))
    else:
        if cases not in guard:
            raise RuntimeError("catalog audit cases missing")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    _change(True)


def downgrade() -> None:
    _change(False)
