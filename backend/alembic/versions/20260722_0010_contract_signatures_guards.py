"""Create demo Contract signatures and signed/active lifecycle guards.

Revision ID: 20260722_0010
Revises: 20260722_0009
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0010"
down_revision: str | None = "20260722_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "contract_signatures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("contract_party_id", sa.Uuid(), nullable=False),
        sa.Column("signer_organization_id", sa.Uuid(), nullable=False),
        sa.Column("signer_user_id", sa.Uuid(), nullable=False),
        sa.Column("signature_type", sa.String(length=16), server_default="demo", nullable=False),
        sa.Column("signature_value_ref", sa.Text(), nullable=False),
        sa.Column("signed_content_digest", sa.Text(), nullable=False),
        sa.Column("authority_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verification_status", sa.String(length=16), server_default="verified", nullable=False),
        sa.Column("signature_digest", sa.Text(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("signature_type = 'demo'", name="signature_type"),
        sa.CheckConstraint("verification_status = 'verified'", name="verification_status"),
        sa.CheckConstraint("length(signature_value_ref) > 0", name="value_ref_nonempty"),
        sa.CheckConstraint("jsonb_typeof(authority_snapshot) = 'object'", name="authority_snapshot_object"),
        sa.CheckConstraint("signed_content_digest ~ '^sha256:[0-9a-f]{64}$'", name="signed_content_digest_format"),
        sa.CheckConstraint("signature_digest ~ '^sha256:[0-9a-f]{64}$'", name="signature_digest_format"),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "contract_party_id", "signer_organization_id"],
            [
                "medtrust.contract_parties.contract_revision_id",
                "medtrust.contract_parties.id",
                "medtrust.contract_parties.organization_id",
            ],
            name="fk_contract_signatures_party_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id", "signed_content_digest"],
            [
                "medtrust.contract_revisions.id",
                "medtrust.contract_revisions.content_digest",
            ],
            name="fk_contract_signatures_revision_digest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signer_organization_id", "signer_user_id"],
            [
                "medtrust.organization_members.organization_id",
                "medtrust.organization_members.user_id",
            ],
            name="fk_contract_signatures_signer_member",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contract_signatures"),
        sa.UniqueConstraint("signature_digest", name="uq_contract_signatures_digest"),
        sa.UniqueConstraint(
            "contract_party_id",
            "signed_content_digest",
            name="uq_contract_signatures_party_content",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_signatures_signer_signed",
        "contract_signatures",
        ["signer_user_id", sa.text("signed_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_signatures_revision_content",
        "contract_signatures",
        ["contract_revision_id", "signed_content_digest"],
        schema=SCHEMA,
    )
    _create_signature_guards()
    _patch_revision_guard(enable=True)
    _create_activation_guard()
    _create_deferred_signature_consistency()


def _create_signature_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_signature_append_only_v6()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'contract signature is append-only';
            END IF;
            SELECT status INTO parent_status
              FROM medtrust.contract_revisions
             WHERE id = NEW.contract_revision_id;
            IF parent_status IS DISTINCT FROM 'proposed' THEN
                RAISE EXCEPTION 'contract signatures can only be appended to proposed revisions';
            END IF;
            IF NEW.signature_type <> 'demo' OR NEW.verification_status <> 'verified' OR
               NEW.verified_at IS NULL OR jsonb_typeof(NEW.authority_snapshot) <> 'object' THEN
                RAISE EXCEPTION 'invalid demo signature shape';
            END IF;
            IF NEW.authority_snapshot->>'schema_version' <> '1.0' OR
               NEW.authority_snapshot->>'is_demo' <> 'true' OR
               NEW.authority_snapshot->>'organization_id' <> NEW.signer_organization_id::text OR
               NEW.authority_snapshot->>'user_id' <> NEW.signer_user_id::text OR
               NEW.authority_snapshot->>'membership_status' <> 'active' OR
               NEW.authority_snapshot->>'authority_code' <> 'demo_contract_signer' OR
               NEW.authority_snapshot#>>'{scope,contract_revision_id}' <> NEW.contract_revision_id::text OR
               NEW.authority_snapshot#>>'{scope,contract_party_id}' <> NEW.contract_party_id::text THEN
                RAISE EXCEPTION 'authority snapshot does not match signature scope';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                  FROM medtrust.organization_members om
                  JOIN medtrust.organization_member_roles r
                    ON r.organization_member_id = om.id
                  JOIN medtrust.organizations o ON o.id = om.organization_id
                  JOIN medtrust.users u ON u.id = om.user_id
                 WHERE om.organization_id = NEW.signer_organization_id
                   AND om.user_id = NEW.signer_user_id
                   AND om.id::text = NEW.authority_snapshot->>'organization_member_id'
                   AND om.status = 'active'
                   AND (om.valid_from IS NULL OR om.valid_from <= NEW.signed_at)
                   AND (om.valid_until IS NULL OR om.valid_until > NEW.signed_at)
                   AND r.role_code = 'contract_signer'
                   AND o.status = 'active'
                   AND u.status = 'active'
            ) THEN
                RAISE EXCEPTION 'signer authority is not active';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_contract_signature_append_only "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.contract_signatures "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_signature_append_only_v6()"
    )


def _patch_revision_guard(*, enable: bool) -> None:
    old_block = """
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'proposed or terminal revision is immutable in D1';
            END IF;
            IF NEW.status = 'draft' THEN RETURN NEW; END IF;
