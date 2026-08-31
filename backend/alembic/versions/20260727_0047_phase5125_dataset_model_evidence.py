"""Phase 5.12.5 dataset-model evidence graph.

Revision ID: 20260727_0047
Revises: 20260727_0046
"""

from alembic import op
import re
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_0047"
down_revision = "20260727_0046"
branch_labels = None
depends_on = None
SCHEMA = "medtrust"
EVENTS = (
    "dataset_model_relation.created",
    "dataset_model_evidence.created",
    "dataset_model_evidence.superseded",
    "dataset_model_relation.status_changed",
    "dataset_model_relation.publication_changed",
)


def _constraint_values(name: str) -> list[str]:
    definition = op.get_bind().execute(sa.text(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname='medtrust' AND t.relname='audit_events' AND c.conname=:name"
    ), {"name": name}).scalar_one()
    return list(dict.fromkeys(re.findall(r"'([^']+)'", definition)))


def _replace_values(name: str, column: str, values: list[str]) -> None:
    rendered = ",".join(repr(value) for value in values)
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        f"ALTER TABLE medtrust.audit_events ADD CONSTRAINT {name} "
        f"CHECK ({column} IN ({rendered}))"
    )


def _change_audit(enable: bool) -> None:
    event_name = "ck_audit_events_ck_audit_events_event_type"
    subject_name = "ck_audit_events_ck_audit_events_subject_type"
    events = _constraint_values(event_name)
    subjects = _constraint_values(subject_name)
    if enable:
        events.extend(value for value in EVENTS if value not in events)
        if "dataset_model_relation" not in subjects:
            subjects.append("dataset_model_relation")
    else:
        events = [value for value in events if value not in EVENTS]
        subjects = [value for value in subjects if value != "dataset_model_relation"]
    _replace_values(event_name, "event_type", events)
    _replace_values(subject_name, "subject_type", subjects)
    guard = op.get_bind().execute(sa.text(
        "SELECT pg_get_functiondef('medtrust.guard_audit_event_v8()'::regprocedure)"
    )).scalar_one()
    cases = "".join(
        f"""
                WHEN '{event}' THEN
                    IF NEW.subject_type<>'dataset_model_relation' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid dataset-model event shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.dataset_model_relations r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
"""
        for event in EVENTS
    )
    marker = "                WHEN 'contract.revision.proposed' THEN"
    op.execute(
        guard.replace(marker, cases + marker, 1)
        if enable else guard.replace(cases, "", 1)
    )


