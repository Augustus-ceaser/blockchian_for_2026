"""Add Consumer Inbox and the compute.run.dispatched event vocabulary.

Revision ID: 20260722_0016
Revises: 20260722_0015
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260722_0016"
down_revision: str | None = "20260722_0015"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"

OLD_EVENT_CHECK = (
    "event_type IN ('contract.revision.activated','compute.job.created',"
    "'compute.run.reserved','compute.run.started','compute.run.completed',"
    "'compute.run.failed','compute.run.interrupted','artifact.created',"
    "'artifact.review.decided','artifact.released')"
)
NEW_EVENT_CHECK = (
    "event_type IN ('contract.revision.activated','compute.job.created',"
    "'compute.run.reserved','compute.run.dispatched','compute.run.started',"
    "'compute.run.completed','compute.run.failed','compute.run.interrupted',"
    "'artifact.created','artifact.review.decided','artifact.released')"
)
OLD_GUARD_CASE = """WHEN 'compute.run.reserved' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.started' THEN"""
NEW_GUARD_CASE = """WHEN 'compute.run.reserved' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.dispatched' THEN
                    IF NEW.subject_type<>'compute_run' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_runs r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.run.started' THEN"""


def _replace_audit_guard(old: str, new: str) -> None:
    bind = op.get_bind()
    definition = bind.execute(
        sa.text(
            "SELECT pg_get_functiondef('medtrust.guard_audit_event_v8()'::regprocedure)"
        )
    ).scalar_one()
    if old not in definition:
        raise RuntimeError("expected v8 AuditEvent catalog branch was not found")
    bind.exec_driver_sql(definition.replace(old, new, 1))