"""
    lifecycle_block = """
            IF OLD.status = 'proposed' THEN
                IF NEW.status <> 'signed' THEN
                    RAISE EXCEPTION 'proposed revision only supports signed transition in 0010';
                END IF;
                IF NEW.signed_at IS NULL OR NEW.row_version <> OLD.row_version + 1 THEN
                    RAISE EXCEPTION 'signed transition requires timestamp and row version increment';
                END IF;
                IF ROW(NEW.contract_id, NEW.revision_no, NEW.supersedes_revision_id,
                       NEW.name, NEW.summary, NEW.terms_schema_version,
                       NEW.terms_document, NEW.terms_digest, NEW.signing_mode,
                       NEW.effective_from, NEW.effective_until,
                       NEW.handoff_guard_evidence, NEW.handoff_guard_digest,
                       NEW.content_digest, NEW.proposed_at, NEW.created_at, NEW.created_by)
                   IS DISTINCT FROM
                   ROW(OLD.contract_id, OLD.revision_no, OLD.supersedes_revision_id,
                       OLD.name, OLD.summary, OLD.terms_schema_version,
                       OLD.terms_document, OLD.terms_digest, OLD.signing_mode,
                       OLD.effective_from, OLD.effective_until,
                       OLD.handoff_guard_evidence, OLD.handoff_guard_digest,
                       OLD.content_digest, OLD.proposed_at, OLD.created_at, OLD.created_by) THEN
                    RAISE EXCEPTION 'signing cannot change revision content';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status = 'signed' THEN
                IF NEW.status <> 'active' THEN
                    RAISE EXCEPTION 'signed revision only supports active transition in 0010';
                END IF;
                IF NEW.activated_at IS NULL OR NEW.row_version <> OLD.row_version + 1 THEN
                    RAISE EXCEPTION 'active transition requires timestamp and row version increment';
                END IF;
                IF ROW(NEW.contract_id, NEW.revision_no, NEW.supersedes_revision_id,
                       NEW.name, NEW.summary, NEW.terms_schema_version,
                       NEW.terms_document, NEW.terms_digest, NEW.signing_mode,
                       NEW.effective_from, NEW.effective_until,
                       NEW.handoff_guard_evidence, NEW.handoff_guard_digest,
                       NEW.content_digest, NEW.proposed_at, NEW.signed_at,
                       NEW.created_at, NEW.created_by)
                   IS DISTINCT FROM
                   ROW(OLD.contract_id, OLD.revision_no, OLD.supersedes_revision_id,
                       OLD.name, OLD.summary, OLD.terms_schema_version,
                       OLD.terms_document, OLD.terms_digest, OLD.signing_mode,
                       OLD.effective_from, OLD.effective_until,
                       OLD.handoff_guard_evidence, OLD.handoff_guard_digest,
                       OLD.content_digest, OLD.proposed_at, OLD.signed_at,
                       OLD.created_at, OLD.created_by) THEN
                    RAISE EXCEPTION 'activation cannot change revision content';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'proposed or terminal revision is immutable in D2';
            END IF;
            IF NEW.status = 'draft' THEN RETURN NEW; END IF;
