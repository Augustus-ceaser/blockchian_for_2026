"""Align Contract database guards with Phase 4 multi-party role semantics.

Revision ID: 20260723_0024
Revises: 20260723_0023
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision: str = "20260723_0024"
down_revision: str | None = "20260723_0023"
branch_labels: str | None = None
depends_on: str | None = None


def _replace_fragment(function_name: str, source: str, target: str) -> None:
    escaped_source = source.replace("$", "$$")
    escaped_target = target.replace("$", "$$")
    op.execute(
        f"""
        DO $migration$
        DECLARE
            definition text;
            source_fragment text := $source${escaped_source}$source$;
            target_fragment text := $target${escaped_target}$target$;
        BEGIN
            SELECT pg_get_functiondef(p.oid) INTO definition
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='medtrust' AND p.proname='{function_name}'
               AND pg_get_function_identity_arguments(p.oid)='';
            IF definition IS NULL OR position(source_fragment IN definition)=0 THEN
                RAISE EXCEPTION 'unexpected {function_name} definition';
            END IF;
            definition := replace(definition, source_fragment, target_fragment);
            EXECUTE definition;
        END;
        $migration$;
        """
    )


def upgrade() -> None:
    _replace_fragment(
        "guard_contract_party_core",
        "NEW.party_role = 'provider'",
        "NEW.party_role IN ('provider','data_provider')",
    )
    _replace_fragment(
        "guard_contract_party_core",
        "NEW.party_role = 'consumer'",
        "NEW.party_role IN ('consumer','data_requester')",
    )
    _replace_fragment(
        "guard_contract_revision_core",
        "party_role = 'provider'",
        "party_role IN ('provider','data_provider')",
    )
    _replace_fragment(
        "guard_contract_revision_core",
        "party_role = 'consumer'",
        "party_role IN ('consumer','data_requester')",
    )
    _replace_fragment(
        "guard_contract_revision_core",
        "cp.party_role IN ('provider','service_provider','operator_witness')",
        "cp.party_role IN ('provider','data_provider','model_provider','service_provider','operator_witness')",
    )
    _replace_fragment(
        "guard_contract_revision_activation_v6",
        "rt.review_type='provider_review'",
        "rt.review_type=(CASE WHEN EXISTS (SELECT 1 FROM medtrust.application_model_selections ms WHERE ms.application_id=c.application_id) THEN 'data_provider_review' ELSE 'provider_review' END)",
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_phase4_contract_activation_v1()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT (OLD.status='signed' AND NEW.status='active') THEN
                RETURN NEW;
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM medtrust.contracts c
                  JOIN medtrust.application_model_selections ms
                    ON ms.application_id=c.application_id
                 WHERE c.id=NEW.contract_id
            ) THEN
                IF NOT EXISTS (
                    SELECT 1
                      FROM medtrust.contracts c
                      JOIN medtrust.review_tasks rt
                        ON rt.application_snapshot_id=c.application_snapshot_id
                      JOIN medtrust.review_decisions rd ON rd.review_task_id=rt.id
                     WHERE c.id=NEW.contract_id AND rt.is_required
                       AND rt.review_type='data_provider_review'
                       AND rt.task_status='decided' AND rd.decision='approved'
                ) OR NOT EXISTS (
                    SELECT 1
                      FROM medtrust.contracts c
                      JOIN medtrust.review_tasks rt
                        ON rt.application_snapshot_id=c.application_snapshot_id
                      JOIN medtrust.review_decisions rd ON rd.review_task_id=rt.id
                     WHERE c.id=NEW.contract_id AND rt.is_required
                       AND rt.review_type='model_provider_review'
                       AND rt.task_status='decided' AND rd.decision='approved'
                ) THEN
                    RAISE EXCEPTION 'Phase 4 activation requires approved data and model provider reviews';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                      FROM medtrust.contracts c
                      JOIN medtrust.application_model_selections ms
                        ON ms.application_id=c.application_id
                      JOIN medtrust.contract_model_objects mo
                        ON mo.contract_revision_id=NEW.id
                       AND mo.model_version_id=ms.model_version_id
                       AND mo.model_snapshot_digest=ms.model_snapshot_digest
                     WHERE c.id=NEW.contract_id
                ) THEN
                    RAISE EXCEPTION 'Phase 4 activation requires the reviewed fixed model object';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_phase4_contract_activation "
        "BEFORE UPDATE OF status ON medtrust.contract_revisions "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_phase4_contract_activation_v1()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_phase4_contract_activation ON medtrust.contract_revisions"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_phase4_contract_activation_v1()")
    _replace_fragment(
        "guard_contract_revision_activation_v6",
        "rt.review_type=(CASE WHEN EXISTS (SELECT 1 FROM medtrust.application_model_selections ms WHERE ms.application_id=c.application_id) THEN 'data_provider_review' ELSE 'provider_review' END)",
        "rt.review_type='provider_review'",
    )
    _replace_fragment(
        "guard_contract_revision_core",
        "cp.party_role IN ('provider','data_provider','model_provider','service_provider','operator_witness')",
        "cp.party_role IN ('provider','service_provider','operator_witness')",
    )
    _replace_fragment(
        "guard_contract_revision_core",
        "party_role IN ('consumer','data_requester')",
        "party_role = 'consumer'",
    )
    _replace_fragment(
        "guard_contract_revision_core",
        "party_role IN ('provider','data_provider')",
        "party_role = 'provider'",
    )
    _replace_fragment(
        "guard_contract_party_core",
        "NEW.party_role IN ('consumer','data_requester')",
        "NEW.party_role = 'consumer'",
    )
    _replace_fragment(
        "guard_contract_party_core",
        "NEW.party_role IN ('provider','data_provider')",
        "NEW.party_role = 'provider'",
    )
