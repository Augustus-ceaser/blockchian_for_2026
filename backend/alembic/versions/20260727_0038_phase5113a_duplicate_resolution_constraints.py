"""Constrain Phase 5.11.3A duplicate resolution decisions.

Revision ID: 20260727_0038
Revises: 20260727_0037
"""

from alembic import op

revision = "20260727_0038"
down_revision = "20260727_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_external_dataset_duplicate_resolutions_status",
        "external_dataset_duplicate_resolutions",
        "resolution_status IN ('resolved','unresolved')",
        schema="medtrust",
    )
    op.create_check_constraint(
        "ck_external_dataset_duplicate_resolutions_type",
        "external_dataset_duplicate_resolutions",
        "resolution_type IN ('same_dataset_aliases','same_url_different_entry',"
        "'same_name_different_dataset','version_variants','false_positive','unresolved')",
        schema="medtrust",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_external_dataset_duplicate_resolutions_type",
        "external_dataset_duplicate_resolutions",
        schema="medtrust",
        type_="check",
    )
    op.drop_constraint(
        "ck_external_dataset_duplicate_resolutions_status",
        "external_dataset_duplicate_resolutions",
        schema="medtrust",
        type_="check",
    )
