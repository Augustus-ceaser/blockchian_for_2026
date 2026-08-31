"""Fix deferred Contract active-commit consistency triggers.

Revision ID: 20260722_0012
Revises: 20260722_0011
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260722_0012"
down_revision: str | None = "20260722_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _drop_v6_triggers_and_function()
    _create_v7_functions_and_triggers()


def downgrade() -> None:
    _drop_v7_triggers_and_functions()
    _create_v6_function_and_triggers()


def _drop_v6_triggers_and_function() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_revision_signed_consistency "
        "ON medtrust.contract_revisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_signature_consistency "
        "ON medtrust.contract_signatures"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "medtrust.guard_contract_revision_signed_consistency_v6()"
    )


def _create_v7_functions_and_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.assert_contract_revision_signed_consistency_v7(
            p_revision_id uuid
        )
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            revision_status text;
            revision_digest text;
            required_count integer;
            signed_count integer;
        BEGIN
            SELECT status, content_digest
              INTO revision_status, revision_digest
              FROM medtrust.contract_revisions
             WHERE id = p_revision_id;
            SELECT count(*) INTO required_count
              FROM medtrust.contract_parties
             WHERE contract_revision_id = p_revision_id AND is_required;
            SELECT count(*) INTO signed_count
              FROM medtrust.contract_signatures
             WHERE contract_revision_id = p_revision_id
               AND signed_content_digest = revision_digest
               AND verification_status = 'verified'
               AND contract_party_id IN (
                   SELECT id
                     FROM medtrust.contract_parties
                    WHERE contract_revision_id = p_revision_id AND is_required
               );
            IF revision_status = 'proposed' AND required_count > 0
               AND signed_count = required_count THEN
                RAISE EXCEPTION
                    'last required signature must transition revision to signed';
            END IF;
            IF revision_status IN ('signed', 'active', 'suspended', 'expired', 'terminated')
               AND (required_count = 0 OR signed_count <> required_count) THEN
                RAISE EXCEPTION
                    'signed revision requires every required party signature';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_signature_consistency_v7()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM medtrust.assert_contract_revision_signed_consistency_v7(
                NEW.contract_revision_id
            );
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_revision_signed_consistency_v7()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM medtrust.assert_contract_revision_signed_consistency_v7(NEW.id);
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_contract_signature_consistency "
        "AFTER INSERT ON medtrust.contract_signatures DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION "
        "medtrust.guard_contract_signature_consistency_v7()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_contract_revision_signed_consistency "
        "AFTER UPDATE OF status ON medtrust.contract_revisions "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "medtrust.guard_contract_revision_signed_consistency_v7()"
    )


def _drop_v7_triggers_and_functions() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_revision_signed_consistency "
        "ON medtrust.contract_revisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_signature_consistency "
        "ON medtrust.contract_signatures"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "medtrust.guard_contract_revision_signed_consistency_v7()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.guard_contract_signature_consistency_v7()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "medtrust.assert_contract_revision_signed_consistency_v7(uuid)"
    )


def _create_v6_function_and_triggers() -> None:
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
            IF revision_status='proposed' AND required_count > 0
               AND signed_count=required_count THEN
                RAISE EXCEPTION
                    'last required signature must transition revision to signed';
            END IF;
            IF revision_status IN ('signed','active','suspended','expired','terminated')
               AND (required_count=0 OR signed_count<>required_count) THEN
                RAISE EXCEPTION
                    'signed revision requires every required party signature';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_contract_signature_consistency "
        "AFTER INSERT ON medtrust.contract_signatures DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION "
        "medtrust.guard_contract_revision_signed_consistency_v6()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_contract_revision_signed_consistency "
        "AFTER UPDATE OF status ON medtrust.contract_revisions "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "medtrust.guard_contract_revision_signed_consistency_v6()"
    )
