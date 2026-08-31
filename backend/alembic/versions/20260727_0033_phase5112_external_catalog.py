"""Add Phase 5.11.2 external catalog metadata synchronization.

Revision ID: 20260727_0033
Revises: 20260725_0032
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_0033"
down_revision = "20260725_0032"
branch_labels = None
depends_on = None

SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())
EVENTS = (
    "external_catalog.sync.succeeded",
    "external_catalog.sync.not_modified",
    "external_catalog.sync.failed",
)
SUBJECT = "external_catalog_sync_run"


def _values(name: str, field: str) -> list[str]:
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
    start = definition.index("ARRAY[") + 6
    end = definition.index("])", start)
    return [
        item.strip().split("::", 1)[0].strip().strip("'")
        for item in definition[start:end].split(",")
        if item.strip()
    ]


def _replace_check(name: str, field: str, values: list[str]) -> None:
    rendered = ",".join(f"'{value}'" for value in values)
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        f"ALTER TABLE medtrust.audit_events ADD CONSTRAINT {name} "
        f"CHECK ({field} IN ({rendered}))"
    )


def _extend_audit(enable: bool) -> None:
    event_constraint = "ck_audit_events_ck_audit_events_event_type"
    subject_constraint = "ck_audit_events_ck_audit_events_subject_type"
    event_values = _values(event_constraint, "event_type")
    subject_values = _values(subject_constraint, "subject_type")
    if enable:
        event_values.extend(value for value in EVENTS if value not in event_values)
        if SUBJECT not in subject_values:
            subject_values.append(SUBJECT)
    else:
        event_values = [value for value in event_values if value not in EVENTS]
        subject_values = [value for value in subject_values if value != SUBJECT]
    _replace_check(event_constraint, "event_type", event_values)
    _replace_check(subject_constraint, "subject_type", subject_values)

    connection = op.get_bind()
    guard = connection.execute(
        sa.text(
            "SELECT pg_get_functiondef('medtrust.guard_audit_event_v8()'::regprocedure)"
        )
    ).scalar_one()
    cases = "".join(
        f"""
                WHEN '{event}' THEN
                    IF NEW.subject_type<>'external_catalog_sync_run' OR NEW.result<>'{'failure' if event.endswith('.failed') else 'success'}' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(
                        SELECT 1 FROM medtrust.external_catalog_sync_runs r
                        JOIN medtrust.external_catalog_sources s ON s.id=r.source_id
                        WHERE r.id=NEW.subject_id AND s.space_id=NEW.space_id
                    ) INTO v_subject_ok;
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
            raise RuntimeError("external catalog audit guard cases missing")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    op.create_table(
        "external_catalog_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("auth_mode", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("expected_schema_version", sa.String(32), nullable=False),
        sa.Column("last_successful_catalog_version", sa.Text()),
        sa.Column("last_successful_etag", sa.Text()),
        sa.Column("last_successful_digest", sa.String(64)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), server_default="ready", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("auth_mode = 'none'", name="ck_external_catalog_sources_auth_mode_none"),
        sa.ForeignKeyConstraint(["space_id"], [f"{SCHEMA}.spaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_code"),
        sa.UniqueConstraint("space_id", "source_code", name="uq_external_catalog_source_space_code"),
        schema=SCHEMA,
    )
    op.create_index("ix_external_catalog_sources_space_status", "external_catalog_sources", ["space_id", "status"], schema=SCHEMA)
    op.create_table(
        "external_catalog_sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("request_etag", sa.Text()),
        sa.Column("response_etag", sa.Text()),
        sa.Column("http_status", sa.Integer()),
        sa.Column("schema_version", sa.Text()),
        sa.Column("catalog_version", sa.Text()),
        sa.Column("expected_record_count", sa.Integer()),
        sa.Column("received_record_count", sa.Integer()),
        sa.Column("manifest_digest", sa.String(64)),
        sa.Column("datasets_digest", sa.String(64)),
        sa.Column("inserted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stale_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_summary", sa.Text()),
        sa.CheckConstraint(
            "status IN ('created','fetching_manifest','not_modified','validating','applying','succeeded','failed')",
            name="ck_external_catalog_sync_runs_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], [f"{SCHEMA}.external_catalog_sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_external_catalog_sync_runs_source_started", "external_catalog_sync_runs", ["source_id", "started_at"], schema=SCHEMA)
    op.create_index(
        "uq_external_catalog_sync_runs_active",
        "external_catalog_sync_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('created','fetching_manifest','validating','applying')"),
        schema=SCHEMA,
    )
    op.create_table(
        "external_dataset_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("display_name_cn", sa.Text()),
        sa.Column("display_name_en", sa.Text()),
        sa.Column("source_catalog", sa.Text(), nullable=False),
        sa.Column("official_source_name", sa.Text()),
        sa.Column("official_source_url", sa.Text()),
        sa.Column("catalog_source_url", sa.Text()),
        sa.Column("modalities", JSONB, nullable=False),
        sa.Column("disease_areas", JSONB, nullable=False),
        sa.Column("organs", JSONB, nullable=False),
        sa.Column("task_types", JSONB, nullable=False),
        sa.Column("species", sa.Text()),
        sa.Column("sample_count", sa.Integer()),
        sa.Column("patient_count", sa.Integer()),
        sa.Column("file_count", sa.Integer()),
        sa.Column("approximate_size_bytes", sa.Integer()),
        sa.Column("data_formats", JSONB, nullable=False),
        sa.Column("license_name", sa.Text()),
        sa.Column("license_url", sa.Text()),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("access_level", sa.String(32), nullable=False),
        sa.Column("registration_required", sa.Boolean()),
        sa.Column("dataset_version", sa.Text()),
        sa.Column("upstream_updated_at", sa.DateTime(timezone=True)),
        sa.Column("link_status", sa.String(48), nullable=False),
        sa.Column("quality_flags", JSONB, nullable=False),
        sa.Column("duplicate_group_id", sa.Text()),
        sa.Column("raw_record_digest", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("status IN ('active','stale')", name="ck_external_dataset_records_status"),
        sa.ForeignKeyConstraint(["source_id"], [f"{SCHEMA}.external_catalog_sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_external_dataset_source_external"),
        schema=SCHEMA,
    )
    op.create_table(
        "external_dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
        sa.Column("normalized_payload", JSONB, nullable=False),
        sa.Column("source_payload", JSONB, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], [f"{SCHEMA}.external_dataset_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", "record_digest", name="uq_external_dataset_version_digest"),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_external_dataset_records_current_version",
        "external_dataset_records",
        "external_dataset_versions",
        ["current_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
        use_alter=True,
    )
    op.create_index("ix_external_dataset_records_source_status", "external_dataset_records", ["source_id", "status"], schema=SCHEMA)
    op.create_index("ix_external_dataset_records_license", "external_dataset_records", ["license_status"], schema=SCHEMA)
    op.create_index("ix_external_dataset_records_link", "external_dataset_records", ["link_status"], schema=SCHEMA)
    op.create_index("ix_external_dataset_records_modalities", "external_dataset_records", ["modalities"], schema=SCHEMA, postgresql_using="gin")
    op.create_index("ix_external_dataset_records_disease_areas", "external_dataset_records", ["disease_areas"], schema=SCHEMA, postgresql_using="gin")
    op.create_index("ix_external_dataset_versions_record_current", "external_dataset_versions", ["record_id", "is_current"], schema=SCHEMA)
    _extend_audit(True)


def downgrade() -> None:
    _extend_audit(False)
    op.drop_constraint(
        "fk_external_dataset_records_current_version",
        "external_dataset_records",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("external_dataset_versions", schema=SCHEMA)
    op.drop_table("external_dataset_records", schema=SCHEMA)
    op.drop_table("external_catalog_sync_runs", schema=SCHEMA)
    op.drop_table("external_catalog_sources", schema=SCHEMA)
