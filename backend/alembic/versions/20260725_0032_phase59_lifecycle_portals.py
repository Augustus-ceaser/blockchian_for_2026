"""Add Phase 5.9 lifecycle governance and local portal sessions.

Revision ID: 20260725_0032
Revises: 20260725_0031
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260725_0032"
down_revision: str | None = "20260725_0031"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())
NEW_SUBJECT_TYPES = ("product_lifecycle_request",)
NEW_EVENT_TYPES = (
    "data_product.unpublish.requested",
    "data_product.unpublish.approved",
    "data_product.unpublish.rejected",
    "data_product.unpublish.returned",
    "data_product.unpublished",
    "data_product.relist.requested",
    "data_product.relist.approved",
    "data_product.relist.rejected",
    "data_product.relist.returned",
    "data_product.republished",
    "data_product.deletion.requested",
    "data_product.deletion.approved",
    "data_product.deletion.rejected",
    "data_product.deletion.returned",
    "data_product.archived",
    "model_product.unpublish.requested",
    "model_product.unpublish.approved",
    "model_product.unpublish.rejected",
    "model_product.unpublish.returned",
    "model_product.unpublished",
    "model_product.relist.requested",
    "model_product.relist.approved",
    "model_product.relist.rejected",
    "model_product.relist.returned",
    "model_product.republished",
    "model_product.deletion.requested",
    "model_product.deletion.approved",
    "model_product.deletion.rejected",
    "model_product.deletion.returned",
    "model_product.archived",
    "product.lifecycle.cancelled",
)


def _constraint_values(connection, constraint_name: str, field: str) -> list[str]:
    definition = connection.execute(
        sa.text(
            """
            SELECT pg_get_constraintdef(c.oid)
              FROM pg_constraint c
              JOIN pg_class t ON t.oid=c.conrelid
              JOIN pg_namespace n ON n.oid=t.relnamespace
             WHERE n.nspname='medtrust' AND t.relname='audit_events'
               AND c.conname=:constraint_name
            """
        ),
        {"constraint_name": constraint_name},
    ).scalar_one()
    if "ARRAY[" in definition:
        start = definition.index("ARRAY[") + len("ARRAY[")
        end = definition.index("])", start)
    else:
        marker = f"{field} IN ("
        start = definition.index(marker) + len(marker)
        end = definition.index(")", start)
    return [
        item.strip().split("::", 1)[0].strip().strip("'")
        for item in definition[start:end].split(",")
        if item.strip()
    ]


def _replace_check(name: str, field: str, values: list[str]) -> None:
    rendered = ",".join(f"'{value}'" for value in values)
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        "ALTER TABLE medtrust.audit_events "
        f"ADD CONSTRAINT {name} CHECK ({field} IN ({rendered}))"
    )


def _function_definition(connection, name: str) -> str:
    return connection.execute(
        sa.text(f"SELECT pg_get_functiondef('medtrust.{name}()'::regprocedure)")
    ).scalar_one()


def _audit_cases() -> str:
    return "".join(
        f"""
                WHEN '{event_type}' THEN
                    IF NEW.subject_type<>'product_lifecycle_request' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.product_lifecycle_requests r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