def _create_table() -> None:
    op.create_table(
        "consumer_inbox_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumer_name", sa.String(length=96), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_digest", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="received", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_owner", sa.String(length=96), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_code", sa.String(length=64), nullable=True),
        sa.Column("outcome_reference_type", sa.String(length=32), nullable=True),
        sa.Column("outcome_reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("processing_error", sa.String(length=1024), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("status IN ('received','processing','completed','dead_letter')", name="ck_consumer_inbox_entries_status"),
        sa.CheckConstraint("attempt_count >= 0 AND attempt_count <= 10", name="ck_consumer_inbox_entries_attempt_count_range"),
        sa.CheckConstraint("row_version >= 1", name="ck_consumer_inbox_entries_row_version_positive"),
        sa.CheckConstraint("payload_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_consumer_inbox_entries_payload_digest_format"),
        sa.CheckConstraint(
            "outcome_code IS NULL OR outcome_code IN ('executor_submitted','already_dispatched','authorization_revoked','ignored_terminal_run','non_retryable_rejection')",
            name="ck_consumer_inbox_entries_outcome_code",
        ),
        sa.CheckConstraint("(outcome_reference_type IS NULL) = (outcome_reference_id IS NULL)", name="ck_consumer_inbox_entries_outcome_reference_shape"),
        sa.CheckConstraint(
            "(status='received' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND completed_at IS NULL AND terminal_at IS NULL AND outcome_code IS NULL AND outcome_reference_type IS NULL AND outcome_reference_id IS NULL) OR "
            "(status='processing' AND locked_at IS NOT NULL AND lock_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND processing_started_at IS NOT NULL AND completed_at IS NULL AND terminal_at IS NULL AND outcome_code IS NULL AND outcome_reference_type IS NULL AND outcome_reference_id IS NULL) OR "
            "(status='completed' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND completed_at IS NOT NULL AND terminal_at IS NOT NULL AND outcome_code IS NOT NULL) OR "
            "(status='dead_letter' AND locked_at IS NULL AND lock_owner IS NULL AND lease_expires_at IS NULL AND completed_at IS NULL AND terminal_at IS NOT NULL AND processing_error IS NOT NULL)",
            name="ck_consumer_inbox_entries_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "space_id"],
            ["medtrust.audit_events.event_id", "medtrust.audit_events.space_id"],
            name="fk_consumer_inbox_entries_event_space",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id", "space_id"],
            ["medtrust.outbox_messages.message_id", "medtrust.outbox_messages.space_id"],
            name="fk_consumer_inbox_entries_message_space",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consumer_inbox_entries"),
        sa.UniqueConstraint("consumer_name", "event_id", name="uq_consumer_inbox_entries_consumer_event"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_consumer_inbox_entries_received_claim",
        "consumer_inbox_entries",
        ["consumer_name", "available_at", "received_at", "id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status='received'"),
    )
    op.create_index(
        "ix_consumer_inbox_entries_processing_lease",
        "consumer_inbox_entries",
        ["consumer_name", "lease_expires_at", "id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status='processing'"),
    )
    op.create_index("ix_consumer_inbox_entries_source_message", "consumer_inbox_entries", ["source_message_id"], schema=SCHEMA)
    op.create_index("ix_consumer_inbox_entries_space_received", "consumer_inbox_entries", ["space_id", "received_at"], schema=SCHEMA)
    op.create_index("ix_consumer_inbox_entries_event", "consumer_inbox_entries", ["event_id"], schema=SCHEMA)


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_consumer_inbox_entry_v9()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_message medtrust.outbox_messages%ROWTYPE;
            v_event medtrust.audit_events%ROWTYPE;
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'ConsumerInboxEntry cannot be deleted' USING ERRCODE='55000';
            END IF;

            SELECT * INTO v_message FROM medtrust.outbox_messages
             WHERE message_id=NEW.source_message_id AND space_id=NEW.space_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'ConsumerInboxEntry source OutboxMessage is invalid' USING ERRCODE='23503';
            END IF;
            SELECT * INTO v_event FROM medtrust.audit_events
             WHERE event_id=NEW.event_id AND space_id=NEW.space_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'ConsumerInboxEntry source AuditEvent is invalid' USING ERRCODE='23503';
            END IF;
            IF v_message.audit_event_id<>NEW.event_id
               OR v_message.payload_digest<>NEW.payload_digest THEN
                RAISE EXCEPTION 'ConsumerInboxEntry source evidence is inconsistent' USING ERRCODE='23514';
            END IF;
            IF v_message.topic<>'medtrust.compute.dispatch.v1'
               OR v_message.destination<>'compute.dispatch'
               OR v_event.event_type<>'compute.run.reserved'
               OR v_event.subject_type<>'compute_run'
               OR v_event.result<>'success' THEN
                RAISE EXCEPTION 'ConsumerInboxEntry source is not a dispatchable reservation' USING ERRCODE='23514';
            END IF;

            IF TG_OP='INSERT' THEN
                IF v_message.status<>'processing' THEN
                    RAISE EXCEPTION 'ConsumerInboxEntry source OutboxMessage must be processing at receipt' USING ERRCODE='23514';
                END IF;
                IF NEW.status<>'received' OR NEW.attempt_count<>0 OR NEW.row_version<>1 THEN
                    RAISE EXCEPTION 'ConsumerInboxEntry must start as received' USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status IN ('completed','dead_letter') THEN
                RAISE EXCEPTION 'terminal ConsumerInboxEntry is immutable' USING ERRCODE='55000';
            END IF;
            IF (NEW.consumer_name,NEW.event_id,NEW.source_message_id,NEW.space_id,NEW.payload_digest,NEW.received_at,NEW.created_at)
               IS DISTINCT FROM
               (OLD.consumer_name,OLD.event_id,OLD.source_message_id,OLD.space_id,OLD.payload_digest,OLD.received_at,OLD.created_at) THEN
                RAISE EXCEPTION 'ConsumerInboxEntry source identity is immutable' USING ERRCODE='55000';
            END IF;
            IF NEW.row_version<>OLD.row_version+1 THEN
                RAISE EXCEPTION 'ConsumerInboxEntry row_version must advance exactly once' USING ERRCODE='40001';
            END IF;

            IF OLD.status='received' AND NEW.status='processing' THEN
                IF NEW.attempt_count<>OLD.attempt_count+1 THEN
                    RAISE EXCEPTION 'Inbox claim must consume one attempt' USING ERRCODE='23514';
                END IF;
            ELSIF OLD.status='processing' AND NEW.status='processing' THEN
                IF OLD.lease_expires_at>statement_timestamp() OR NEW.attempt_count<>OLD.attempt_count+1 THEN
                    RAISE EXCEPTION 'Inbox processing lease can only be reclaimed after expiry' USING ERRCODE='55000';
                END IF;
            ELSIF OLD.status='processing' AND NEW.status IN ('received','completed','dead_letter') THEN
                IF NEW.attempt_count<>OLD.attempt_count THEN
                    RAISE EXCEPTION 'Inbox settlement cannot change attempt_count' USING ERRCODE='23514';
                END IF;
            ELSE
                RAISE EXCEPTION 'illegal ConsumerInboxEntry status transition' USING ERRCODE='55000';
            END IF;

            IF NEW.status='completed' AND NEW.outcome_reference_type IS NOT NULL THEN
                IF NEW.outcome_reference_type<>'compute_run'
                   OR NEW.outcome_reference_id<>v_event.subject_id
                   OR NOT EXISTS(
                       SELECT 1 FROM medtrust.compute_runs r
                        WHERE r.id=NEW.outcome_reference_id AND r.space_id=NEW.space_id
                   ) THEN
                    RAISE EXCEPTION 'Inbox completion outcome reference is invalid' USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_consumer_inbox_entry_v9
        BEFORE INSERT OR UPDATE OR DELETE ON medtrust.consumer_inbox_entries
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_consumer_inbox_entry_v9()
        """
    )


def upgrade() -> None:
    _create_table()
    _create_guards()
    op.drop_constraint("ck_audit_events_event_type", "audit_events", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_audit_events_event_type", "audit_events", NEW_EVENT_CHECK, schema=SCHEMA)
    _replace_audit_guard(OLD_GUARD_CASE, NEW_GUARD_CASE)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT count(*) FROM medtrust.audit_events WHERE event_type='compute.run.dispatched'")
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade 0016 while compute.run.dispatched events exist")
    _replace_audit_guard(NEW_GUARD_CASE, OLD_GUARD_CASE)
    op.drop_constraint("ck_audit_events_event_type", "audit_events", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_audit_events_event_type", "audit_events", OLD_EVENT_CHECK, schema=SCHEMA)
    op.execute("DROP TRIGGER IF EXISTS trg_guard_consumer_inbox_entry_v9 ON medtrust.consumer_inbox_entries")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_consumer_inbox_entry_v9()")
    op.drop_table("consumer_inbox_entries", schema=SCHEMA)
