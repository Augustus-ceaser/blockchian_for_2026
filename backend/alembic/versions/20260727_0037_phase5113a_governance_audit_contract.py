"""Add the formal Phase 5.11.3A governance audit contract names.

Revision ID: 20260727_0037
Revises: 20260727_0036
"""

from alembic import op
import sqlalchemy as sa

revision = "20260727_0037"
down_revision = "20260727_0036"
branch_labels = None
depends_on = None

EVENTS = (
    "external_catalog.governance.profile.initialized",
    "external_catalog.duplicate.resolved",
    "external_catalog.productization.eligibility.changed",
)


def _values() -> list[str]:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname='medtrust' AND t.relname='audit_events' "
            "AND c.conname='ck_audit_events_ck_audit_events_event_type'"
        )
    ).scalar_one()
    start = definition.index("ARRAY[") + 6
    end = definition.index("])", start)
    return [
        item.strip().split("::", 1)[0].strip().strip("'")
        for item in definition[start:end].split(",")
        if item.strip()
    ]


def _change(enable: bool) -> None:
    values = _values()
    if enable:
        values.extend(value for value in EVENTS if value not in values)
    else:
        values = [value for value in values if value not in EVENTS]
    rendered = ",".join(f"'{value}'" for value in values)
    op.execute(
        "ALTER TABLE medtrust.audit_events DROP CONSTRAINT "
        "ck_audit_events_ck_audit_events_event_type"
    )
    op.execute(
        "ALTER TABLE medtrust.audit_events ADD CONSTRAINT "
        "ck_audit_events_ck_audit_events_event_type "
        f"CHECK (event_type IN ({rendered}))"
    )

    guard = op.get_bind().execute(
        sa.text("SELECT pg_get_functiondef('medtrust.guard_audit_event_v8()'::regprocedure)")
    ).scalar_one()
    cases = "".join(
        f"""
                WHEN '{event}' THEN
                    IF NEW.subject_type<>'external_catalog_source' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog governance shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.external_catalog_sources s WHERE s.id=NEW.subject_id AND s.space_id=NEW.space_id) INTO v_subject_ok;
"""
        for event in EVENTS
    )
    marker = "                WHEN 'contract.revision.proposed' THEN"
    if enable:
        if marker not in guard:
            raise RuntimeError("audit guard insertion marker missing")
        op.execute(guard.replace(marker, cases + marker, 1))
    else:
        if cases not in guard:
            raise RuntimeError("governance audit contract cases missing")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    _change(True)


def downgrade() -> None:
    _change(False)
