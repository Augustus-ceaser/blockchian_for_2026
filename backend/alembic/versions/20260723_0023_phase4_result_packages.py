"""Add safe result packages, download grants and Phase 4 audit catalog.

Revision ID: 20260723_0023
Revises: 20260723_0022
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0023"
down_revision: str | None = "20260723_0022"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())

BASE_EVENT_TYPES = (
    "contract.revision.activated",
    "compute.job.created",
    "compute.run.reserved",
    "compute.run.dispatched",
    "compute.run.started",
    "compute.run.completed",
    "compute.run.failed",
    "compute.run.interrupted",
    "artifact.created",
    "artifact.review.decided",
    "artifact.released",
)
PHASE4_EVENT_TYPES = (
    "data_product.version.submitted",
    "data_product.version.approved",
    "data_product.version.published",
    "model_product.version.submitted",
    "model_product.version.approved",
    "model_product.version.published",
    "application.submitted",
    "application.review.decided",
    "contract.revision.proposed",
    "contract.revision.signed",
    "contract.readiness.confirmed",
    "artifact.multiparty_review.decided",
    "result.package.created",
    "result.download.grant.created",
    "result.download.completed",
)
BASE_SUBJECT_TYPES = (
    "contract_revision",
    "compute_job",
    "compute_run",
    "artifact",
    "artifact_review",
)
PHASE4_SUBJECT_TYPES = (
    "data_product_version",
    "model_version",
    "application",
    "review_decision",
    "contract_readiness",
    "artifact_review_decision",
    "result_package",
    "result_download_grant",
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _install_audit_guard(*, include_phase4: bool) -> None:
    phase4_cases = ""
    allowed_services = "'medtrust.contract','medtrust.compute','medtrust.artifact','medtrust.audit'"
    if include_phase4:
        allowed_services += ",'medtrust.marketplace'"
        phase4_cases = """
                WHEN 'data_product.version.submitted' THEN
                    IF NEW.subject_type<>'data_product_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.data_product_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'data_product.version.approved' THEN
                    IF NEW.subject_type<>'data_product_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.data_product_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'data_product.version.published' THEN
                    IF NEW.subject_type<>'data_product_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.data_product_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'model_product.version.submitted' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'model_product.version.approved' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'model_product.version.published' THEN
                    IF NEW.subject_type<>'model_version' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.model_versions v WHERE v.id=NEW.subject_id AND v.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'application.submitted' THEN
                    IF NEW.subject_type<>'application' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.applications a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'application.review.decided' THEN
                    IF NEW.subject_type<>'review_decision' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.review_decisions d JOIN medtrust.review_tasks t ON t.id=d.review_task_id WHERE d.id=NEW.subject_id AND t.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'contract.revision.proposed' THEN
                    IF NEW.subject_type<>'contract_revision' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_revisions r JOIN medtrust.contracts c ON c.id=r.contract_id WHERE r.id=NEW.subject_id AND c.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'contract.revision.signed' THEN
                    IF NEW.subject_type<>'contract_revision' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_revisions r JOIN medtrust.contracts c ON c.id=r.contract_id WHERE r.id=NEW.subject_id AND c.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'contract.readiness.confirmed' THEN
                    IF NEW.subject_type<>'contract_readiness' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_readiness_confirmations r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'artifact.multiparty_review.decided' THEN
                    IF NEW.subject_type<>'artifact_review_decision' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.artifact_review_decisions d JOIN medtrust.artifact_review_tasks t ON t.id=d.artifact_review_task_id WHERE d.id=NEW.subject_id AND t.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'result.package.created' THEN
                    IF NEW.subject_type<>'result_package' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.approved_result_packages p WHERE p.id=NEW.subject_id AND p.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'result.download.grant.created' THEN
                    IF NEW.subject_type<>'result_download_grant' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.result_download_grants g WHERE g.id=NEW.subject_id AND g.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'result.download.completed' THEN
                    IF NEW.subject_type<>'result_download_grant' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.result_download_grants g WHERE g.id=NEW.subject_id AND g.space_id=NEW.space_id) INTO v_subject_ok;
        """
    op.execute(
        f"""
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
            ELSIF NEW.actor_type='system' AND NEW.actor_service_code NOT IN ({allowed_services}) THEN
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
                WHEN 'compute.run.dispatched' THEN
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
                WHEN 'artifact.released' THEN
                    IF NEW.subject_type<>'artifact' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.artifacts a WHERE a.id=NEW.subject_id AND a.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'artifact.review.decided' THEN
                    IF NEW.subject_type<>'artifact_review' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.artifact_reviews r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                {phase4_cases}
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


