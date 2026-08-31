"""Create Contract Policy, Constraint, and execution Binding tables.

Revision ID: 20260722_0009
Revises: 20260722_0008
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0009"
down_revision: str | None = "20260722_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_check_constraint(
        "capability_verification_shape",
        "connector_capabilities",
        "(status <> 'declared' OR verified_at IS NULL) AND "
        "(status <> 'verified' OR verified_at IS NOT NULL)",
        schema=SCHEMA,
    )

    op.create_table(
        "policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("policy_code", sa.Text(), nullable=False),
        sa.Column("policy_type", sa.String(length=16), nullable=False),
        sa.Column("effect", sa.String(length=8), nullable=False),
        sa.Column("subject_contract_party_id", sa.Uuid(), nullable=False),
        sa.Column("contract_object_id", sa.Uuid(), nullable=False),
        sa.Column("action_code", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("policy_digest", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("policy_code <> ''", name="policy_code_nonempty"),
        sa.CheckConstraint(
            "(policy_type = 'permission' AND effect = 'permit') OR "
            "(policy_type = 'prohibition' AND effect = 'deny') OR "
            "(policy_type = 'obligation' AND effect = 'require')",
            name="type_effect_pair",
        ),
        sa.CheckConstraint(
            "action_code IN ('read_catalog_metadata','execute_controlled_compute',"
            "'export_artifact','export_raw_data','reidentify_subject',"
            "'redistribute_data','retain_intermediate','delete_intermediate',"
            "'write_audit_log')",
            name="action_code",
        ),
        sa.CheckConstraint("priority >= 0", name="priority_nonnegative"),
        sa.CheckConstraint(
            "policy_digest IS NULL OR policy_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="policy_digest_shape",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id"],
            [f"{SCHEMA}.contract_revisions.id"],
            name="fk_policies_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "subject_contract_party_id"],
            [
                f"{SCHEMA}.contract_parties.contract_revision_id",
                f"{SCHEMA}.contract_parties.id",
            ],
            name="fk_policies_subject_party_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "contract_object_id"],
            [
                f"{SCHEMA}.contract_objects.contract_revision_id",
                f"{SCHEMA}.contract_objects.id",
            ],
            name="fk_policies_object_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_policies_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policies"),
        sa.UniqueConstraint(
            "contract_revision_id", "policy_code", name="uq_policies_revision_code"
        ),
        sa.UniqueConstraint(
            "contract_revision_id", "id", name="uq_policies_revision_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_policies_revision_digest",
        "policies",
        ["contract_revision_id", "policy_digest"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("policy_digest IS NOT NULL"),
    )
    op.create_index(
        "ix_policies_subject_action",
        "policies",
        ["subject_contract_party_id", "action_code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policies_object_action",
        "policies",
        ["contract_object_id", "action_code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policies_revision_priority",
        "policies",
        ["contract_revision_id", sa.text("priority DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policies_created_by", "policies", ["created_by"], schema=SCHEMA
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.validate_policy_constraint_v1(
            p_name text, p_operator text, p_value jsonb, p_unit text
        ) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE
            sorted_unique jsonb;
            parsed_time timestamptz;
        BEGIN
            IF p_name IN ('purpose_code', 'output_type', 'region') THEN
                IF p_operator <> 'in' OR p_unit IS NOT NULL OR
                   jsonb_typeof(p_value) <> 'array' OR jsonb_array_length(p_value) = 0 OR
                   EXISTS (SELECT 1 FROM jsonb_array_elements(p_value) e
                           WHERE jsonb_typeof(e) <> 'string') THEN
                    RETURN false;
                END IF;
                SELECT jsonb_agg(to_jsonb(v) ORDER BY v) INTO sorted_unique
                  FROM (SELECT DISTINCT jsonb_array_elements_text(p_value) AS v) s;
                IF sorted_unique IS DISTINCT FROM p_value THEN RETURN false; END IF;
                IF p_name = 'purpose_code' AND EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(p_value) v
                    WHERE v NOT IN ('ai_training','model_validation',
                                    'research_analysis','drug_development')
                ) THEN RETURN false; END IF;
                IF p_name = 'output_type' AND EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(p_value) v
                    WHERE v NOT IN ('aggregate_statistics','model_artifact',
                                    'feature_dataset','risk_scoring_model')
                ) THEN RETURN false; END IF;
                RETURN true;
            ELSIF p_name = 'algorithm_digest' THEN
                RETURN p_operator = 'eq' AND p_unit IS NULL AND
                       jsonb_typeof(p_value) = 'string' AND
                       (p_value #>> '{}') ~ '^sha256:[0-9a-f]{64}$';
            ELSIF p_name = 'environment_mode' THEN
                RETURN p_operator = 'eq' AND p_unit IS NULL AND
                       p_value = '"controlled_compute"'::jsonb;
            ELSIF p_name = 'run_count' THEN
                RETURN p_operator = 'lte' AND p_unit = 'count' AND
                       jsonb_typeof(p_value) = 'number' AND
                       (p_value #>> '{}') ~ '^[1-9][0-9]*$';
            ELSIF p_name = 'effective_until' THEN
                IF p_operator <> 'before' OR p_unit IS NOT NULL OR
                   jsonb_typeof(p_value) <> 'string' OR
                   right(p_value #>> '{}', 1) <> 'Z' THEN RETURN false; END IF;
                BEGIN parsed_time := (p_value #>> '{}')::timestamptz;
                EXCEPTION WHEN others THEN RETURN false; END;
                RETURN true;
            ELSIF p_name = 'output_review_required' THEN
                RETURN p_operator = 'eq' AND p_unit IS NULL AND p_value = 'true'::jsonb;
            ELSIF p_name = 'retention_seconds' THEN
                RETURN p_operator = 'lte' AND p_unit = 'seconds' AND
                       jsonb_typeof(p_value) = 'number' AND
                       (p_value #>> '{}') ~ '^(0|[1-9][0-9]*)$';
            ELSIF p_name = 'network_zone' THEN
                RETURN p_operator = 'eq' AND p_unit IS NULL AND
                       jsonb_typeof(p_value) = 'string' AND length(p_value #>> '{}') > 0;
            ELSIF p_name = 'audit_level' THEN
                RETURN p_operator = 'gte' AND p_unit IS NULL AND p_value = '"full"'::jsonb;
            END IF;
            RETURN false;
        END;
        $$;
        """
    )

    op.create_table(
        "policy_constraints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("constraint_name", sa.String(length=32), nullable=False),
        sa.Column("operator", sa.String(length=8), nullable=False),
        sa.Column(
            "value", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("position_no", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "constraint_name IN ('purpose_code','algorithm_digest','environment_mode',"
            "'run_count','effective_until','output_type','output_review_required',"
            "'retention_seconds','region','network_zone','audit_level')",
            name="constraint_name",
        ),
        sa.CheckConstraint(
            "operator IN ('eq','in','lte','gte','before','after')", name="operator"
        ),
        sa.CheckConstraint("position_no > 0", name="position_no_positive"),
        sa.CheckConstraint(
            "medtrust.validate_policy_constraint_v1(constraint_name, operator, value, unit)",
            name="value_shape",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            [f"{SCHEMA}.policies.id"],
            name="fk_policy_constraints_policy",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_constraints"),
        sa.UniqueConstraint(
            "policy_id", "position_no", name="uq_policy_constraints_policy_pos"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policy_constraints_policy_name",
        "policy_constraints",
        ["policy_id", "constraint_name"],
        schema=SCHEMA,
    )

    op.create_table(
        "policy_execution_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("execution_role", sa.String(length=32), nullable=False),
        sa.Column("required_capability_code", sa.String(length=48), nullable=False),
        sa.Column(
            "required_capability_version",
            sa.String(length=16),
            server_default="1.0",
            nullable=False,
        ),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "deployment_status",
            sa.String(length=12),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_digest", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_receipt_digest", sa.Text(), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(execution_role = 'compute_executor' AND "
            "required_capability_code = 'controlled_compute_execution' AND "
            "required_capability_version = '1.0') OR "
            "(execution_role = 'egress_controller' AND "
            "required_capability_code = 'egress_policy_enforcement' AND "
            "required_capability_version = '1.0') OR "
            "(execution_role = 'audit_evidence_emitter' AND "
            "required_capability_code = 'audit_evidence_emit' AND "
            "required_capability_version = '1.0')",
            name="role_capability_pair",
        ),
        sa.CheckConstraint(
            "deployment_status IN ('pending','accepted','rejected','revoked')",
            name="deployment_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.CheckConstraint(
            "(deployment_status = 'pending' AND acknowledged_at IS NULL AND "
            "receipt_digest IS NULL AND rejection_reason IS NULL AND revoked_at IS NULL "
            "AND revocation_receipt_digest IS NULL AND revocation_reason IS NULL) OR "
            "(deployment_status = 'accepted' AND acknowledged_at IS NOT NULL AND "
            "receipt_digest IS NOT NULL AND rejection_reason IS NULL AND revoked_at IS NULL "
            "AND revocation_receipt_digest IS NULL AND revocation_reason IS NULL) OR "
            "(deployment_status = 'rejected' AND acknowledged_at IS NOT NULL AND "
            "receipt_digest IS NULL AND length(rejection_reason) > 0 AND revoked_at IS NULL "
            "AND revocation_receipt_digest IS NULL AND revocation_reason IS NULL) OR "
            "(deployment_status = 'revoked' AND acknowledged_at IS NOT NULL AND "
            "receipt_digest IS NOT NULL AND rejection_reason IS NULL AND revoked_at IS NOT NULL "
            "AND revocation_receipt_digest IS NOT NULL AND length(revocation_reason) > 0)",
            name="deployment_shape",
        ),
        sa.CheckConstraint(
            "receipt_digest IS NULL OR receipt_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="receipt_digest_shape",
        ),
        sa.CheckConstraint(
            "revocation_receipt_digest IS NULL OR "
            "revocation_receipt_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="revocation_receipt_digest_shape",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            [f"{SCHEMA}.policies.id"],
            name="fk_policy_execution_bindings_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id", "required_capability_code", "required_capability_version"],
            [
                f"{SCHEMA}.connector_capabilities.connector_id",
                f"{SCHEMA}.connector_capabilities.capability_code",
                f"{SCHEMA}.connector_capabilities.capability_version",
            ],
            name="fk_policy_bindings_connector_capability",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_execution_bindings"),
        sa.UniqueConstraint(
            "policy_id",
            "connector_id",
            "execution_role",
            "required_capability_code",
            "required_capability_version",
            name="uq_policy_bindings_spec",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policy_bindings_connector_status_deployed",
        "policy_execution_bindings",
        ["connector_id", "deployment_status", sa.text("deployed_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policy_bindings_policy_status",
        "policy_execution_bindings",
        ["policy_id", "deployment_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policy_bindings_capability_status",
        "policy_execution_bindings",
        ["required_capability_code", "required_capability_version", "deployment_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_policy_bindings_pending",
        "policy_execution_bindings",
        ["policy_id", "connector_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("deployment_status = 'pending'"),
    )

    _create_structure_guards()
    _replace_revision_guard_for_policy()


def _create_structure_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_policy_structure()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            SELECT status INTO parent_status FROM medtrust.contract_revisions
             WHERE id = COALESCE(NEW.contract_revision_id, OLD.contract_revision_id);
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'policies can only change in draft';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_policy_structure "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.policies "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_policy_structure()"
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_policy_constraint_structure()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            SELECT r.status INTO parent_status
              FROM medtrust.policies p
              JOIN medtrust.contract_revisions r ON r.id = p.contract_revision_id
             WHERE p.id = COALESCE(NEW.policy_id, OLD.policy_id);
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'policy constraints can only change in draft';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_policy_constraint_structure "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.policy_constraints "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_policy_constraint_structure()"
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_policy_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            SELECT r.status INTO parent_status
              FROM medtrust.policies p
              JOIN medtrust.contract_revisions r ON r.id = p.contract_revision_id
             WHERE p.id = COALESCE(NEW.policy_id, OLD.policy_id);
            IF TG_OP IN ('INSERT','DELETE') AND parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'policy bindings can only be added or removed in draft';
            END IF;
            IF TG_OP = 'UPDATE' AND parent_status IS DISTINCT FROM 'draft' THEN
                IF ROW(NEW.policy_id, NEW.connector_id, NEW.execution_role,
                       NEW.required_capability_code, NEW.required_capability_version,
                       NEW.is_required, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.policy_id, OLD.connector_id, OLD.execution_role,
                       OLD.required_capability_code, OLD.required_capability_version,
                       OLD.is_required, OLD.created_at) THEN
                    RAISE EXCEPTION 'binding specification is immutable';
                END IF;
                IF NOT ((OLD.deployment_status = 'pending' AND
                         NEW.deployment_status IN ('accepted','rejected')) OR
                        (OLD.deployment_status = 'accepted' AND
                         NEW.deployment_status = 'revoked') OR
                        OLD.deployment_status = NEW.deployment_status) THEN
                    RAISE EXCEPTION 'illegal binding deployment transition';
                END IF;
                IF OLD.deployment_status <> NEW.deployment_status AND
                   NEW.row_version <> OLD.row_version + 1 THEN
                    RAISE EXCEPTION 'binding transition must increment row_version';
                END IF;
                IF OLD.deployment_status = NEW.deployment_status AND
                   OLD.deployment_status <> 'pending' AND
                   ROW(NEW.deployed_at, NEW.acknowledged_at, NEW.receipt_digest,
                       NEW.rejection_reason, NEW.revoked_at,
                       NEW.revocation_receipt_digest, NEW.revocation_reason,
                       NEW.row_version)
                   IS DISTINCT FROM
                   ROW(OLD.deployed_at, OLD.acknowledged_at, OLD.receipt_digest,
                       OLD.rejection_reason, OLD.revoked_at,
                       OLD.revocation_receipt_digest, OLD.revocation_reason,
                       OLD.row_version) THEN
                    RAISE EXCEPTION 'binding evidence is immutable within a state';
                END IF;
                IF OLD.deployment_status = 'accepted' AND
                   NEW.deployment_status = 'revoked' AND
                   ROW(NEW.deployed_at, NEW.acknowledged_at, NEW.receipt_digest)
                   IS DISTINCT FROM
                   ROW(OLD.deployed_at, OLD.acknowledged_at, OLD.receipt_digest) THEN
                    RAISE EXCEPTION 'accepted binding receipt is immutable';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_policy_binding "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.policy_execution_bindings "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_policy_binding()"
    )


def _replace_revision_guard_for_policy() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_contract_revision_core()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'draft' THEN
                    RAISE EXCEPTION 'new contract revision must start as draft';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION 'only an unproposed draft revision can be deleted';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'proposed or terminal revision is immutable in D1';
            END IF;
            IF NEW.status = 'draft' THEN RETURN NEW; END IF;
            IF NEW.status = 'withdrawn' THEN
                IF NEW.ended_at IS NULL THEN
                    RAISE EXCEPTION 'withdrawn revision requires ended_at';
                END IF;
                IF ROW(NEW.contract_id, NEW.revision_no, NEW.supersedes_revision_id,
                       NEW.name, NEW.summary, NEW.terms_schema_version,
                       NEW.terms_document, NEW.terms_digest, NEW.signing_mode,
                       NEW.effective_from, NEW.effective_until,
                       NEW.handoff_guard_evidence, NEW.handoff_guard_digest,
                       NEW.content_digest, NEW.created_at, NEW.created_by)
                   IS DISTINCT FROM
                   ROW(OLD.contract_id, OLD.revision_no, OLD.supersedes_revision_id,
                       OLD.name, OLD.summary, OLD.terms_schema_version,
                       OLD.terms_document, OLD.terms_digest, OLD.signing_mode,
                       OLD.effective_from, OLD.effective_until,
                       OLD.handoff_guard_evidence, OLD.handoff_guard_digest,
                       OLD.content_digest, OLD.created_at, OLD.created_by) THEN
                    RAISE EXCEPTION 'withdrawing a draft cannot change revision content';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.status <> 'proposed' THEN
                RAISE EXCEPTION 'signed and active transitions are unavailable until 0010';
            END IF;
            IF NEW.proposed_at IS NULL OR NEW.handoff_guard_evidence IS NULL OR
               NEW.handoff_guard_digest IS NULL OR NEW.content_digest IS NULL THEN
                RAISE EXCEPTION 'proposal evidence and digests are required';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM medtrust.contract_parties
                           WHERE contract_revision_id = NEW.id AND party_role = 'provider') OR
               NOT EXISTS (SELECT 1 FROM medtrust.contract_parties
                           WHERE contract_revision_id = NEW.id AND party_role = 'consumer') OR
               NOT EXISTS (SELECT 1 FROM medtrust.contract_objects
                           WHERE contract_revision_id = NEW.id) THEN
                RAISE EXCEPTION 'proposal requires provider, consumer, and object';
            END IF;
            IF EXISTS (SELECT 1 FROM medtrust.policies
                       WHERE contract_revision_id = NEW.id AND policy_digest IS NULL) THEN
                RAISE EXCEPTION 'all policies require a digest';
            END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.contract_parties cp
                CROSS JOIN medtrust.contract_objects co
                WHERE cp.contract_revision_id = NEW.id AND cp.party_role = 'consumer'
                  AND co.contract_revision_id = NEW.id
                  AND (
                    NOT EXISTS (SELECT 1 FROM medtrust.policies p WHERE p.contract_revision_id=NEW.id AND p.subject_contract_party_id=cp.id AND p.contract_object_id=co.id AND p.policy_type='permission' AND p.effect='permit' AND p.action_code='execute_controlled_compute') OR
                    NOT EXISTS (SELECT 1 FROM medtrust.policies p WHERE p.contract_revision_id=NEW.id AND p.subject_contract_party_id=cp.id AND p.contract_object_id=co.id AND p.policy_type='prohibition' AND p.effect='deny' AND p.action_code='export_raw_data') OR
                    NOT EXISTS (SELECT 1 FROM medtrust.policies p WHERE p.contract_revision_id=NEW.id AND p.subject_contract_party_id=cp.id AND p.contract_object_id=co.id AND p.policy_type='prohibition' AND p.effect='deny' AND p.action_code='reidentify_subject') OR
                    NOT EXISTS (SELECT 1 FROM medtrust.policies p WHERE p.contract_revision_id=NEW.id AND p.subject_contract_party_id=cp.id AND p.contract_object_id=co.id AND p.policy_type='prohibition' AND p.effect='deny' AND p.action_code='redistribute_data') OR
                    NOT EXISTS (SELECT 1 FROM medtrust.policies p WHERE p.contract_revision_id=NEW.id AND p.subject_contract_party_id=cp.id AND p.contract_object_id=co.id AND p.policy_type='obligation' AND p.effect='require' AND p.action_code='write_audit_log')
                  )
            ) THEN RAISE EXCEPTION 'minimum policy set is incomplete'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.policy_constraints pc
                JOIN medtrust.policies p ON p.id=pc.policy_id
                JOIN medtrust.contract_revisions r ON r.id=p.contract_revision_id
                JOIN medtrust.contracts c ON c.id=r.contract_id
                CROSS JOIN LATERAL jsonb_array_elements_text(pc.value) requested(value)
                WHERE p.contract_revision_id=NEW.id AND pc.constraint_name='purpose_code'
                  AND NOT EXISTS (
                      SELECT 1 FROM medtrust.application_requested_actions a
                      WHERE a.application_id=c.application_id AND a.action_code=requested.value
                  )
            ) THEN RAISE EXCEPTION 'policy purpose expands the application'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.policy_constraints pc
                JOIN medtrust.policies p ON p.id=pc.policy_id
                JOIN medtrust.contract_revisions r ON r.id=p.contract_revision_id
                JOIN medtrust.contracts c ON c.id=r.contract_id
                CROSS JOIN LATERAL jsonb_array_elements_text(pc.value) requested(value)
                WHERE p.contract_revision_id=NEW.id AND pc.constraint_name='output_type'
                  AND NOT EXISTS (
                      SELECT 1 FROM medtrust.application_requested_output_types o
                      WHERE o.application_id=c.application_id AND o.output_type=requested.value
                  )
            ) THEN RAISE EXCEPTION 'policy output expands the application'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.policies p
                WHERE p.contract_revision_id=NEW.id AND p.action_code='export_artifact'
                  AND p.effect='permit' AND NOT EXISTS (
                      SELECT 1 FROM medtrust.policy_constraints pc
                      WHERE pc.policy_id=p.id AND pc.constraint_name='output_type'
                  )
            ) THEN RAISE EXCEPTION 'artifact export requires output_type constraint'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.policy_execution_bindings b
                JOIN medtrust.policies p ON p.id=b.policy_id
                JOIN medtrust.connector_capabilities cap
                  ON cap.connector_id=b.connector_id
                 AND cap.capability_code=b.required_capability_code
                 AND cap.capability_version=b.required_capability_version
                JOIN medtrust.connectors cn ON cn.id=b.connector_id
                JOIN medtrust.contract_revisions r ON r.id=p.contract_revision_id
                JOIN medtrust.contracts c ON c.id=r.contract_id
                WHERE p.contract_revision_id=NEW.id AND b.is_required
                  AND (cap.status <> 'verified' OR cap.verified_at IS NULL OR
                       cn.verification_status <> 'verified' OR cn.space_id <> c.space_id OR
                       (b.required_capability_code='controlled_compute_execution' AND NOT (
                          cap.parameters @> jsonb_build_object(
                            'environment_modes', jsonb_build_array('controlled_compute'),
                            'algorithm_digest_enforced', true,
                            'run_count_enforced', true,
                            'effective_window_enforced', true))) OR
                       (b.required_capability_code='egress_policy_enforcement' AND NOT (
                          cap.parameters @> jsonb_build_object(
                            'raw_export_denied', true,
                            'artifact_review_gate', true,
                            'output_type_filter', true))) OR
                       (b.required_capability_code='audit_evidence_emit' AND NOT (
                          cap.parameters @> jsonb_build_object(
                            'audit_levels', jsonb_build_array('full'),
                            'digest_algorithm', 'sha256',
                            'failure_mode', 'fail_closed'))) OR
                       NOT EXISTS (SELECT 1 FROM medtrust.contract_parties cp
                                   WHERE cp.contract_revision_id=NEW.id
                                     AND cp.organization_id=cn.owner_organization_id
                                     AND cp.party_role IN ('provider','service_provider','operator_witness')))
            ) THEN RAISE EXCEPTION 'required connector capability is not verified'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.policies p
                WHERE p.contract_revision_id=NEW.id AND (
                    (p.action_code='execute_controlled_compute' AND p.effect='permit' AND NOT EXISTS (SELECT 1 FROM medtrust.policy_execution_bindings b WHERE b.policy_id=p.id AND b.is_required AND b.execution_role='compute_executor')) OR
                    (p.action_code IN ('export_artifact','export_raw_data','redistribute_data') AND NOT EXISTS (SELECT 1 FROM medtrust.policy_execution_bindings b WHERE b.policy_id=p.id AND b.is_required AND b.execution_role='egress_controller')) OR
                    (p.action_code='reidentify_subject' AND (NOT EXISTS (SELECT 1 FROM medtrust.policy_execution_bindings b WHERE b.policy_id=p.id AND b.is_required AND b.execution_role='compute_executor') OR NOT EXISTS (SELECT 1 FROM medtrust.policy_execution_bindings b WHERE b.policy_id=p.id AND b.is_required AND b.execution_role='egress_controller'))) OR
                    (p.action_code='write_audit_log' AND p.effect='require' AND NOT EXISTS (SELECT 1 FROM medtrust.policy_execution_bindings b WHERE b.policy_id=p.id AND b.is_required AND b.execution_role='audit_evidence_emitter')) OR
                    (p.action_code IN ('retain_intermediate','delete_intermediate') AND p.effect='require' AND NOT EXISTS (SELECT 1 FROM medtrust.policy_execution_bindings b WHERE b.policy_id=p.id AND b.is_required AND b.execution_role='compute_executor'))
                )
            ) THEN RAISE EXCEPTION 'required execution binding is missing'; END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def downgrade() -> None:
    _restore_0008_revision_guard()
    op.execute("DROP TRIGGER IF EXISTS trg_policy_binding ON medtrust.policy_execution_bindings")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_policy_binding()")
    op.execute("DROP TRIGGER IF EXISTS trg_policy_constraint_structure ON medtrust.policy_constraints")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_policy_constraint_structure()")
    op.execute("DROP TRIGGER IF EXISTS trg_policy_structure ON medtrust.policies")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_policy_structure()")
    op.drop_table("policy_execution_bindings", schema=SCHEMA)
    op.drop_table("policy_constraints", schema=SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS medtrust.validate_policy_constraint_v1(text,text,jsonb,text)")
    op.drop_table("policies", schema=SCHEMA)
    op.drop_constraint(
        "capability_verification_shape",
        "connector_capabilities",
        schema=SCHEMA,
        type_="check",
    )


def _restore_0008_revision_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_contract_revision_core()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'draft' THEN RAISE EXCEPTION 'new contract revision must start as draft'; END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'only an unproposed draft revision can be deleted'; END IF;
                RETURN OLD;
            END IF;
            IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'proposed or terminal revision is immutable in B1'; END IF;
            IF NEW.status = 'draft' THEN RETURN NEW; END IF;
            IF NEW.status = 'withdrawn' THEN
                IF NEW.ended_at IS NULL THEN RAISE EXCEPTION 'withdrawn revision requires ended_at'; END IF;
                IF ROW(NEW.contract_id, NEW.revision_no, NEW.supersedes_revision_id,
                       NEW.name, NEW.summary, NEW.terms_schema_version,
                       NEW.terms_document, NEW.terms_digest, NEW.signing_mode,
                       NEW.effective_from, NEW.effective_until,
                       NEW.handoff_guard_evidence, NEW.handoff_guard_digest,
                       NEW.content_digest, NEW.created_at, NEW.created_by)
                   IS DISTINCT FROM
                   ROW(OLD.contract_id, OLD.revision_no, OLD.supersedes_revision_id,
                       OLD.name, OLD.summary, OLD.terms_schema_version,
                       OLD.terms_document, OLD.terms_digest, OLD.signing_mode,
                       OLD.effective_from, OLD.effective_until,
                       OLD.handoff_guard_evidence, OLD.handoff_guard_digest,
                       OLD.content_digest, OLD.created_at, OLD.created_by) THEN
                    RAISE EXCEPTION 'withdrawing a draft cannot change revision content';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'proposal is unavailable until Policy and Binding exist';
        END;
        $$;
        """
    )