def upgrade() -> None:
    op.create_table(
        "dataset_model_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("model_product_id", sa.Uuid(), nullable=False),
        sa.Column("model_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("current_status", sa.String(48), nullable=False),
        sa.Column("strongest_evidence_level", sa.String(32), nullable=False),
        sa.Column("current_evidence_id", sa.Uuid()),
        sa.Column("data_source_link_id", sa.Uuid(), nullable=False),
        sa.Column("model_source_link_id", sa.Uuid(), nullable=False),
        sa.Column("data_version_digest", sa.String(71), nullable=False),
        sa.Column("model_version_digest", sa.String(71), nullable=False),
        sa.Column("data_source_digest", sa.String(64), nullable=False),
        sa.Column("model_source_digest", sa.String(64), nullable=False),
        sa.Column("data_governance_digest", sa.String(71), nullable=False),
        sa.Column("model_governance_digest", sa.String(71), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("public_visible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["space_id"], [f"{SCHEMA}.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["data_product_id"], [f"{SCHEMA}.data_products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["data_product_version_id"], [f"{SCHEMA}.data_product_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_product_id"], [f"{SCHEMA}.model_products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_product_version_id"], [f"{SCHEMA}.model_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["data_source_link_id"], [f"{SCHEMA}.data_product_external_source_links.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_source_link_id"], [f"{SCHEMA}.model_product_external_source_links.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "current_status IN ('not_assessed','external_declaration_only','static_schema_compatible',"
            "'static_schema_compatible_with_transformation','static_schema_incompatible',"
            "'insufficient_metadata','executed','execution_failed','verified','superseded','archived')",
            name="ck_dataset_model_relations_status",
        ),
        sa.CheckConstraint(
            "strongest_evidence_level IN ('none','external_declaration','platform_static_review',"
            "'runtime_execution','platform_verification')",
            name="ck_dataset_model_relations_level",
        ),
        sa.UniqueConstraint("data_product_version_id", "model_product_version_id",
                            name="uq_dataset_model_relation_version_pair"),
        schema=SCHEMA,
    )
    op.create_index("ix_dataset_model_relation_data", "dataset_model_relations",
                    ["data_product_id", "public_visible"], schema=SCHEMA)
    op.create_index("ix_dataset_model_relation_model", "dataset_model_relations",
                    ["model_product_id", "public_visible"], schema=SCHEMA)
    op.create_index("ix_dataset_model_relation_status", "dataset_model_relations",
                    ["space_id", "current_status", "active"], schema=SCHEMA)
    op.create_table(
        "dataset_model_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("relation_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_level", sa.String(32), nullable=False),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("evidence_scope", sa.String(32), nullable=False),
        sa.Column("evidence_reference", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_note", sa.String(2000), nullable=False),
        sa.Column("structured_assessment", postgresql.JSONB(), nullable=False),
        sa.Column("transformation_requirements", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("blocking_reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("warning_reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("model_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("data_version_digest", sa.String(71), nullable=False),
        sa.Column("model_version_digest", sa.String(71), nullable=False),
        sa.Column("data_source_digest", sa.String(64), nullable=False),
        sa.Column("model_source_digest", sa.String(64), nullable=False),
        sa.Column("data_governance_digest", sa.String(71), nullable=False),
        sa.Column("model_governance_digest", sa.String(71), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_organization_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_digest", sa.String(71), nullable=False),
        sa.Column("idempotency_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("supersedes_evidence_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["relation_id"], [f"{SCHEMA}.dataset_model_relations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["data_product_version_id"], [f"{SCHEMA}.data_product_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_product_version_id"], [f"{SCHEMA}.model_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_evidence_id"], [f"{SCHEMA}.dataset_model_evidence.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "evidence_level IN ('external_declaration','platform_static_review','runtime_execution','platform_verification')",
            name="ck_dataset_model_evidence_level",
        ),
        sa.CheckConstraint(
            "evidence_type IN ('author_declared_training','author_declared_evaluation','author_declared_benchmark',"
            "'external_related_reference','static_schema_compatible','static_schema_compatible_with_transformation',"
            "'static_schema_incompatible','insufficient_metadata','executed','execution_failed','verified')",
            name="ck_dataset_model_evidence_type",
        ),
        sa.CheckConstraint("outcome IN ('supports','contradicts','inconclusive')",
                           name="ck_dataset_model_evidence_outcome"),
        sa.CheckConstraint(
            "evidence_scope IN ('training','evaluation','benchmark','input_schema',"
            "'preprocessing','task','modality','format','resolution','label_schema',"
            "'runtime','verification')",
            name="ck_dataset_model_evidence_scope",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_dataset_model_evidence_relation", "dataset_model_evidence",
                    ["relation_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_dataset_model_evidence_level", "dataset_model_evidence",
                    ["evidence_level", "evidence_type"], schema=SCHEMA)
    op.execute("""
        CREATE FUNCTION medtrust.guard_dataset_model_evidence_v1() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'dataset-model evidence is append-only' USING ERRCODE='55000';
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_dataset_model_evidence_immutable
        BEFORE UPDATE OR DELETE ON medtrust.dataset_model_evidence
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_dataset_model_evidence_v1();
    """)
    op.execute("""
        CREATE FUNCTION medtrust.guard_dataset_model_relation_locks_v1() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.space_id IS DISTINCT FROM OLD.space_id
              OR NEW.data_product_id IS DISTINCT FROM OLD.data_product_id
              OR NEW.data_product_version_id IS DISTINCT FROM OLD.data_product_version_id
              OR NEW.model_product_id IS DISTINCT FROM OLD.model_product_id
              OR NEW.model_product_version_id IS DISTINCT FROM OLD.model_product_version_id
              OR NEW.data_source_link_id IS DISTINCT FROM OLD.data_source_link_id
              OR NEW.model_source_link_id IS DISTINCT FROM OLD.model_source_link_id
              OR NEW.data_version_digest IS DISTINCT FROM OLD.data_version_digest
              OR NEW.model_version_digest IS DISTINCT FROM OLD.model_version_digest
              OR NEW.data_source_digest IS DISTINCT FROM OLD.data_source_digest
              OR NEW.model_source_digest IS DISTINCT FROM OLD.model_source_digest
              OR NEW.data_governance_digest IS DISTINCT FROM OLD.data_governance_digest
              OR NEW.model_governance_digest IS DISTINCT FROM OLD.model_governance_digest
              OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
              RAISE EXCEPTION 'dataset-model relation version locks are immutable' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_dataset_model_relation_locks
        BEFORE UPDATE ON medtrust.dataset_model_relations
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_dataset_model_relation_locks_v1();
    """)
    _change_audit(True)


def downgrade() -> None:
    _change_audit(False)
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_model_relation_locks ON medtrust.dataset_model_relations")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_dataset_model_relation_locks_v1()")
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_model_evidence_immutable ON medtrust.dataset_model_evidence")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_dataset_model_evidence_v1()")
    op.drop_index("ix_dataset_model_evidence_level", table_name="dataset_model_evidence", schema=SCHEMA)
    op.drop_index("ix_dataset_model_evidence_relation", table_name="dataset_model_evidence", schema=SCHEMA)
    op.drop_table("dataset_model_evidence", schema=SCHEMA)
    op.drop_index("ix_dataset_model_relation_status", table_name="dataset_model_relations", schema=SCHEMA)
    op.drop_index("ix_dataset_model_relation_model", table_name="dataset_model_relations", schema=SCHEMA)
    op.drop_index("ix_dataset_model_relation_data", table_name="dataset_model_relations", schema=SCHEMA)
    op.drop_table("dataset_model_relations", schema=SCHEMA)
