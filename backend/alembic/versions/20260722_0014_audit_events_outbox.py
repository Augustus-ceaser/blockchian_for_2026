"""Add immutable AuditEvent stream and transactional Outbox infrastructure.

Revision ID: 20260722_0014
Revises: 20260722_0013
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260722_0014"
down_revision: str | None = "20260722_0013"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"


def _create_canonical_helpers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.canonicalize_jsonb_v1(p_value jsonb)
        RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE STRICT PARALLEL SAFE
        AS $$
        DECLARE
            v_type text;
            v_result text;
        BEGIN
            v_type := jsonb_typeof(p_value);
            CASE v_type
                WHEN 'object' THEN
                    SELECT '{' || COALESCE(
                        string_agg(
                            to_jsonb(entry.key)::text || ':' ||
                            medtrust.canonicalize_jsonb_v1(entry.value),
                            ',' ORDER BY convert_to(entry.key, 'UTF8')
                        ), ''
                    ) || '}' INTO v_result
                    FROM jsonb_each(p_value) AS entry(key, value);
                    RETURN v_result;
                WHEN 'array' THEN
                    SELECT '[' || COALESCE(
                        string_agg(
                            medtrust.canonicalize_jsonb_v1(entry.value),
                            ',' ORDER BY entry.ordinality
                        ), ''
                    ) || ']' INTO v_result
                    FROM jsonb_array_elements(p_value)
                         WITH ORDINALITY AS entry(value, ordinality);
                    RETURN v_result;
                WHEN 'number' THEN
                    IF p_value::text !~ '^-?(0|[1-9][0-9]*)$' THEN
                        RAISE EXCEPTION 'medtrust-jsonb-c14n/v1 rejects non-integer numbers'
                            USING ERRCODE = '22023';
                    END IF;
                    RETURN p_value::text;
                WHEN 'string' THEN RETURN p_value::text;
                WHEN 'boolean' THEN RETURN p_value::text;
                WHEN 'null' THEN RETURN 'null';
                ELSE
                    RAISE EXCEPTION 'unsupported JSONB type: %', v_type
                        USING ERRCODE = '22023';
            END CASE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.sha256_text_v1(p_value text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE STRICT PARALLEL SAFE
        AS $$
            SELECT 'sha256:' || encode(sha256(convert_to(p_value, 'UTF8')), 'hex')
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.sha256_canonical_jsonb_v1(p_value jsonb)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE STRICT PARALLEL SAFE
        AS $$
            SELECT medtrust.sha256_text_v1(medtrust.canonicalize_jsonb_v1(p_value))
        $$
        """
    )


