"""Phase 5.12.4 external model metadata publication review.

Revision ID: 20260727_0045
Revises: 20260727_0044
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0045"
down_revision = "20260727_0044"
branch_labels = None
depends_on = None
SCHEMA = "medtrust"
EVENTS = (
    "external_model_catalog.product.submitted",
    "external_model_catalog.product.published",
    "external_model_catalog.product.publication.rejected",
)


def _audit_values() -> list[str]:
    definition = op.get_bind().execute(sa.text(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname='medtrust' AND t.relname='audit_events' "
        "AND c.conname='ck_audit_events_ck_audit_events_event_type'"
    )).scalar_one()
    start = definition.index("ARRAY[") + 6
    end = definition.index("])", start)
    return [
        item.strip().split("::", 1)[0].strip().strip("'")
        for item in definition[start:end].split(",")
        if item.strip()
    ]


def _replace_audit_values(values: list[str]) -> None:
    rendered = ",".join(repr(value) for value in values)
    op.execute(
        "ALTER TABLE medtrust.audit_events DROP CONSTRAINT "
        "ck_audit_events_ck_audit_events_event_type"
    )
    op.execute(
        "ALTER TABLE medtrust.audit_events ADD CONSTRAINT "
        "ck_audit_events_ck_audit_events_event_type "
        f"CHECK (event_type IN ({rendered}))"
    )


def _change_audit(enable: bool) -> None:
    values = _audit_values()
    if enable:
        values.extend(value for value in EVENTS if value not in values)
    else:
        values = [value for value in values if value not in EVENTS]
    _replace_audit_values(values)
    guard = op.get_bind().execute(sa.text(
        "SELECT pg_get_functiondef('medtrust.guard_audit_event_v8()'::regprocedure)"
    )).scalar_one()
    cases = """
                WHEN 'external_model_catalog.product.submitted' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid external model submit event shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'external_model_catalog.product.published' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid external model publish event shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'external_model_catalog.product.publication.rejected' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'denied' THEN RAISE EXCEPTION 'invalid external model return event shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
"""
    marker = "                WHEN 'contract.revision.proposed' THEN"
    op.execute(
        guard.replace(marker, cases + marker, 1)
        if enable
        else guard.replace(cases, "", 1)
    )


def upgrade() -> None:
    op.create_table(
        "model_metadata_publication_review_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("model_product_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("external_source_link_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("task_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decision", sa.String(16)),
        sa.Column("submission_digest", sa.String(71), nullable=False),
        sa.Column("review_digest", sa.String(71)),
        sa.Column("submitter_organization_id", sa.Uuid(), nullable=False),
        sa.Column("submitter_user_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_organization_id", sa.Uuid()),
        sa.Column("reviewer_user_id", sa.Uuid()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["space_id"], [f"{SCHEMA}.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_product_id"], [f"{SCHEMA}.model_products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_version_id"], [f"{SCHEMA}.model_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["external_source_link_id"], [f"{SCHEMA}.model_product_external_source_links.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submitter_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submitter_user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("sequence_no > 0", name="ck_model_metadata_publication_review_tasks_sequence_positive"),
        sa.CheckConstraint("task_status IN ('pending','decided')", name="ck_model_metadata_publication_review_tasks_task_status"),
        sa.CheckConstraint("decision IS NULL OR decision IN ('approved','returned')", name="ck_model_metadata_publication_review_tasks_decision"),
        sa.CheckConstraint(
            "(task_status='pending' AND decision IS NULL AND review_digest IS NULL AND reviewer_organization_id IS NULL AND reviewer_user_id IS NULL AND decided_at IS NULL) OR "
            "(task_status='decided' AND decision IS NOT NULL AND review_digest IS NOT NULL AND reviewer_organization_id IS NOT NULL AND reviewer_user_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_model_metadata_publication_review_tasks_lifecycle_shape",
        ),
        sa.UniqueConstraint("model_version_id", "sequence_no", name="uq_model_metadata_review_version_sequence"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_model_metadata_review_space_status",
        "model_metadata_publication_review_tasks",
        ["space_id", "task_status", "submitted_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_model_metadata_review_version",
        "model_metadata_publication_review_tasks",
        ["model_version_id", "sequence_no"],
        schema=SCHEMA,
    )
    _change_audit(True)


def downgrade() -> None:
    _change_audit(False)
    op.drop_index(
        "ix_model_metadata_review_version",
        table_name="model_metadata_publication_review_tasks",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_model_metadata_review_space_status",
        table_name="model_metadata_publication_review_tasks",
        schema=SCHEMA,
    )
    op.drop_table("model_metadata_publication_review_tasks", schema=SCHEMA)