"""
    source = old_block if enable else lifecycle_block
    target = lifecycle_block if enable else old_block
    escaped_source = source.replace("$", "$$")
    escaped_target = target.replace("$", "$$")
    op.execute(
        f"""
        DO $migration$
        DECLARE
            definition text;
            source_block text := $source${escaped_source}$source$;
            target_block text := $target${escaped_target}$target$;
        BEGIN
            SELECT pg_get_functiondef(p.oid) INTO definition
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'medtrust'
               AND p.proname = 'guard_contract_revision_core';
            IF position(source_block IN definition) = 0 THEN
                RAISE EXCEPTION 'unexpected guard_contract_revision_core definition';
            END IF;
            definition := replace(definition, source_block, target_block);
            EXECUTE definition;
        END;
        $migration$;
        """
    )


def _create_activation_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_revision_activation_v6()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT (OLD.status = 'signed' AND NEW.status = 'active') THEN
                RETURN NEW;
            END IF;
            IF (NEW.effective_from IS NOT NULL AND NEW.effective_from > CURRENT_TIMESTAMP) OR
               (NEW.effective_until IS NOT NULL AND NEW.effective_until <= CURRENT_TIMESTAMP) THEN
                RAISE EXCEPTION 'revision effective window is unavailable';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM medtrust.policies p WHERE p.contract_revision_id=NEW.id) OR
               EXISTS (SELECT 1 FROM medtrust.policies p WHERE p.contract_revision_id=NEW.id AND p.policy_digest IS NULL) THEN
                RAISE EXCEPTION 'active revision requires complete policies';
            END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.policy_execution_bindings b
                JOIN medtrust.policies p ON p.id=b.policy_id
                WHERE p.contract_revision_id=NEW.id AND b.is_required
                  AND (b.deployment_status <> 'accepted' OR b.receipt_digest IS NULL)
            ) THEN RAISE EXCEPTION 'required policy binding is not accepted'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.policy_execution_bindings b
                JOIN medtrust.policies p ON p.id=b.policy_id
                JOIN medtrust.connectors cn ON cn.id=b.connector_id
                JOIN medtrust.connector_capabilities cap
                  ON cap.connector_id=b.connector_id
                 AND cap.capability_code=b.required_capability_code
                 AND cap.capability_version=b.required_capability_version
                JOIN medtrust.contract_revisions r ON r.id=p.contract_revision_id
                JOIN medtrust.contracts c ON c.id=r.contract_id
                WHERE p.contract_revision_id=NEW.id AND b.is_required
                  AND (cn.space_id <> c.space_id OR cn.verification_status <> 'verified' OR
                       cn.runtime_status <> 'online' OR cn.last_heartbeat_at IS NULL OR
                       cap.status <> 'verified' OR cap.verified_at IS NULL)
            ) THEN RAISE EXCEPTION 'required connector capability is not executable'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.contract_parties cp
                JOIN medtrust.contract_revisions r ON r.id=cp.contract_revision_id
                JOIN medtrust.contracts c ON c.id=r.contract_id
                LEFT JOIN medtrust.organizations o ON o.id=cp.organization_id
                LEFT JOIN medtrust.space_participants sp
                  ON sp.space_id=c.space_id AND sp.organization_id=cp.organization_id
                WHERE cp.contract_revision_id=NEW.id
                  AND (o.status IS DISTINCT FROM 'active' OR
                       sp.admission_status IS DISTINCT FROM 'admitted')
            ) THEN RAISE EXCEPTION 'contract party is not currently admitted'; END IF;
            IF EXISTS (
                SELECT 1 FROM medtrust.contract_objects co
                JOIN medtrust.data_product_versions v ON v.id=co.data_product_version_id
                JOIN medtrust.data_products p ON p.id=v.data_product_id
                WHERE co.contract_revision_id=NEW.id
                  AND (v.status <> 'approved' OR p.lifecycle_status <> 'active' OR
                       v.snapshot_digest <> co.product_snapshot_digest)
            ) THEN RAISE EXCEPTION 'contracted product version is unavailable'; END IF;
            IF NOT EXISTS (
                SELECT 1 FROM medtrust.contracts c
                JOIN medtrust.applications a ON a.id=c.application_id
                JOIN medtrust.spaces s ON s.id=c.space_id
                WHERE c.id=NEW.contract_id AND a.status='approved' AND s.status='active'
            ) THEN RAISE EXCEPTION 'application or space is not currently eligible'; END IF;
            IF NOT EXISTS (
                SELECT 1 FROM medtrust.contracts c
                JOIN medtrust.review_tasks rt
                  ON rt.application_snapshot_id=c.application_snapshot_id
                JOIN medtrust.review_decisions rd ON rd.review_task_id=rt.id
                WHERE c.id=NEW.contract_id AND rt.is_required
                  AND rt.review_type='application_precheck'
                  AND rt.task_status='decided' AND rd.decision='approved'
            ) OR NOT EXISTS (
                SELECT 1 FROM medtrust.contracts c
                JOIN medtrust.review_tasks rt
                  ON rt.application_snapshot_id=c.application_snapshot_id
                JOIN medtrust.review_decisions rd ON rd.review_task_id=rt.id
                WHERE c.id=NEW.contract_id AND rt.is_required
                  AND rt.review_type='provider_review'
                  AND rt.task_status='decided' AND rd.decision='approved'
            ) OR EXISTS (
                SELECT 1 FROM medtrust.contracts c
                JOIN medtrust.review_tasks rt
                  ON rt.application_snapshot_id=c.application_snapshot_id
                LEFT JOIN medtrust.review_decisions rd ON rd.review_task_id=rt.id
                WHERE c.id=NEW.contract_id AND rt.is_required
                  AND (rt.task_status <> 'decided' OR rd.decision IS DISTINCT FROM 'approved')
            ) THEN RAISE EXCEPTION 'review eligibility is not currently approved'; END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_contract_revision_activation "
        "BEFORE UPDATE ON medtrust.contract_revisions "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_revision_activation_v6()"
    )


def _create_deferred_signature_consistency() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_revision_signed_consistency_v6()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            revision_id uuid;
            revision_status text;
            revision_digest text;
            required_count integer;
            signed_count integer;
        BEGIN
            revision_id := CASE WHEN TG_TABLE_NAME='contract_signatures'
                                THEN NEW.contract_revision_id ELSE NEW.id END;
            SELECT status, content_digest INTO revision_status, revision_digest
              FROM medtrust.contract_revisions WHERE id=revision_id;
            SELECT count(*) INTO required_count FROM medtrust.contract_parties
             WHERE contract_revision_id=revision_id AND is_required;
            SELECT count(*) INTO signed_count FROM medtrust.contract_signatures
             WHERE contract_revision_id=revision_id
               AND signed_content_digest=revision_digest
               AND verification_status='verified'
               AND contract_party_id IN (
                   SELECT id FROM medtrust.contract_parties
                    WHERE contract_revision_id=revision_id AND is_required
               );
            IF revision_status='proposed' AND required_count > 0 AND signed_count=required_count THEN
                RAISE EXCEPTION 'last required signature must transition revision to signed';
            END IF;
            IF revision_status IN ('signed','active','suspended','expired','terminated') AND
               (required_count=0 OR signed_count<>required_count) THEN
                RAISE EXCEPTION 'signed revision requires every required party signature';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_contract_signature_consistency "
        "AFTER INSERT ON medtrust.contract_signatures DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_revision_signed_consistency_v6()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_contract_revision_signed_consistency "
        "AFTER UPDATE OF status ON medtrust.contract_revisions DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_revision_signed_consistency_v6()"
    )


def downgrade() -> None:
    _patch_revision_guard(enable=False)
    op.execute("DROP TRIGGER IF EXISTS trg_contract_revision_signed_consistency ON medtrust.contract_revisions")
    op.execute("DROP TRIGGER IF EXISTS trg_contract_signature_consistency ON medtrust.contract_signatures")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_contract_revision_signed_consistency_v6()")
    op.execute("DROP TRIGGER IF EXISTS trg_contract_revision_activation ON medtrust.contract_revisions")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_contract_revision_activation_v6()")
    op.execute("DROP TRIGGER IF EXISTS trg_contract_signature_append_only ON medtrust.contract_signatures")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_contract_signature_append_only_v6()")
    op.drop_table("contract_signatures", schema=SCHEMA)
