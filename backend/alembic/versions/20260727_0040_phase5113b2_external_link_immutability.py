"""Guard immutable external-source linkage rows for Phase 5.11.3B2.

Revision ID: 20260727_0040
Revises: 20260727_0039
"""

from alembic import op


revision = "20260727_0040"
down_revision = "20260727_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_external_source_link_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'external source linkage is immutable' USING ERRCODE='55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_external_source_link_immutable
        BEFORE UPDATE OR DELETE ON medtrust.data_product_external_source_links
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_external_source_link_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_external_source_link_immutable
        ON medtrust.data_product_external_source_links;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS medtrust.guard_external_source_link_immutable();
        """
    )