"""
        for event_type in NEW_EVENT_TYPES
    )


def _extend_audit(enable: bool) -> None:
    connection = op.get_bind()
    event_name = "ck_audit_events_ck_audit_events_event_type"
    subject_name = "ck_audit_events_ck_audit_events_subject_type"
    events = _constraint_values(connection, event_name, "event_type")
    subjects = _constraint_values(connection, subject_name, "subject_type")
    if enable:
        events.extend(value for value in NEW_EVENT_TYPES if value not in events)
        subjects.extend(value for value in NEW_SUBJECT_TYPES if value not in subjects)
    else:
        events = [value for value in events if value not in NEW_EVENT_TYPES]
        subjects = [value for value in subjects if value not in NEW_SUBJECT_TYPES]
    _replace_check(event_name, "event_type", events)
    _replace_check(subject_name, "subject_type", subjects)
    guard = _function_definition(connection, "guard_audit_event_v8")
    cases = _audit_cases()
    marker = "                WHEN 'contract.revision.proposed' THEN"
    if enable:
        if marker not in guard:
            raise RuntimeError("expected audit guard insertion marker was not found")
        op.execute(guard.replace(marker, cases + marker, 1))
    else:
        if cases not in guard:
            raise RuntimeError("expected Phase 5.9 audit guard cases were not found")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    op.add_column("data_products", sa.Column("unpublished_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("data_products", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("model_products", sa.Column("unpublished_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("model_products", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.execute("ALTER TABLE medtrust.data_products DROP CONSTRAINT ck_data_products_lifecycle_status")
    op.execute(
        "ALTER TABLE medtrust.data_products ADD CONSTRAINT "
        "ck_data_products_lifecycle_status CHECK "
        "(lifecycle_status IN ('draft','active','suspended','unpublished','expired','archived'))"
    )
    op.execute(
        "ALTER TABLE medtrust.model_products DROP CONSTRAINT "
        "ck_model_products_ck_model_products_lifecycle_status"
    )
    op.execute(
        "ALTER TABLE medtrust.model_products ADD CONSTRAINT "
        "ck_model_products_ck_model_products_lifecycle_status CHECK "
        "(lifecycle_status IN ('draft','active','suspended','unpublished','archived'))"
    )
    op.drop_constraint(
        "uq_model_publications_version",
        "model_publications",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_table(
        "local_demo_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username"),
        schema=SCHEMA,
    )
    op.create_table(
        "local_demo_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_digest", sa.String(71), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(session_digest)=71 AND substr(session_digest,1,7)='sha256:'", name="ck_local_demo_sessions_session_digest_format"),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_digest"),
        schema=SCHEMA,
    )
    op.create_index("ix_local_demo_sessions_user_active", "local_demo_sessions", ["user_id", "expires_at"], schema=SCHEMA)
    op.create_table(
        "product_lifecycle_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(24), nullable=False),
        sa.Column("target_product_id", sa.Uuid(), nullable=False),
        sa.Column("target_version_id", sa.Uuid()),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_organization_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("impact_snapshot", JSONB, nullable=False),
        sa.Column("impact_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_comment", sa.Text()),
        sa.Column("decision", sa.String(16)),
        sa.Column("idempotency_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("target_type IN ('data_product','model_product')", name="ck_product_lifecycle_requests_target_type"),
        sa.CheckConstraint("action IN ('unpublish','relist','archive')", name="ck_product_lifecycle_requests_action"),
        sa.CheckConstraint("status IN ('pending','approved','rejected','returned','cancelled')", name="ck_product_lifecycle_requests_status"),
        sa.CheckConstraint("length(impact_digest)=71 AND substr(impact_digest,1,7)='sha256:'", name="ck_product_lifecycle_requests_impact_digest"),
        sa.CheckConstraint("length(idempotency_digest)=71 AND substr(idempotency_digest,1,7)='sha256:'", name="ck_product_lifecycle_requests_idempotency_digest"),
        sa.CheckConstraint("row_version >= 1", name="ck_product_lifecycle_requests_row_version"),
        sa.ForeignKeyConstraint(["space_id"], [f"{SCHEMA}.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_product_lifecycle_requests_open_target",
        "product_lifecycle_requests",
        ["space_id", "target_type", "target_product_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        schema=SCHEMA,
    )
    op.create_index("ix_product_lifecycle_requests_queue", "product_lifecycle_requests", ["space_id", "status", "requested_at"], schema=SCHEMA)
    op.create_index("ix_product_lifecycle_requests_owner", "product_lifecycle_requests", ["requested_by_organization_id", "status"], schema=SCHEMA)
    _extend_audit(True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT count(*) FROM medtrust.audit_events WHERE event_type = ANY(:events)"),
        {"events": list(NEW_EVENT_TYPES)},
    ).scalar_one():
        raise RuntimeError("cannot remove Phase 5.9 while lifecycle audit evidence exists")
    _extend_audit(False)
    op.drop_table("product_lifecycle_requests", schema=SCHEMA)
    op.drop_table("local_demo_sessions", schema=SCHEMA)
    op.drop_table("local_demo_credentials", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_model_publications_version",
        "model_publications",
        ["model_product_id", "model_version_id"],
        schema=SCHEMA,
    )
    op.execute(
        "ALTER TABLE medtrust.model_products DROP CONSTRAINT "
        "ck_model_products_ck_model_products_lifecycle_status"
    )
    op.execute(
        "ALTER TABLE medtrust.model_products ADD CONSTRAINT "
        "ck_model_products_ck_model_products_lifecycle_status CHECK "
        "(lifecycle_status IN ('draft','active','suspended','archived'))"
    )
    op.execute("ALTER TABLE medtrust.data_products DROP CONSTRAINT ck_data_products_lifecycle_status")
    op.execute(
        "ALTER TABLE medtrust.data_products ADD CONSTRAINT "
        "ck_data_products_lifecycle_status CHECK "
        "(lifecycle_status IN ('draft','active','suspended','expired','archived'))"
    )
    op.drop_column("model_products", "deleted_at", schema=SCHEMA)
    op.drop_column("model_products", "unpublished_at", schema=SCHEMA)
    op.drop_column("data_products", "deleted_at", schema=SCHEMA)
    op.drop_column("data_products", "unpublished_at", schema=SCHEMA)
