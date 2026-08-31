"""Add Phase 5.5 execution readiness and pre-dispatch job evidence.

Revision ID: 20260724_0030
Revises: 20260724_0029
Create Date: 2026-07-24
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260724_0030"
down_revision: str | None = "20260724_0029"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())
NEW_EVENT_TYPES = (
    "contract.readiness.revoked",
    "execution.eligibility.passed",
    "execution.eligibility.blocked",
    "execution.eligibility.invalidated",
    "compute.job.pre_dispatch_slot_reserved",
)
NEW_SUBJECT_TYPES = (
    "contract_readiness_revocation",
    "execution_eligibility",
    "execution_eligibility_invalidation",
)


def _constraint_values(connection, constraint_name: str, field: str) -> list[str]:
    definition = connection.execute(
        sa.text(
            """
            SELECT pg_get_constraintdef(c.oid)
              FROM pg_constraint c
              JOIN pg_class t ON t.oid=c.conrelid
              JOIN pg_namespace n ON n.oid=t.relnamespace
             WHERE n.nspname='medtrust'
               AND t.relname='audit_events'
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


def _replace_check(
    constraint_name: str, field: str, values: list[str]
) -> None:
    rendered = ",".join(f"'{value}'" for value in values)
    op.execute(
        f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {constraint_name}"
    )
    op.execute(
        "ALTER TABLE medtrust.audit_events "
        f"ADD CONSTRAINT {constraint_name} CHECK ({field} IN ({rendered}))"
    )


def _function_definition(connection, name: str) -> str:
    return connection.execute(
        sa.text(f"SELECT pg_get_functiondef('medtrust.{name}()'::regprocedure)")
    ).scalar_one()


def _audit_cases() -> str:
    return """
                WHEN 'contract.readiness.revoked' THEN
                    IF NEW.subject_type<>'contract_readiness_revocation' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_readiness_revocations r WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'execution.eligibility.passed' THEN
                    IF NEW.subject_type<>'execution_eligibility' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.execution_eligibility_snapshots e WHERE e.id=NEW.subject_id AND e.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'execution.eligibility.blocked' THEN
                    IF NEW.subject_type<>'contract_revision' OR NEW.result<>'denied' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.contract_revisions r JOIN medtrust.contracts c ON c.id=r.contract_id WHERE r.id=NEW.subject_id AND c.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'execution.eligibility.invalidated' THEN
                    IF NEW.subject_type<>'execution_eligibility_invalidation' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.execution_eligibility_invalidations i WHERE i.id=NEW.subject_id AND i.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'compute.job.pre_dispatch_slot_reserved' THEN
                    IF NEW.subject_type<>'compute_job' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid event catalog shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.compute_jobs j WHERE j.id=NEW.subject_id AND j.space_id=NEW.space_id) INTO v_subject_ok;
"""


