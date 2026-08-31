"""Add the independent metadata catalog curator role.

Revision ID: 20260727_0041
Revises: 20260727_0040
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0041"
down_revision = "20260727_0040"
branch_labels = None
depends_on = None

EVENTS = (
    "external_catalog.product.submitted",
    "external_catalog.product.published",
    "external_catalog.product.publication.rejected",
)


def _event_values() -> list[str]:
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


def _change_audit_contract(enable: bool) -> None:
    values = _event_values()
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
        sa.text(
            "SELECT pg_get_functiondef("
            "'medtrust.guard_audit_event_v8()'::regprocedure)"
        )
    ).scalar_one()
    cases = "".join(
        f"""
                WHEN '{event}' THEN
                    IF NEW.subject_type<>'data_product_version' THEN RAISE EXCEPTION 'invalid external product publication audit shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.data_product_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
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
            raise RuntimeError("external publication audit cases missing")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        schema="medtrust",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        "role_code IN ('provider','consumer','service_provider','operator',"
        "'space_operator','data_provider','model_provider','data_requester',"
        "'catalog_curator')",
        schema="medtrust",
    )
    _change_audit_contract(True)


def downgrade() -> None:
    _change_audit_contract(False)
    op.drop_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        schema="medtrust",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        "role_code IN ('provider','consumer','service_provider','operator',"
        "'space_operator','data_provider','model_provider','data_requester')",
        schema="medtrust",
    )