def _create_tables() -> None:
    op.create_table(
        "audit_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("canonicalization_version", sa.String(length=40), server_default="medtrust-jsonb-c14n/v1", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_connector_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_service_code", sa.String(length=64), nullable=True),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=71), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_digest", sa.String(length=71), nullable=False),
        sa.Column("previous_event_digest", sa.String(length=71), nullable=True),
        sa.Column("event_digest", sa.String(length=71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("stream_sequence > 0", name="ck_audit_events_stream_sequence_positive"),
        sa.CheckConstraint("schema_version = 1", name="ck_audit_events_schema_version_v1"),
        sa.CheckConstraint("canonicalization_version = 'medtrust-jsonb-c14n/v1'", name="ck_audit_events_canonicalization_version_v1"),
        sa.CheckConstraint("event_type IN ('contract.revision.activated','compute.job.created','compute.run.reserved','compute.run.started','compute.run.completed','compute.run.failed','compute.run.interrupted','artifact.created','artifact.review.decided','artifact.released')", name="ck_audit_events_event_type"),
        sa.CheckConstraint("actor_type IN ('user','connector','system')", name="ck_audit_events_actor_type"),
        sa.CheckConstraint("subject_type IN ('contract_revision','compute_job','compute_run','artifact','artifact_review')", name="ck_audit_events_subject_type"),
        sa.CheckConstraint("result IN ('success','failure','denied','interrupted','cancelled')", name="ck_audit_events_result"),
        sa.CheckConstraint(
            "(actor_type='user' AND actor_organization_id IS NOT NULL AND actor_user_id IS NOT NULL AND actor_connector_id IS NULL AND actor_service_code IS NULL) OR "
            "(actor_type='connector' AND actor_organization_id IS NOT NULL AND actor_user_id IS NULL AND actor_connector_id IS NOT NULL AND actor_service_code IS NULL) OR "
            "(actor_type='system' AND actor_user_id IS NULL AND actor_connector_id IS NULL AND actor_service_code IS NOT NULL)",
            name="ck_audit_events_actor_shape",
        ),
        sa.CheckConstraint("idempotency_key ~ '^sha256:[0-9a-f]{64}$'", name="ck_audit_events_idempotency_digest_format"),
        sa.CheckConstraint("evidence_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_audit_events_evidence_digest_format"),
        sa.CheckConstraint("event_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_audit_events_event_digest_format"),
        sa.CheckConstraint("previous_event_digest IS NULL OR previous_event_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_audit_events_previous_digest_format"),
        sa.CheckConstraint("(stream_sequence=1 AND previous_event_digest IS NULL) OR (stream_sequence>1 AND previous_event_digest IS NOT NULL)", name="ck_audit_events_chain_shape"),
        sa.CheckConstraint("jsonb_typeof(evidence_snapshot)='object'", name="ck_audit_events_evidence_object"),
        sa.CheckConstraint("octet_length(medtrust.canonicalize_jsonb_v1(evidence_snapshot)) <= 65536", name="ck_audit_events_evidence_size"),
        sa.ForeignKeyConstraint(["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_organization_id"], ["medtrust.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["medtrust.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_connector_id"], ["medtrust.connectors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["causation_id", "space_id"],
            ["medtrust.audit_events.event_id", "medtrust.audit_events.space_id"],
            name="fk_audit_events_causation_space", ondelete="RESTRICT",
            deferrable=True, initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
        sa.UniqueConstraint("event_id", "space_id", name="uq_audit_events_event_space"),
        sa.UniqueConstraint("space_id", "stream_sequence", name="uq_audit_events_space_sequence"),
        sa.UniqueConstraint("space_id", "event_digest", name="uq_audit_events_space_digest"),
        sa.UniqueConstraint("space_id", "idempotency_key", "event_type", "subject_type", "subject_id", name="uq_audit_events_idempotent_fact"),
        sa.UniqueConstraint("space_id", "command_id", "event_type", "subject_type", "subject_id", name="uq_audit_events_command_fact"),
        schema=SCHEMA,
    )
    op.create_index("ix_audit_events_space_sequence_desc", "audit_events", ["space_id", sa.text("stream_sequence DESC")], schema=SCHEMA)
    op.create_index("ix_audit_events_space_occurred_desc", "audit_events", ["space_id", sa.text("occurred_at DESC"), "event_id"], schema=SCHEMA)
    op.create_index("ix_audit_events_subject_occurred_desc", "audit_events", ["subject_type", "subject_id", sa.text("occurred_at DESC")], schema=SCHEMA)
    op.create_index("ix_audit_events_correlation", "audit_events", ["correlation_id", "occurred_at", "event_id"], schema=SCHEMA)
    op.create_index("ix_audit_events_space_command_sequence", "audit_events", ["space_id", "command_id", "stream_sequence"], schema=SCHEMA)
    op.create_index("ix_audit_events_space_idempotency_sequence", "audit_events", ["space_id", "idempotency_key", "stream_sequence"], schema=SCHEMA)
    op.create_index("ix_audit_events_type_occurred", "audit_events", ["event_type", sa.text("occurred_at DESC")], schema=SCHEMA)
    op.create_index("ix_audit_events_actor_org_occurred", "audit_events", ["actor_organization_id", sa.text("occurred_at DESC")], schema=SCHEMA, postgresql_where=sa.text("actor_organization_id IS NOT NULL"))

    op.create_table(
        "outbox_messages",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=96), nullable=False),
        sa.Column("destination", sa.String(length=96), nullable=False),
        sa.Column("message_schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("payload_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_digest", sa.String(length=71), nullable=False),
        sa.Column("idempotency_key", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_owner", sa.String(length=96), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("status IN ('pending','processing','published','dead_letter')", name="ck_outbox_messages_status"),
        sa.CheckConstraint("attempt_count >= 0 AND attempt_count <= 10", name="ck_outbox_messages_attempt_count_range"),
        sa.CheckConstraint("row_version >= 1", name="ck_outbox_messages_row_version_positive"),
        sa.CheckConstraint("message_schema_version = 1", name="ck_outbox_messages_schema_version_v1"),
        sa.CheckConstraint("payload_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_outbox_messages_payload_digest_format"),
        sa.CheckConstraint("idempotency_key ~ '^sha256:[0-9a-f]{64}$'", name="ck_outbox_messages_idempotency_digest_format"),
        sa.CheckConstraint("jsonb_typeof(payload_snapshot)='object'", name="ck_outbox_messages_payload_object"),
        sa.CheckConstraint("octet_length(medtrust.canonicalize_jsonb_v1(payload_snapshot)) <= 65536", name="ck_outbox_messages_payload_size"),
        sa.CheckConstraint(
            "(status='pending' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND published_at IS NULL) OR "
            "(status='processing' AND locked_at IS NOT NULL AND lock_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND published_at IS NULL) OR "
            "(status='published' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND published_at IS NOT NULL AND last_error IS NULL) OR "
            "(status='dead_letter' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND published_at IS NULL)",
            name="ck_outbox_messages_delivery_shape",
        ),
        sa.ForeignKeyConstraint(["audit_event_id", "space_id"], ["medtrust.audit_events.event_id", "medtrust.audit_events.space_id"], name="fk_outbox_messages_event_space", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("message_id", name="pk_outbox_messages"),
        sa.UniqueConstraint("message_id", "space_id", name="uq_outbox_messages_message_space"),
        sa.UniqueConstraint("audit_event_id", "topic", "destination", name="uq_outbox_messages_event_target"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_messages_idempotency_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_outbox_messages_pending_claim", "outbox_messages", ["available_at", "created_at", "message_id"], schema=SCHEMA, postgresql_where=sa.text("status='pending'"))
    op.create_index("ix_outbox_messages_processing_lease", "outbox_messages", ["lease_expires_at", "message_id"], schema=SCHEMA, postgresql_where=sa.text("status='processing'"))
    op.create_index("ix_outbox_messages_destination_status_available", "outbox_messages", ["destination", "status", "available_at"], schema=SCHEMA)
    op.create_index("ix_outbox_messages_space_created", "outbox_messages", ["space_id", sa.text("created_at DESC")], schema=SCHEMA)
    op.create_index("ix_outbox_messages_event", "outbox_messages", ["audit_event_id"], schema=SCHEMA)
    op.create_index("ix_outbox_messages_status_updated", "outbox_messages", ["status", "updated_at"], schema=SCHEMA)


def _create_audit_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.audit_event_manifest_v1(p_event medtrust.audit_events)
        RETURNS jsonb
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        AS $$
            SELECT jsonb_build_object(
                'event_id', p_event.event_id::text,
                'space_id', p_event.space_id::text,
                'stream_sequence', p_event.stream_sequence,
                'previous_event_digest', p_event.previous_event_digest,
                'event_type', p_event.event_type,
                'schema_version', p_event.schema_version,
                'canonicalization_version', p_event.canonicalization_version,
                'occurred_at', to_char(p_event.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'actor_type', p_event.actor_type,
                'actor_organization_id', CASE WHEN p_event.actor_organization_id IS NULL THEN NULL ELSE to_jsonb(p_event.actor_organization_id::text) END,
                'actor_user_id', CASE WHEN p_event.actor_user_id IS NULL THEN NULL ELSE to_jsonb(p_event.actor_user_id::text) END,
                'actor_connector_id', CASE WHEN p_event.actor_connector_id IS NULL THEN NULL ELSE to_jsonb(p_event.actor_connector_id::text) END,
                'actor_service_code', p_event.actor_service_code,
                'subject_type', p_event.subject_type,
                'subject_id', p_event.subject_id::text,
                'result', p_event.result,
                'correlation_id', p_event.correlation_id::text,
                'causation_id', CASE WHEN p_event.causation_id IS NULL THEN NULL ELSE to_jsonb(p_event.causation_id::text) END,
                'command_id', p_event.command_id::text,
                'idempotency_key', p_event.idempotency_key,
                'evidence_digest', p_event.evidence_digest
            )
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_audit_event_v8()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_previous medtrust.audit_events%ROWTYPE;
            v_subject_ok boolean := false;
        BEGIN
            IF TG_OP IN ('UPDATE','DELETE') THEN
                RAISE EXCEPTION 'AuditEvent is append-only' USING ERRCODE='55000';
            END IF;
            PERFORM 1 FROM medtrust.spaces WHERE id=NEW.space_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'AuditEvent space does not exist' USING ERRCODE='23503'; END IF;

            IF NEW.actor_type='user' THEN
                SELECT EXISTS(
                    SELECT 1 FROM medtrust.users u
                    JOIN medtrust.organization_members m ON m.user_id=u.id AND m.organization_id=NEW.actor_organization_id
                    WHERE u.id=NEW.actor_user_id AND u.status='active' AND m.status='active'
                      AND (m.valid_from IS NULL OR m.valid_from<=NEW.occurred_at)
                      AND (m.valid_until IS NULL OR m.valid_until>NEW.occurred_at)
                ) INTO v_subject_ok;
                IF NOT v_subject_ok THEN RAISE EXCEPTION 'AuditEvent user actor is not active in organization' USING ERRCODE='23514'; END IF;
            ELSIF NEW.actor_type='connector' THEN
                SELECT EXISTS(SELECT 1 FROM medtrust.connectors c WHERE c.id=NEW.actor_connector_id AND c.space_id=NEW.space_id AND c.owner_organization_id=NEW.actor_organization_id) INTO v_subject_ok;
                IF NOT v_subject_ok THEN RAISE EXCEPTION 'AuditEvent connector actor is outside its space' USING ERRCODE='23514'; END IF;
            ELSIF NEW.actor_type='system' AND NEW.actor_service_code NOT IN ('medtrust.contract','medtrust.compute','medtrust.artifact','medtrust.audit') THEN
                RAISE EXCEPTION 'AuditEvent system service is not registered' USING ERRCODE='23514';
            END IF;

            v_subject_ok := false;
            CASE NEW.event_type
                WHEN 'contract.revision.activated' THEN
                    IF NEW.subject_type<>'contract_revision' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_revisions r JOIN medtrust.contracts c ON c.id=r.contract_id WHERE r.id=NEW.subject_id AND c.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.job.created' THEN
                    IF NEW.subject_type<>'compute_job' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_jobs j WHERE j.id=NEW.subject_id AND j.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.reserved' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.started' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.completed' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.failed' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'failure' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.interrupted' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'interrupted' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'artifact.created' THEN
                    IF NEW.subject_type<>'artifact' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.artifacts a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'artifact.review.decided' THEN
                    IF NEW.subject_type<>'artifact_review' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.artifact_reviews r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'artifact.released' THEN
                    IF NEW.subject_type<>'artifact' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.artifacts a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                ELSE RAISE EXCEPTION 'unsupported AuditEvent type' USING ERRCODE='23514';
            END CASE;
            IF NOT v_subject_ok THEN RAISE EXCEPTION 'AuditEvent subject is missing or cross-space' USING ERRCODE='23514'; END IF;

            IF EXISTS(SELECT 1 FROM medtrust.audit_events e WHERE e.space_id=NEW.space_id AND e.idempotency_key=NEW.idempotency_key AND (e.command_id<>NEW.command_id OR e.correlation_id<>NEW.correlation_id)) THEN
                RAISE EXCEPTION 'idempotency key maps to another command context' USING ERRCODE='23505';
            END IF;
            IF EXISTS(SELECT 1 FROM medtrust.audit_events e WHERE e.space_id=NEW.space_id AND e.command_id=NEW.command_id AND (e.idempotency_key<>NEW.idempotency_key OR e.correlation_id<>NEW.correlation_id)) THEN
                RAISE EXCEPTION 'command id maps to another idempotency context' USING ERRCODE='23505';
            END IF;

            SELECT * INTO v_previous FROM medtrust.audit_events e WHERE e.space_id=NEW.space_id ORDER BY e.stream_sequence DESC LIMIT 1;
            IF FOUND THEN
                IF NEW.stream_sequence<>v_previous.stream_sequence+1 OR NEW.previous_event_digest IS DISTINCT FROM v_previous.event_digest THEN
                    RAISE EXCEPTION 'AuditEvent chain sequence or previous digest is invalid' USING ERRCODE='23514';
                END IF;
            ELSIF NEW.stream_sequence<>1 OR NEW.previous_event_digest IS NOT NULL THEN
                RAISE EXCEPTION 'AuditEvent genesis shape is invalid' USING ERRCODE='23514';
            END IF;
            IF NEW.causation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM medtrust.audit_events e WHERE e.event_id=NEW.causation_id AND e.space_id=NEW.space_id AND e.stream_sequence<NEW.stream_sequence) THEN
                RAISE EXCEPTION 'AuditEvent causation must reference an earlier event in the same space' USING ERRCODE='23514';
            END IF;
            IF NEW.created_at IS DISTINCT FROM NEW.occurred_at THEN RAISE EXCEPTION 'AuditEvent timestamps must match' USING ERRCODE='23514'; END IF;
            IF NEW.evidence_snapshot::text ~* '"(patient_name|patient_id|patient_identifier|mrn|medical_record_number|pathology_number|wsi_path|pacs_path|lis_path|emr_path|object_path|presigned_url|access_token|refresh_token|password|secret|access_key|secret_key|credential|private_key)"[[:space:]]*:' THEN
                RAISE EXCEPTION 'AuditEvent evidence contains forbidden sensitive keys' USING ERRCODE='23514';
            END IF;
            IF NEW.evidence_digest<>medtrust.sha256_canonical_jsonb_v1(NEW.evidence_snapshot) THEN RAISE EXCEPTION 'AuditEvent evidence digest mismatch' USING ERRCODE='23514'; END IF;
            IF NEW.event_digest<>medtrust.sha256_canonical_jsonb_v1(medtrust.audit_event_manifest_v1(NEW)) THEN RAISE EXCEPTION 'AuditEvent digest mismatch' USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_audit_event_v8
        BEFORE INSERT OR UPDATE OR DELETE ON medtrust.audit_events
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_audit_event_v8()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.verify_audit_space_chain_v1(p_space_id uuid)
        RETURNS TABLE(is_valid boolean, invalid_sequence bigint, reason text)
        LANGUAGE plpgsql STABLE AS $$
        DECLARE
            v_event medtrust.audit_events%ROWTYPE;
            v_expected_sequence bigint := 1;
            v_previous_digest text := NULL;
        BEGIN
            FOR v_event IN SELECT * FROM medtrust.audit_events WHERE space_id=p_space_id ORDER BY stream_sequence LOOP
                IF v_event.stream_sequence<>v_expected_sequence THEN RETURN QUERY SELECT false,v_event.stream_sequence,'non_contiguous_sequence'::text; RETURN; END IF;
                IF v_event.previous_event_digest IS DISTINCT FROM v_previous_digest THEN RETURN QUERY SELECT false,v_event.stream_sequence,'previous_digest_mismatch'::text; RETURN; END IF;
                IF v_event.evidence_digest<>medtrust.sha256_canonical_jsonb_v1(v_event.evidence_snapshot) THEN RETURN QUERY SELECT false,v_event.stream_sequence,'evidence_digest_mismatch'::text; RETURN; END IF;
                IF v_event.event_digest<>medtrust.sha256_canonical_jsonb_v1(medtrust.audit_event_manifest_v1(v_event)) THEN RETURN QUERY SELECT false,v_event.stream_sequence,'event_digest_mismatch'::text; RETURN; END IF;
                v_expected_sequence:=v_expected_sequence+1;
                v_previous_digest:=v_event.event_digest;
            END LOOP;
            RETURN QUERY SELECT true,NULL::bigint,NULL::text;
        END;
        $$
        """
    )


def _create_outbox_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.outbox_payload_v1(p_event medtrust.audit_events,p_message_id uuid)
        RETURNS jsonb LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT jsonb_build_object(
                'message_schema','medtrust-event-envelope/v1','message_id',p_message_id::text,
                'event_id',p_event.event_id::text,'space_id',p_event.space_id::text,
                'event_type',p_event.event_type,'event_schema_version',p_event.schema_version,
                'occurred_at',to_char(p_event.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'subject_type',p_event.subject_type,'subject_id',p_event.subject_id::text,
                'result',p_event.result,'correlation_id',p_event.correlation_id::text,
                'event_digest',p_event.event_digest,'evidence',p_event.evidence_snapshot
            )
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.outbox_target_allowed_v8(p_event_type text,p_topic text,p_destination text)
        RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT (p_topic='medtrust.audit.v1' AND p_destination='audit.timeline')
                OR (p_event_type='compute.run.reserved' AND p_topic='medtrust.compute.dispatch.v1' AND p_destination='compute.dispatch')
                OR (p_event_type='artifact.created' AND p_topic='medtrust.artifact.review.v1' AND p_destination='artifact.review-routing')
                OR (p_event_type='artifact.review.decided' AND p_topic='medtrust.artifact.release-evaluation.v1' AND p_destination='artifact.release-evaluation')
                OR (p_event_type='artifact.released' AND p_topic='medtrust.artifact.delivery.v1' AND p_destination='artifact.delivery-notification')
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_outbox_message_v8()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_event medtrust.audit_events%ROWTYPE;
            v_expected_key text;
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'OutboxMessage cannot be deleted in V1' USING ERRCODE='55000'; END IF;
            SELECT * INTO v_event FROM medtrust.audit_events WHERE event_id=NEW.audit_event_id AND space_id=NEW.space_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'OutboxMessage event is missing or cross-space' USING ERRCODE='23503'; END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.status<>'pending' OR NEW.attempt_count<>0 OR NEW.row_version<>1 OR NEW.locked_at IS NOT NULL OR NEW.lock_owner IS NOT NULL OR NEW.lease_expires_at IS NOT NULL OR NEW.last_error IS NOT NULL OR NEW.published_at IS NOT NULL THEN
                    RAISE EXCEPTION 'OutboxMessage must begin pending and unleased' USING ERRCODE='23514';
                END IF;
                IF NEW.created_at IS DISTINCT FROM NEW.updated_at THEN RAISE EXCEPTION 'OutboxMessage initial timestamps must match' USING ERRCODE='23514'; END IF;
            ELSE
                IF OLD.status IN ('published','dead_letter') THEN RAISE EXCEPTION 'terminal OutboxMessage cannot be changed' USING ERRCODE='55000'; END IF;
                IF NEW.message_id IS DISTINCT FROM OLD.message_id OR NEW.audit_event_id IS DISTINCT FROM OLD.audit_event_id OR NEW.space_id IS DISTINCT FROM OLD.space_id OR NEW.topic IS DISTINCT FROM OLD.topic OR NEW.destination IS DISTINCT FROM OLD.destination OR NEW.message_schema_version IS DISTINCT FROM OLD.message_schema_version OR NEW.payload_snapshot IS DISTINCT FROM OLD.payload_snapshot OR NEW.payload_digest IS DISTINCT FROM OLD.payload_digest OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'OutboxMessage core fields are immutable' USING ERRCODE='55000';
                END IF;
                IF NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at THEN RAISE EXCEPTION 'OutboxMessage row version is invalid' USING ERRCODE='23514'; END IF;
                IF OLD.status='pending' THEN
                    IF NEW.status='processing' THEN
                        IF NEW.attempt_count<>OLD.attempt_count+1 THEN RAISE EXCEPTION 'claim must increment attempt count' USING ERRCODE='23514'; END IF;
                    ELSIF NEW.status='dead_letter' THEN
                        IF NEW.attempt_count<>OLD.attempt_count THEN RAISE EXCEPTION 'pending dead-letter must preserve attempt count' USING ERRCODE='23514'; END IF;
                    ELSE RAISE EXCEPTION 'invalid pending Outbox transition' USING ERRCODE='23514';
                    END IF;
                ELSIF OLD.status='processing' THEN
                    IF NEW.status='processing' THEN
                        IF OLD.lease_expires_at>clock_timestamp() OR NEW.attempt_count<>OLD.attempt_count+1 THEN RAISE EXCEPTION 'processing message lease is not reclaimable' USING ERRCODE='23514'; END IF;
                    ELSIF NEW.status IN ('pending','published','dead_letter') THEN
                        IF NEW.attempt_count<>OLD.attempt_count THEN RAISE EXCEPTION 'completion/failure must preserve attempt count' USING ERRCODE='23514'; END IF;
                    ELSE RAISE EXCEPTION 'invalid processing Outbox transition' USING ERRCODE='23514';
                    END IF;
                ELSE RAISE EXCEPTION 'invalid Outbox transition source' USING ERRCODE='23514';
                END IF;
            END IF;
            IF NOT medtrust.outbox_target_allowed_v8(v_event.event_type,NEW.topic,NEW.destination) THEN RAISE EXCEPTION 'Outbox target is not permitted for event type' USING ERRCODE='23514'; END IF;
            IF NEW.payload_snapshot<>medtrust.outbox_payload_v1(v_event,NEW.message_id) THEN RAISE EXCEPTION 'Outbox payload does not match immutable event projection' USING ERRCODE='23514'; END IF;
            IF NEW.payload_digest<>medtrust.sha256_canonical_jsonb_v1(NEW.payload_snapshot) THEN RAISE EXCEPTION 'Outbox payload digest mismatch' USING ERRCODE='23514'; END IF;
            v_expected_key:=medtrust.sha256_text_v1(NEW.audit_event_id::text||'|'||NEW.topic||'|'||NEW.destination||'|'||NEW.message_schema_version::text);
            IF NEW.idempotency_key<>v_expected_key THEN RAISE EXCEPTION 'Outbox idempotency digest mismatch' USING ERRCODE='23514'; END IF;
            IF NEW.last_error IS NOT NULL AND (length(NEW.last_error)>1024 OR NEW.last_error ~* '(authorization|bearer|token|secret|password|access[_-]?key|x-amz-signature|signature)[[:space:]]*[:=]' OR NEW.last_error ~* 'https?://[^[:space:]?]+\\?[^[:space:]]+') THEN
                RAISE EXCEPTION 'Outbox last_error contains forbidden sensitive content' USING ERRCODE='23514';
            END IF;
            IF NEW.status='processing' AND NEW.lease_expires_at<=NEW.locked_at THEN RAISE EXCEPTION 'Outbox lease must expire after lock time' USING ERRCODE='23514'; END IF;
            IF NEW.status='dead_letter' AND NEW.last_error IS NULL THEN RAISE EXCEPTION 'dead-letter requires a sanitized reason' USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_outbox_message_v8
        BEFORE INSERT OR UPDATE OR DELETE ON medtrust.outbox_messages
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_outbox_message_v8()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_audit_event_outbox_targets_v8()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE v_expected integer:=1; v_actual integer;
        BEGIN
            IF NEW.event_type IN ('compute.run.reserved','artifact.created','artifact.review.decided','artifact.released') THEN v_expected:=2; END IF;
            SELECT count(*) INTO v_actual FROM medtrust.outbox_messages m WHERE m.audit_event_id=NEW.event_id;
            IF v_actual<>v_expected THEN RAISE EXCEPTION 'AuditEvent requires exactly % Outbox target(s), found %',v_expected,v_actual USING ERRCODE='23514'; END IF;
            IF NOT EXISTS(SELECT 1 FROM medtrust.outbox_messages m WHERE m.audit_event_id=NEW.event_id AND m.topic='medtrust.audit.v1' AND m.destination='audit.timeline') THEN
                RAISE EXCEPTION 'AuditEvent is missing audit.timeline Outbox target' USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_guard_audit_event_outbox_targets_v8
        AFTER INSERT ON medtrust.audit_events DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_audit_event_outbox_targets_v8()
        """
    )


def upgrade() -> None:
    _create_canonical_helpers()
    _create_tables()
    _create_audit_guards()
    _create_outbox_guards()


def downgrade() -> None:
    for statement in (
        "DROP TRIGGER IF EXISTS trg_guard_audit_event_outbox_targets_v8 ON medtrust.audit_events",
        "DROP TRIGGER IF EXISTS trg_guard_outbox_message_v8 ON medtrust.outbox_messages",
        "DROP TRIGGER IF EXISTS trg_guard_audit_event_v8 ON medtrust.audit_events",
        "DROP FUNCTION IF EXISTS medtrust.guard_audit_event_outbox_targets_v8()",
        "DROP FUNCTION IF EXISTS medtrust.guard_outbox_message_v8()",
        "DROP FUNCTION IF EXISTS medtrust.outbox_target_allowed_v8(text,text,text)",
        "DROP FUNCTION IF EXISTS medtrust.outbox_payload_v1(medtrust.audit_events,uuid)",
        "DROP FUNCTION IF EXISTS medtrust.verify_audit_space_chain_v1(uuid)",
        "DROP FUNCTION IF EXISTS medtrust.guard_audit_event_v8()",
        "DROP FUNCTION IF EXISTS medtrust.audit_event_manifest_v1(medtrust.audit_events)",
    ):
        op.execute(statement)
    op.drop_table("outbox_messages", schema=SCHEMA)
    op.drop_table("audit_events", schema=SCHEMA)
    for statement in (
        "DROP FUNCTION IF EXISTS medtrust.sha256_canonical_jsonb_v1(jsonb)",
        "DROP FUNCTION IF EXISTS medtrust.sha256_text_v1(text)",
        "DROP FUNCTION IF EXISTS medtrust.canonicalize_jsonb_v1(jsonb)",
    ):
        op.execute(statement)
