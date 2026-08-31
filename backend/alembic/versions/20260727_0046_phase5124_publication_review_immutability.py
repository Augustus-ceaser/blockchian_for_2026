"""Freeze decided external model publication reviews.

Revision ID: 20260727_0046
Revises: 20260727_0045
"""

from alembic import op


revision = "20260727_0046"
down_revision = "20260727_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_model_metadata_publication_review_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'model metadata publication reviews are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.task_status <> 'pending' OR NEW.task_status <> 'decided' THEN
                RAISE EXCEPTION 'only pending to decided review transition is allowed'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.space_id IS DISTINCT FROM OLD.space_id
                OR NEW.model_product_id IS DISTINCT FROM OLD.model_product_id
                OR NEW.model_version_id IS DISTINCT FROM OLD.model_version_id
                OR NEW.external_source_link_id IS DISTINCT FROM OLD.external_source_link_id
                OR NEW.sequence_no IS DISTINCT FROM OLD.sequence_no
                OR NEW.submission_digest IS DISTINCT FROM OLD.submission_digest
                OR NEW.submitter_organization_id IS DISTINCT FROM OLD.submitter_organization_id
                OR NEW.submitter_user_id IS DISTINCT FROM OLD.submitter_user_id
                OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'model metadata publication review identity is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_model_metadata_publication_review_immutable
        BEFORE UPDATE OR DELETE
        ON medtrust.model_metadata_publication_review_tasks
        FOR EACH ROW
        EXECUTE FUNCTION medtrust.guard_model_metadata_publication_review_v1();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_model_metadata_publication_review_immutable
        ON medtrust.model_metadata_publication_review_tasks;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS medtrust.guard_model_metadata_publication_review_v1();
        """
    )
