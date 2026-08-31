"""Allow native reference-pair evidence backfill.

Revision ID: 20260728_0049
Revises: 20260728_0048
"""

import re

from alembic import op
import sqlalchemy as sa

revision = "20260728_0049"
down_revision = "20260728_0048"
branch_labels = None
depends_on = None

EVENTS = (
    "dataset_model_evidence.execution_backfilled",
    "dataset_model_evidence.verification_backfilled",
)


def _constraint_values(name: str) -> list[str]:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname='medtrust' AND t.relname='audit_events' "
            "AND c.conname=:name"
        ),
        {"name": name},
    ).scalar_one()
    return list(dict.fromkeys(re.findall(r"'([^']+)'", definition)))


def _replace_events(values: list[str]) -> None:
    name = "ck_audit_events_ck_audit_events_event_type"
    rendered = ",".join(repr(value) for value in values)
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        f"ALTER TABLE medtrust.audit_events ADD CONSTRAINT {name} "
        f"CHECK (event_type IN ({rendered}))"
    )


def _change_audit(enable: bool) -> None:
    name = "ck_audit_events_ck_audit_events_event_type"
    values = _constraint_values(name)
    if enable:
        values.extend(event for event in EVENTS if event not in values)
    else:
        values = [event for event in values if event not in EVENTS]
    _replace_events(values)

    guard = op.get_bind().execute(
        sa.text(
            "SELECT pg_get_functiondef("
            "'medtrust.guard_audit_event_v8()'::regprocedure)"
        )
    ).scalar_one()
    cases = "".join(
        f"""
                WHEN '{event}' THEN
                    IF NEW.subject_type<>'dataset_model_relation'
                      OR NEW.result<>'success' THEN
                      RAISE EXCEPTION 'invalid reference evidence backfill event shape'
                        USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(
                      SELECT 1 FROM medtrust.dataset_model_relations r
                      WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id
                    ) INTO v_subject_ok;
"""
        for event in EVENTS
    )
    marker = "                WHEN 'contract.revision.proposed' THEN"
    op.execute(
        guard.replace(marker, cases + marker, 1)
        if enable
        else guard.replace(cases, "", 1)
    )


def _nullable(nullable: bool) -> None:
    for table in ("dataset_model_relations", "dataset_model_evidence"):
        for column, type_ in (
            ("data_source_digest", sa.String(64)),
            ("model_source_digest", sa.String(64)),
            ("data_governance_digest", sa.String(71)),
            ("model_governance_digest", sa.String(71)),
        ):
            op.alter_column(
                table, column, schema="medtrust",
                existing_type=type_, nullable=nullable,
            )
    for column in ("data_source_link_id", "model_source_link_id"):
        op.alter_column(
            "dataset_model_relations", column, schema="medtrust",
            existing_type=sa.Uuid(), nullable=nullable,
        )


def upgrade() -> None:
    _nullable(True)
    _change_audit(True)


def downgrade() -> None:
    native_count = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM medtrust.dataset_model_relations "
            "WHERE data_source_link_id IS NULL OR model_source_link_id IS NULL"
        )
    ).scalar_one()
    if native_count:
        raise RuntimeError(
            "cannot downgrade while native reference evidence relations exist"
        )
    _change_audit(False)
    _nullable(False)
