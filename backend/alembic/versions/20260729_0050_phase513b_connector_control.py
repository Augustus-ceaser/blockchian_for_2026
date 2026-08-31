"""Add Hospital Connector control-plane alpha tables.

Revision ID: 20260729_0050
Revises: 20260728_0049
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260729_0050"
down_revision = "20260728_0049"
branch_labels = None
depends_on = None

S = "medtrust"


def upgrade() -> None:
    op.create_table(
        "hospital_connectors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("space_id", sa.Uuid(), sa.ForeignKey(f"{S}.spaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey(f"{S}.organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("connector_instance_id", sa.String(80), nullable=False, unique=True),
        sa.Column("installation_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("environment", sa.String(24), nullable=False, server_default="local_test"),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("operating_system", sa.String(40), nullable=False),
        sa.Column("architecture", sa.String(24), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending_approval"),
        sa.Column("current_certificate_id", sa.Uuid()),
        sa.Column("current_capability_manifest_id", sa.Uuid()),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_heartbeat_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heartbeat_status", sa.String(16), nullable=False, server_default="never"),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("paused_by", sa.Uuid(), sa.ForeignKey(f"{S}.users.id", ondelete="RESTRICT")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.Uuid(), sa.ForeignKey(f"{S}.users.id", ondelete="RESTRICT")),
        sa.Column("revocation_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending_registration','pending_approval','pending_certificate','active','paused','offline','revoked','certificate_expired','certificate_rotation_required','error','archived')", name="ck_hospital_connectors_status"),
        sa.CheckConstraint("last_heartbeat_sequence >= 0", name="ck_hospital_connectors_heartbeat_sequence_nonnegative"),
        schema=S,
    )
    op.create_index("ix_hospital_connectors_status_heartbeat", "hospital_connectors", ["status", "last_heartbeat_at"], schema=S)
    op.create_index("ix_hospital_connectors_org_status", "hospital_connectors", ["organization_id", "status"], schema=S)

    op.create_table(
        "connector_enrollment_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("space_id", sa.Uuid(), sa.ForeignKey(f"{S}.spaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey(f"{S}.organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("connector_name", sa.String(120), nullable=False),
        sa.Column("token_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_by_connector_request_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey(f"{S}.users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.CheckConstraint("status IN ('active','consumed','expired','cancelled')", name="ck_connector_enrollment_tokens_status"),
        schema=S,
    )
    op.create_index("ix_connector_enrollment_tokens_status_expires", "connector_enrollment_tokens", ["status", "expires_at"], schema=S)

    op.create_table(
        "connector_registration_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("enrollment_token_id", sa.Uuid(), sa.ForeignKey(f"{S}.connector_enrollment_tokens.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("space_id", sa.Uuid(), sa.ForeignKey(f"{S}.spaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey(f"{S}.organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("connector_instance_id", sa.String(80), nullable=False, unique=True),
        sa.Column("installation_digest", sa.String(71), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("csr_pem", sa.LargeBinary(), nullable=False),
        sa.Column("csr_fingerprint", sa.String(71), nullable=False, unique=True),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("operating_system", sa.String(40), nullable=False),
        sa.Column("architecture", sa.String(24), nullable=False),
        sa.Column("bootstrap_manifest_digest", sa.String(71), nullable=False),
        sa.Column("nonce", sa.String(96), nullable=False, unique=True),
        sa.Column("request_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(28), nullable=False, server_default="submitted"),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey(f"{S}.users.id", ondelete="RESTRICT")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column("connector_id", sa.Uuid(), sa.ForeignKey(f"{S}.hospital_connectors.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('submitted','under_review','approved','rejected','expired','certificate_issued','consumed','superseded','cancelled')", name="ck_connector_registration_requests_status"),
        schema=S,
    )
    op.create_index("ix_connector_registration_status_created", "connector_registration_requests", ["status", "created_at"], schema=S)

    op.create_table(
        "connector_certificates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connector_id", sa.Uuid(), sa.ForeignKey(f"{S}.hospital_connectors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("serial_number", sa.String(80), nullable=False, unique=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(71), nullable=False, unique=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key_id", sa.String(80), nullable=False),
        sa.Column("certificate_pem", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="issued"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.Text()),
        sa.Column("supersedes_certificate_id", sa.Uuid(), sa.ForeignKey(f"{S}.connector_certificates.id", ondelete="RESTRICT")),
        sa.CheckConstraint("status IN ('issued','active','rotation_pending','superseded','revoked','expired')", name="ck_connector_certificates_status"),
        schema=S,
    )
    op.create_index("ix_connector_certificates_connector_status", "connector_certificates", ["connector_id", "status"], schema=S)

    op.create_table(
        "connector_capability_manifests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connector_id", sa.Uuid(), sa.ForeignKey(f"{S}.hospital_connectors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("manifest_version", sa.String(40), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("operating_system", sa.String(40), nullable=False),
        sa.Column("architecture", sa.String(24), nullable=False),
        sa.Column("capability_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("execution_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("data_transfer_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_transfer_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("local_asset_registry_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("artifact_egress_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hard_isolation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("isolation_maturity", sa.String(8), nullable=False, server_default="L1"),
        sa.Column("manifest_digest", sa.String(71), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("connector_id", "sequence", name="uq_connector_manifest_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_connector_capability_manifests_sequence_positive"),
        sa.CheckConstraint("NOT execution_enabled AND NOT data_transfer_enabled AND NOT model_transfer_enabled AND NOT local_asset_registry_enabled AND NOT artifact_egress_enabled AND NOT hard_isolation", name="ck_connector_capability_manifests_alpha_capabilities_disabled"),
        sa.CheckConstraint("isolation_maturity IN ('L0','L1')", name="ck_connector_capability_manifests_alpha_maturity"),
        schema=S,
    )
    op.create_index("ix_connector_manifest_current", "connector_capability_manifests", ["connector_id", "is_current"], schema=S)

    op.create_table(
        "connector_heartbeats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connector_id", sa.Uuid(), sa.ForeignKey(f"{S}.hospital_connectors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("capability_manifest_digest", sa.String(71), nullable=False),
        sa.Column("local_audit_head", sa.String(71), nullable=False),
        sa.Column("health_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("nonce", sa.String(96), nullable=False, unique=True),
        sa.Column("message_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("certificate_fingerprint", sa.String(71), nullable=False),
        sa.Column("acceptance_result", sa.String(40), nullable=False),
        sa.UniqueConstraint("connector_id", "sequence", name="uq_connector_heartbeat_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_connector_heartbeats_sequence_positive"),
        schema=S,
    )
    op.create_index("ix_connector_heartbeats_connector_received", "connector_heartbeats", ["connector_id", "received_at"], schema=S)

    op.create_table(
        "connector_control_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("space_id", sa.Uuid(), sa.ForeignKey(f"{S}.spaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("stream_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(48), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey(f"{S}.users.id", ondelete="RESTRICT")),
        sa.Column("actor_connector_id", sa.Uuid(), sa.ForeignKey(f"{S}.hospital_connectors.id", ondelete="RESTRICT")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("previous_event_digest", sa.String(71)),
        sa.Column("event_digest", sa.String(71), nullable=False, unique=True),
        sa.UniqueConstraint("space_id", "stream_sequence", name="uq_connector_control_audit_sequence"),
        sa.CheckConstraint("stream_sequence > 0", name="ck_connector_control_audit_events_stream_sequence_positive"),
        sa.CheckConstraint("actor_type IN ('operator','hospital_connector','system')", name="ck_connector_control_audit_events_actor_type"),
        schema=S,
    )
    op.create_index("ix_connector_control_audit_subject", "connector_control_audit_events", ["subject_type", "subject_id", "stream_sequence"], schema=S)

    op.create_foreign_key("fk_hospital_connectors_current_certificate", "hospital_connectors", "connector_certificates", ["current_certificate_id"], ["id"], source_schema=S, referent_schema=S, ondelete="RESTRICT")
    op.create_foreign_key("fk_hospital_connectors_current_manifest", "hospital_connectors", "connector_capability_manifests", ["current_capability_manifest_id"], ["id"], source_schema=S, referent_schema=S, ondelete="RESTRICT")
    op.create_foreign_key("fk_connector_tokens_used_request", "connector_enrollment_tokens", "connector_registration_requests", ["used_by_connector_request_id"], ["id"], source_schema=S, referent_schema=S, ondelete="RESTRICT")

    op.execute("""
    CREATE FUNCTION medtrust.guard_connector_control_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'connector control history is immutable' USING ERRCODE='23514'; END IF;
      IF TG_TABLE_NAME='connector_heartbeats' THEN
        RAISE EXCEPTION 'heartbeat evidence is append-only' USING ERRCODE='23514';
      ELSIF TG_TABLE_NAME='connector_control_audit_events' THEN
        RAISE EXCEPTION 'connector audit is append-only' USING ERRCODE='23514';
      ELSIF TG_TABLE_NAME='connector_capability_manifests' THEN
        IF (to_jsonb(OLD)-'is_current')<>(to_jsonb(NEW)-'is_current') THEN
          RAISE EXCEPTION 'manifest facts are immutable' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='connector_certificates' THEN
        IF (to_jsonb(OLD)-ARRAY['status','revoked_at','revocation_reason'])
           <>(to_jsonb(NEW)-ARRAY['status','revoked_at','revocation_reason']) THEN
          RAISE EXCEPTION 'certificate identity is immutable' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='connector_registration_requests' THEN
        IF OLD.status IN ('approved','rejected','certificate_issued','consumed') THEN
          RAISE EXCEPTION 'terminal registration is immutable' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='connector_enrollment_tokens' THEN
        IF OLD.status='consumed' AND NEW.status<>'consumed' THEN
          RAISE EXCEPTION 'consumed token cannot be restored' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='hospital_connectors' THEN
        IF OLD.status='revoked' AND NEW.status<>'revoked' THEN
          RAISE EXCEPTION 'revoked connector cannot be restored' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)
    for table in ("hospital_connectors", "connector_enrollment_tokens", "connector_registration_requests", "connector_certificates", "connector_capability_manifests", "connector_heartbeats", "connector_control_audit_events"):
        op.execute(f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON medtrust.{table} FOR EACH ROW EXECUTE FUNCTION medtrust.guard_connector_control_immutable()")


def downgrade() -> None:
    for table in ("connector_control_audit_events", "connector_heartbeats", "connector_capability_manifests", "connector_certificates", "connector_registration_requests", "connector_enrollment_tokens", "hospital_connectors"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON medtrust.{table}")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_connector_control_immutable()")
    op.drop_constraint("fk_connector_tokens_used_request", "connector_enrollment_tokens", schema=S)
    op.drop_constraint("fk_hospital_connectors_current_manifest", "hospital_connectors", schema=S)
    op.drop_constraint("fk_hospital_connectors_current_certificate", "hospital_connectors", schema=S)
    for table in ("connector_control_audit_events", "connector_heartbeats", "connector_capability_manifests", "connector_certificates", "connector_registration_requests", "connector_enrollment_tokens", "hospital_connectors"):
        op.drop_table(table, schema=S)