def _replace_audit_constraints(*, include_phase4: bool) -> None:
    event_types = BASE_EVENT_TYPES + (PHASE4_EVENT_TYPES if include_phase4 else ())
    subject_types = BASE_SUBJECT_TYPES + (PHASE4_SUBJECT_TYPES if include_phase4 else ())
    op.drop_constraint(
        op.f("ck_audit_events_ck_audit_events_event_type"),
        "audit_events",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_ck_audit_events_event_type"),
        "audit_events",
        f"event_type IN ({_sql_values(event_types)})",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_audit_events_ck_audit_events_subject_type"),
        "audit_events",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_ck_audit_events_subject_type"),
        "audit_events",
        f"subject_type IN ({_sql_values(subject_types)})",
        schema=SCHEMA,
    )
    _install_audit_guard(include_phase4=include_phase4)


def upgrade() -> None:
    op.create_table(
        "approved_result_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="available", nullable=False),
        sa.Column("package_digest", sa.String(71), nullable=False),
        sa.Column("manifest_snapshot", JSONB, nullable=False),
        sa.Column("review_evidence_digest", sa.String(71), nullable=False),
        sa.Column("authority_evaluation_digest", sa.String(71), nullable=False),
        sa.Column("bucket_name", sa.String(63), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('available','revoked')", name="ck_approved_result_packages_status"),
        sa.CheckConstraint(
            "package_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "review_evidence_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "authority_evaluation_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_approved_result_packages_digest_formats",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_approved_result_packages_size_positive"),
        sa.CheckConstraint(
            "(status = 'available' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_approved_result_packages_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], [f"{SCHEMA}.artifacts.id"], name="fk_result_packages_artifact", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["space_id"], [f"{SCHEMA}.spaces.id"], name="fk_result_packages_space", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_approved_result_packages"),
        sa.UniqueConstraint("artifact_id", name="uq_result_packages_artifact"),
        sa.UniqueConstraint("space_id", "id", name="uq_result_packages_space_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_result_packages_requester_status",
        "approved_result_packages",
        ["requester_organization_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "result_download_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_digest", sa.String(71), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("max_downloads", sa.Integer(), nullable=False),
        sa.Column("download_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','exhausted','expired','revoked')",
            name="ck_result_download_grants_status",
        ),
        sa.CheckConstraint(
            "token_digest ~ '^sha256:[0-9a-f]{64}$' AND request_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_result_download_grants_digest_formats",
        ),
        sa.CheckConstraint("max_downloads > 0", name="ck_result_download_grants_max_downloads_positive"),
        sa.CheckConstraint(
            "download_count >= 0 AND download_count <= max_downloads",
            name="ck_result_download_grants_download_count_range",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_result_download_grants_expires_after_created"),
        sa.ForeignKeyConstraint(
            ["result_package_id", "space_id"],
            [f"{SCHEMA}.approved_result_packages.id", f"{SCHEMA}.approved_result_packages.space_id"],
            name="fk_result_download_grants_package_space", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requester_organization_id", "requester_user_id"],
            [f"{SCHEMA}.organization_members.organization_id", f"{SCHEMA}.organization_members.user_id"],
            name="fk_result_download_grants_requester_member", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_result_download_grants"),
        sa.UniqueConstraint("token_digest", name="uq_result_download_grants_token"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_result_download_grants_package_status",
        "result_download_grants",
        ["result_package_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_result_download_grants_expiry",
        "result_download_grants",
        ["status", "expires_at"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_phase4_result_package()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'result package cannot be deleted' USING ERRCODE='55000'; END IF;
            IF TG_OP='INSERT' THEN
                IF NOT EXISTS(SELECT 1 FROM medtrust.artifacts a WHERE a.id=NEW.artifact_id AND a.space_id=NEW.space_id AND a.release_status='quarantined') THEN
                    RAISE EXCEPTION 'result package Artifact is missing, cross-space or not quarantined' USING ERRCODE='23514';
                END IF;
                IF EXISTS(
                    SELECT 1 FROM medtrust.artifact_review_tasks t
                    LEFT JOIN medtrust.artifact_review_decisions d ON d.artifact_review_task_id=t.id
                    WHERE t.artifact_id=NEW.artifact_id AND t.is_required
                      AND (t.status<>'decided' OR d.decision IS DISTINCT FROM 'approved')
                ) OR NOT EXISTS(
                    SELECT 1 FROM medtrust.artifact_review_tasks t
                    WHERE t.artifact_id=NEW.artifact_id AND t.is_required
                ) THEN
                    RAISE EXCEPTION 'all required Artifact reviews must approve before packaging' USING ERRCODE='23514';
                END IF;
            ELSE
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.space_id IS DISTINCT FROM OLD.space_id OR NEW.artifact_id IS DISTINCT FROM OLD.artifact_id OR NEW.requester_organization_id IS DISTINCT FROM OLD.requester_organization_id OR NEW.package_digest IS DISTINCT FROM OLD.package_digest OR NEW.manifest_snapshot IS DISTINCT FROM OLD.manifest_snapshot OR NEW.review_evidence_digest IS DISTINCT FROM OLD.review_evidence_digest OR NEW.authority_evaluation_digest IS DISTINCT FROM OLD.authority_evaluation_digest OR NEW.bucket_name IS DISTINCT FROM OLD.bucket_name OR NEW.object_key IS DISTINCT FROM OLD.object_key OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes OR NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
                    RAISE EXCEPTION 'result package identity is immutable' USING ERRCODE='55000';
                END IF;
                IF OLD.status<>'available' OR NEW.status<>'revoked' OR NEW.revoked_at IS NULL THEN
                    RAISE EXCEPTION 'invalid result package transition' USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_phase4_result_package
        BEFORE INSERT OR UPDATE OR DELETE ON medtrust.approved_result_packages
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_phase4_result_package()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_phase4_download_grant()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'download grant cannot be deleted' USING ERRCODE='55000'; END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.status<>'active' OR NEW.download_count<>0 OR NEW.last_downloaded_at IS NOT NULL OR NEW.revoked_at IS NOT NULL THEN
                    RAISE EXCEPTION 'download grant must start active and unused' USING ERRCODE='23514';
                END IF;
                IF NOT EXISTS(SELECT 1 FROM medtrust.approved_result_packages p WHERE p.id=NEW.result_package_id AND p.space_id=NEW.space_id AND p.requester_organization_id=NEW.requester_organization_id AND p.status='available') THEN
                    RAISE EXCEPTION 'download grant package is unavailable or outside requester scope' USING ERRCODE='23514';
                END IF;
            ELSE
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.space_id IS DISTINCT FROM OLD.space_id OR NEW.result_package_id IS DISTINCT FROM OLD.result_package_id OR NEW.requester_organization_id IS DISTINCT FROM OLD.requester_organization_id OR NEW.requester_user_id IS DISTINCT FROM OLD.requester_user_id OR NEW.token_digest IS DISTINCT FROM OLD.token_digest OR NEW.request_digest IS DISTINCT FROM OLD.request_digest OR NEW.max_downloads IS DISTINCT FROM OLD.max_downloads OR NEW.expires_at IS DISTINCT FROM OLD.expires_at OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'download grant identity is immutable' USING ERRCODE='55000';
                END IF;
                IF NEW.download_count<OLD.download_count OR NEW.download_count>OLD.download_count+1 THEN
                    RAISE EXCEPTION 'download count must advance atomically by at most one' USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_phase4_download_grant
        BEFORE INSERT OR UPDATE OR DELETE ON medtrust.result_download_grants
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_phase4_download_grant()
        """
    )
    _replace_audit_constraints(include_phase4=True)


def downgrade() -> None:
    _replace_audit_constraints(include_phase4=False)
    op.execute("DROP TRIGGER IF EXISTS trg_guard_phase4_download_grant ON medtrust.result_download_grants")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_phase4_download_grant()")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_phase4_result_package ON medtrust.approved_result_packages")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_phase4_result_package()")
    op.drop_table("result_download_grants", schema=SCHEMA)
    op.drop_table("approved_result_packages", schema=SCHEMA)
