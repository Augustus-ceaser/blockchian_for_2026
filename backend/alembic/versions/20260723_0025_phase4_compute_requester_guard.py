"""Permit the Phase 4 data_requester party in Compute authorization.

Revision ID: 20260723_0025
Revises: 20260723_0024
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision: str = "20260723_0025"
down_revision: str | None = "20260723_0024"
branch_labels: str | None = None
depends_on: str | None = None


def _replace(source: str, target: str) -> None:
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text;
        BEGIN
            SELECT pg_get_functiondef(p.oid) INTO definition
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='medtrust' AND p.proname='guard_compute_job_v7'
               AND pg_get_function_identity_arguments(p.oid)='';
            IF definition IS NULL OR position($source${source}$source$ IN definition)=0 THEN
                RAISE EXCEPTION 'unexpected guard_compute_job_v7 definition';
            END IF;
            definition := replace(definition, $source${source}$source$, $target${target}$target$);
            EXECUTE definition;
        END;
        $migration$;
        """
    )


def upgrade() -> None:
    _replace(
        "cp.party_role='consumer'",
        "cp.party_role IN ('consumer','data_requester')",
    )


def downgrade() -> None:
    _replace(
        "cp.party_role IN ('consumer','data_requester')",
        "cp.party_role='consumer'",
    )
