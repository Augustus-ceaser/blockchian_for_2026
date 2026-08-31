"""Scope executor status sequences to one executor mirror.

Revision ID: 20260729_0054
Revises: 20260729_0053
"""

from alembic import op


revision = "20260729_0054"
down_revision = "20260729_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_executor_status_event_sequence",
        "hospital_executor_status_events",
        schema="medtrust",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_executor_status_event_sequence",
        "hospital_executor_status_events",
        ["mirror_id", "status_sequence"],
        schema="medtrust",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_executor_status_event_sequence",
        "hospital_executor_status_events",
        schema="medtrust",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_executor_status_event_sequence",
        "hospital_executor_status_events",
        ["connector_id", "status_sequence"],
        schema="medtrust",
    )