def _create_tables() -> None:
    op.create_table(
        "contract_readiness_revocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("readiness_confirmation_id", sa.Uuid(), nullable=False),
        sa.Column("responsible_organization_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("evidence_snapshot", JSONB, nullable=False),
        sa.Column("evidence_digest", sa.String(71), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_contract_readiness_revocations_digest_format",
        ),
        sa.ForeignKeyConstraint(
            ["readiness_confirmation_id"],
            [f"{SCHEMA}.contract_readiness_confirmations.id"],
            name="fk_contract_readiness_revocations_confirmation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            [
                f"{SCHEMA}.space_participants.space_id",
                f"{SCHEMA}.space_participants.organization_id",
            ],
            name="fk_contract_readiness_revocation_responsible_participant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_organization_id", "revoked_by_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_contract_readiness_revocation_actor_member",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name="pk_contract_readiness_revocations"
        ),
        sa.UniqueConstraint(
            "readiness_confirmation_id",
            name="uq_contract_readiness_revocation_confirmation",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_readiness_revocation_space_time",
        "contract_readiness_revocations",
        ["space_id", sa.text("revoked_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "execution_eligibility_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_content_digest", sa.Text(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("data_readiness_id", sa.Uuid(), nullable=False),
        sa.Column("model_readiness_id", sa.Uuid(), nullable=False),
        sa.Column("platform_readiness_id", sa.Uuid(), nullable=False),
        sa.Column("check_matrix", JSONB, nullable=False),
        sa.Column("check_matrix_digest", sa.Text(), nullable=False),
        sa.Column("eligibility_snapshot", JSONB, nullable=False),
        sa.Column("eligibility_snapshot_digest", sa.Text(), nullable=False),
        sa.Column("execution_environment_snapshot", JSONB, nullable=False),
        sa.Column("execution_environment_digest", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "check_matrix_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "eligibility_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "execution_environment_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_execution_eligibility_snapshots_digest_formats",
        ),
        sa.CheckConstraint(
            "valid_until > created_at",
            name="ck_execution_eligibility_snapshots_validity_window",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_id", "space_id"],
            [f"{SCHEMA}.contracts.id", f"{SCHEMA}.contracts.space_id"],
            name="fk_execution_eligibility_contract_space",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "contract_id"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.contract_id",
            ],
            name="fk_execution_eligibility_revision_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "revision_content_digest"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.content_digest",
            ],
            name="fk_execution_eligibility_revision_digest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            [f"{SCHEMA}.applications.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_product_version_id"],
            [f"{SCHEMA}.data_product_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            [f"{SCHEMA}.model_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_readiness_id"],
            [f"{SCHEMA}.contract_readiness_confirmations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_readiness_id"],
            [f"{SCHEMA}.contract_readiness_confirmations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["platform_readiness_id"],
            [f"{SCHEMA}.contract_readiness_confirmations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name="pk_execution_eligibility_snapshots"
        ),
        sa.UniqueConstraint(
            "id", "space_id", name="uq_execution_eligibility_id_space"
        ),
        sa.UniqueConstraint(
            "eligibility_snapshot_digest",
            name="uq_execution_eligibility_snapshot_digest",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_eligibility_revision_created",
        "execution_eligibility_snapshots",
        ["contract_revision_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "execution_eligibility_invalidations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column(
            "execution_eligibility_snapshot_id", sa.Uuid(), nullable=False
        ),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("evidence_snapshot", JSONB, nullable=False),
        sa.Column("evidence_digest", sa.Text(), nullable=False),
        sa.Column(
            "invalidated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("invalidated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "evidence_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_execution_eligibility_invalidations_digest_format",
        ),
        sa.ForeignKeyConstraint(
            ["execution_eligibility_snapshot_id", "space_id"],
            [
                f"{SCHEMA}.execution_eligibility_snapshots.id",
                f"{SCHEMA}.execution_eligibility_snapshots.space_id",
            ],
            name="fk_execution_eligibility_invalidation_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invalidated_by"],
            [f"{SCHEMA}.users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name="pk_execution_eligibility_invalidations"
        ),
        sa.UniqueConstraint(
            "execution_eligibility_snapshot_id",
            name="uq_execution_eligibility_invalidation_snapshot",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_eligibility_invalidation_space_time",
        "execution_eligibility_invalidations",
        ["space_id", sa.text("invalidated_at DESC")],
        schema=SCHEMA,
    )


def _alter_compute_jobs() -> None:
    columns = (
        sa.Column("execution_eligibility_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("eligibility_snapshot_digest", sa.Text(), nullable=True),
        sa.Column("quota_policy_id", sa.Uuid(), nullable=True),
        sa.Column("run_count_constraint_id", sa.Uuid(), nullable=True),
        sa.Column("run_limit_snapshot", sa.Integer(), nullable=True),
        sa.Column("pre_dispatch_slot_ordinal", sa.Integer(), nullable=True),
        sa.Column("pre_dispatch_slot_digest", sa.Text(), nullable=True),
        sa.Column(
            "pre_dispatch_reserved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    for column in columns:
        op.add_column("compute_jobs", column, schema=SCHEMA)
    op.create_foreign_key(
        "fk_compute_jobs_eligibility_snapshot",
        "compute_jobs",
        "execution_eligibility_snapshots",
        ["execution_eligibility_snapshot_id", "space_id"],
        ["id", "space_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_compute_jobs_quota_policy_revision",
        "compute_jobs",
        "policies",
        ["contract_revision_id", "quota_policy_id"],
        ["contract_revision_id", "id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_compute_jobs_run_count_constraint",
        "compute_jobs",
        "policy_constraints",
        ["run_count_constraint_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_compute_jobs_phase55_slot_shape",
        "compute_jobs",
        "("
        "execution_eligibility_snapshot_id IS NULL AND "
        "eligibility_snapshot_digest IS NULL AND quota_policy_id IS NULL AND "
        "run_count_constraint_id IS NULL AND run_limit_snapshot IS NULL AND "
        "pre_dispatch_slot_ordinal IS NULL AND pre_dispatch_slot_digest IS NULL AND "
        "pre_dispatch_reserved_at IS NULL"
        ") OR ("
        "execution_eligibility_snapshot_id IS NOT NULL AND "
        "eligibility_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "quota_policy_id IS NOT NULL AND run_count_constraint_id IS NOT NULL AND "
        "run_limit_snapshot > 0 AND pre_dispatch_slot_ordinal > 0 AND "
        "pre_dispatch_slot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "pre_dispatch_reserved_at IS NOT NULL"
        ")",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_compute_jobs_pre_dispatch_slot",
        "compute_jobs",
        [
            "contract_revision_id",
            "quota_policy_id",
            "requester_contract_party_id",
            "contract_object_id",
            "pre_dispatch_slot_ordinal",
        ],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("pre_dispatch_slot_ordinal IS NOT NULL"),
    )


def _backfill_phase55_policy_bindings() -> None:
    connection = op.get_bind()
    op.execute(
        "ALTER TABLE medtrust.policy_execution_bindings "
        "DISABLE TRIGGER trg_policy_binding"
    )
    rows = connection.execute(
        sa.text(
            """
        WITH required_bindings AS (
            SELECT
                r.id AS revision_id,
                p.id AS policy_id,
                c.id AS connector_id,
                CASE
                    WHEN p.action_code='execute_controlled_compute'
                        THEN 'compute_executor'
                    WHEN p.action_code='export_artifact'
                        THEN 'egress_controller'
                    ELSE 'audit_evidence_emitter'
                END AS execution_role,
                CASE
                    WHEN p.action_code='execute_controlled_compute'
                        THEN 'controlled_compute_execution'
                    WHEN p.action_code='export_artifact'
                        THEN 'egress_policy_enforcement'
                    ELSE 'audit_evidence_emit'
                END AS capability_code
            FROM medtrust.contract_revisions r
            JOIN medtrust.contracts contract ON contract.id=r.contract_id
            JOIN medtrust.contract_parties party
              ON party.contract_revision_id=r.id
             AND party.party_role='data_provider'
            JOIN medtrust.policies p ON p.contract_revision_id=r.id
            JOIN LATERAL (
                SELECT connector.id
                FROM medtrust.connectors connector
                WHERE connector.space_id=contract.space_id
                  AND connector.owner_organization_id=party.organization_id
                  AND connector.verification_status='verified'
                  AND connector.runtime_status='online'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM (
                          VALUES
                              ('controlled_compute_execution'),
                              ('egress_policy_enforcement'),
                              ('audit_evidence_emit')
                      ) AS required(code)
                      WHERE NOT EXISTS (
                          SELECT 1
                          FROM medtrust.connector_capabilities capability
                          WHERE capability.connector_id=connector.id
                            AND capability.capability_code=required.code
                            AND capability.capability_version='1.0'
                            AND capability.status='verified'
                      )
                  )
                ORDER BY connector.created_at
                LIMIT 1
            ) c ON TRUE
            WHERE r.terms_schema_version='phase5.4/structured-contract/v1'
              AND (
                    (p.action_code='execute_controlled_compute' AND p.effect='permit')
                 OR (p.action_code='export_artifact' AND p.effect='permit')
                 OR (p.action_code='write_audit_log' AND p.effect='require')
              )
        )
        SELECT
            binding.policy_id,
            binding.connector_id,
            binding.execution_role,
            binding.capability_code
        FROM required_bindings binding
        WHERE NOT EXISTS (
            SELECT 1
            FROM medtrust.policy_execution_bindings existing
            WHERE existing.policy_id=binding.policy_id
              AND existing.execution_role=binding.execution_role
              AND existing.is_required
        )
        """
        )
    ).mappings().all()
    for row in rows:
        receipt = {
            "schema_version": "phase5.5/contract-binding/v1",
            "policy_id": str(row["policy_id"]),
            "connector_id": str(row["connector_id"]),
            "execution_role": row["execution_role"],
            "capability_code": row["capability_code"],
            "capability_version": "1.0",
        }
        rendered = json.dumps(
            receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        receipt_digest = f"sha256:{hashlib.sha256(rendered).hexdigest()}"
        connection.execute(
            sa.text(
                """
                INSERT INTO medtrust.policy_execution_bindings (
                    id, policy_id, connector_id, execution_role,
                    required_capability_code, required_capability_version,
                    is_required, deployment_status, deployed_at,
                    acknowledged_at, receipt_digest, created_at, updated_at,
                    row_version
                ) VALUES (
                    :id, :policy_id, :connector_id, :execution_role,
                    :capability_code, '1.0', true, 'accepted', now(), now(),
                    :receipt_digest, now(), now(), 1
                )
                """
            ),
            {
                "id": uuid4(),
                "policy_id": row["policy_id"],
                "connector_id": row["connector_id"],
                "execution_role": row["execution_role"],
                "capability_code": row["capability_code"],
                "receipt_digest": receipt_digest,
            },
        )
    op.execute(
        "ALTER TABLE medtrust.policy_execution_bindings "
        "ENABLE TRIGGER trg_policy_binding"
    )


def _remove_phase55_policy_bindings() -> None:
    op.execute(
        "ALTER TABLE medtrust.policy_execution_bindings "
        "DISABLE TRIGGER trg_policy_binding"
    )
    op.execute(
        """
        DELETE FROM medtrust.policy_execution_bindings binding
        USING medtrust.policies policy, medtrust.contract_revisions revision
        WHERE policy.id=binding.policy_id
          AND revision.id=policy.contract_revision_id
          AND revision.terms_schema_version='phase5.4/structured-contract/v1'
          AND (
                (policy.action_code='execute_controlled_compute'
                 AND binding.execution_role='compute_executor')
             OR (policy.action_code='export_artifact'
                 AND binding.execution_role='egress_controller')
             OR (policy.action_code='write_audit_log'
                 AND binding.execution_role='audit_evidence_emitter')
          )
        """
    )
    op.execute(
        "ALTER TABLE medtrust.policy_execution_bindings "
        "ENABLE TRIGGER trg_policy_binding"
    )


def _install_immutable_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_phase55_evidence_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Phase 5.5 evidence is append-only';
        END;
        $$;
        """
    )
    for table in (
        "contract_readiness_revocations",
        "execution_eligibility_snapshots",
        "execution_eligibility_invalidations",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable "
            f"BEFORE UPDATE OR DELETE ON medtrust.{table} "
            "FOR EACH ROW EXECUTE FUNCTION "
            "medtrust.guard_phase55_evidence_immutable()"
        )


def _extend_compute_job_guard(connection, *, enable: bool) -> None:
    definition = _function_definition(connection, "guard_compute_job_v7")
    old_new = """                   NEW.creation_request_digest, NEW.created_at, NEW.created_by)"""
    new_new = """                   NEW.creation_request_digest,
                   NEW.execution_eligibility_snapshot_id,
                   NEW.eligibility_snapshot_digest, NEW.quota_policy_id,
                   NEW.run_count_constraint_id, NEW.run_limit_snapshot,
                   NEW.pre_dispatch_slot_ordinal, NEW.pre_dispatch_slot_digest,
                   NEW.pre_dispatch_reserved_at, NEW.created_at, NEW.created_by)"""
    old_old = """                   OLD.creation_request_digest, OLD.created_at, OLD.created_by)"""
    new_old = """                   OLD.creation_request_digest,
                   OLD.execution_eligibility_snapshot_id,
                   OLD.eligibility_snapshot_digest, OLD.quota_policy_id,
                   OLD.run_count_constraint_id, OLD.run_limit_snapshot,
                   OLD.pre_dispatch_slot_ordinal, OLD.pre_dispatch_slot_digest,
                   OLD.pre_dispatch_reserved_at, OLD.created_at, OLD.created_by)"""
    source_new, target_new = (old_new, new_new) if enable else (new_new, old_new)
    source_old, target_old = (old_old, new_old) if enable else (new_old, old_old)
    if source_new not in definition or source_old not in definition:
        raise RuntimeError("expected ComputeJob immutable-row guard was not found")
    op.execute(
        definition.replace(source_new, target_new, 1).replace(
            source_old, target_old, 1
        )
    )


def _extend_audit_catalog(connection, *, enable: bool) -> None:
    event_constraint = "ck_audit_events_ck_audit_events_event_type"
    subject_constraint = "ck_audit_events_ck_audit_events_subject_type"
    events = _constraint_values(connection, event_constraint, "event_type")
    subjects = _constraint_values(connection, subject_constraint, "subject_type")
    if enable:
        for value in NEW_EVENT_TYPES:
            if value not in events:
                events.append(value)
        for value in NEW_SUBJECT_TYPES:
            if value not in subjects:
                subjects.append(value)
    else:
        events = [value for value in events if value not in NEW_EVENT_TYPES]
        subjects = [value for value in subjects if value not in NEW_SUBJECT_TYPES]
    _replace_check(event_constraint, "event_type", events)
    _replace_check(subject_constraint, "subject_type", subjects)

    audit_guard = _function_definition(connection, "guard_audit_event_v8")
    marker = "                WHEN 'contract.revision.proposed' THEN"
    cases = _audit_cases()
    if enable:
        if marker not in audit_guard:
            raise RuntimeError("expected audit guard insertion marker was not found")
        op.execute(audit_guard.replace(marker, cases + marker, 1))
    else:
        if cases not in audit_guard:
            raise RuntimeError("expected Phase 5.5 audit guard cases were not found")
        op.execute(audit_guard.replace(cases, "", 1))


def upgrade() -> None:
    connection = op.get_bind()
    _create_tables()
    _alter_compute_jobs()
    _backfill_phase55_policy_bindings()
    _install_immutable_guards()
    _extend_compute_job_guard(connection, enable=True)
    _extend_audit_catalog(connection, enable=True)


def downgrade() -> None:
    connection = op.get_bind()
    evidence_count = connection.execute(
        sa.text(
            """
            SELECT
              (SELECT count(*) FROM medtrust.contract_readiness_revocations) +
              (SELECT count(*) FROM medtrust.execution_eligibility_snapshots) +
              (SELECT count(*) FROM medtrust.execution_eligibility_invalidations) +
              (SELECT count(*) FROM medtrust.compute_jobs
                WHERE execution_eligibility_snapshot_id IS NOT NULL)
            """
        )
    ).scalar_one()
    event_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM medtrust.audit_events "
            "WHERE event_type = ANY(:event_types)"
        ),
        {"event_types": list(NEW_EVENT_TYPES)},
    ).scalar_one()
    if evidence_count or event_count:
        raise RuntimeError(
            "cannot remove Phase 5.5 while execution-readiness evidence exists"
        )
    _remove_phase55_policy_bindings()
    _extend_audit_catalog(connection, enable=False)
    _extend_compute_job_guard(connection, enable=False)
    for table in (
        "contract_readiness_revocations",
        "execution_eligibility_snapshots",
        "execution_eligibility_invalidations",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON medtrust.{table}"
        )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.guard_phase55_evidence_immutable()"
    )
    op.drop_index(
        "uq_compute_jobs_pre_dispatch_slot",
        table_name="compute_jobs",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_compute_jobs_phase55_slot_shape",
        "compute_jobs",
        schema=SCHEMA,
        type_="check",
    )
    for name in (
        "fk_compute_jobs_run_count_constraint",
        "fk_compute_jobs_quota_policy_revision",
        "fk_compute_jobs_eligibility_snapshot",
    ):
        op.drop_constraint(
            name, "compute_jobs", schema=SCHEMA, type_="foreignkey"
        )
    for column in (
        "pre_dispatch_reserved_at",
        "pre_dispatch_slot_digest",
        "pre_dispatch_slot_ordinal",
        "run_limit_snapshot",
        "run_count_constraint_id",
        "quota_policy_id",
        "eligibility_snapshot_digest",
        "execution_eligibility_snapshot_id",
    ):
        op.drop_column("compute_jobs", column, schema=SCHEMA)
    op.drop_index(
        "ix_execution_eligibility_invalidation_space_time",
        table_name="execution_eligibility_invalidations",
        schema=SCHEMA,
    )
    op.drop_table("execution_eligibility_invalidations", schema=SCHEMA)
    op.drop_index(
        "ix_execution_eligibility_revision_created",
        table_name="execution_eligibility_snapshots",
        schema=SCHEMA,
    )
    op.drop_table("execution_eligibility_snapshots", schema=SCHEMA)
    op.drop_index(
        "ix_contract_readiness_revocation_space_time",
        table_name="contract_readiness_revocations",
        schema=SCHEMA,
    )
    op.drop_table("contract_readiness_revocations", schema=SCHEMA)
