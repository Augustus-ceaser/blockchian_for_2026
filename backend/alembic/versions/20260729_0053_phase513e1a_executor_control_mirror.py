"""Phase 5.13E-1A read-only hospital executor control mirrors.

Revision ID: 20260729_0053
Revises: 20260729_0052
"""

from alembic import op

from app.modules.connector_control.models import (
    HospitalExecutorMirror,
    HospitalExecutorStatusEvent,
)

revision = "20260729_0053"
down_revision = "20260729_0052"
branch_labels = None
depends_on = None

TABLES = [
    HospitalExecutorMirror.__table__,
    HospitalExecutorStatusEvent.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    HospitalExecutorMirror.metadata.create_all(
        bind=bind, tables=TABLES, checkfirst=True
    )


def downgrade() -> None:
    bind = op.get_bind()
    HospitalExecutorMirror.metadata.drop_all(
        bind=bind, tables=list(reversed(TABLES)), checkfirst=True
    )
