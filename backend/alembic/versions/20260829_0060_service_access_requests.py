"""Add reviewed data/model authorization requests without fulfillment grants.

Revision ID: 20260829_0060
Revises: 20260828_0059
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260829_0060"
down_revision = "20260828_0059"
branch_labels = None
depends_on = None

SCHEMA = "medtrust"
EVENT_RESULTS = {
    "service_access.request.created": "success",
    "service_access.provider.approved": "success",
    "service_access.provider.rejected": "denied",
    "service_access.operator.approved": "success",
    "service_access.operator.rejected": "denied",
}


def _constraint_values(name: str) -> list[str]:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname='medtrust' AND t.relname='audit_events' "
            "AND c.conname=:name"
        ),
        {"name": name},
    ).scalar_one()
    return list(dict.fromkeys(re.findall(r"'([^']+)'", definition)))


def _replace_values(name: str, column: str, values: list[str]) -> None:
    rendered = ",".join(repr(value) for value in values)
    op.execute(f"ALTER TABLE medtrust.audit_events DROP CONSTRAINT {name}")
    op.execute(
        f"ALTER TABLE medtrust.audit_events ADD CONSTRAINT {name} "
        f"CHECK ({column} IN ({rendered}))"
    )


def _function_definition(name: str) -> str:
    return op.get_bind().execute(
        sa.text(
            f"SELECT pg_get_functiondef('medtrust.{name}()'::regprocedure)"
        )
    ).scalar_one()


def _audit_cases() -> str:
    return "".join(
        f"""
                WHEN '{event_type}' THEN
                    IF NEW.subject_type<>'service_access_request'
                      OR NEW.result<>'{result}' THEN
                      RAISE EXCEPTION 'invalid service access event shape'
                        USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(
                      SELECT 1 FROM medtrust.service_access_requests r
                      WHERE r.id=NEW.subject_id AND r.space_id=NEW.space_id
                    ) INTO v_subject_ok;
"""
        for event_type, result in EVENT_RESULTS.items()
    )


def _change_audit(enable: bool) -> None:
    event_name = "ck_audit_events_ck_audit_events_event_type"
    subject_name = "ck_audit_events_ck_audit_events_subject_type"
    events = _constraint_values(event_name)
    subjects = _constraint_values(subject_name)
    if enable:
        events.extend(value for value in EVENT_RESULTS if value not in events)
        if "service_access_request" not in subjects:
            subjects.append("service_access_request")
    else:
        events = [value for value in events if value not in EVENT_RESULTS]
        subjects = [value for value in subjects if value != "service_access_request"]
    _replace_values(event_name, "event_type", events)
    _replace_values(subject_name, "subject_type", subjects)
    guard = _function_definition("guard_audit_event_v8")
    cases = _audit_cases()
    marker = "                WHEN 'contract.revision.proposed' THEN"
    if enable:
        if marker not in guard:
            raise RuntimeError("expected audit guard insertion marker was not found")
        op.execute(guard.replace(marker, cases + marker, 1))
    else:
        if cases not in guard:
            raise RuntimeError("service access audit guard cases were not found")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    op.create_table(
        "service_access_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("request_number", sa.String(32), nullable=False),
        sa.Column("requester_organization_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("product_kind", sa.String(16), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("service_mode", sa.String(48), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("intended_use", sa.Text(), nullable=False),
        sa.Column("requested_duration_days", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="submitted"
        ),
        sa.Column("product_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("product_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("create_idempotency_digest", sa.String(71), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("provider_decision", sa.String(16)),
        sa.Column("provider_decision_summary", sa.Text()),
        sa.Column("provider_decided_by", sa.Uuid()),
        sa.Column("provider_decided_at", sa.DateTime(timezone=True)),
        sa.Column("provider_decision_idempotency_digest", sa.String(71)),
        sa.Column("operator_decision", sa.String(16)),
        sa.Column("operator_decision_summary", sa.Text()),
        sa.Column("operator_decided_by", sa.Uuid()),
        sa.Column("operator_decided_at", sa.DateTime(timezone=True)),
        sa.Column("operator_decision_idempotency_digest", sa.String(71)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "product_kind IN ('data','model')",
            name="ck_service_access_requests_product_kind",
        ),
        sa.CheckConstraint(
            "(product_kind='data' AND service_mode='deidentified_data_delivery') OR "
            "(product_kind='model' AND service_mode='model_artifact_license')",
            name="ck_service_access_requests_kind_mode_pair",
        ),
        sa.CheckConstraint(
            "status IN ('submitted','provider_approved',"
            "'approved_pending_contract','rejected')",
            name="ck_service_access_requests_status",
        ),
        sa.CheckConstraint(
            "requested_duration_days BETWEEN 1 AND 3650",
            name="ck_service_access_requests_duration_range",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_service_access_requests_row_version_positive",
        ),
        sa.CheckConstraint(
            "length(product_snapshot_digest)=71 AND "
            "substr(product_snapshot_digest,1,7)='sha256:' AND "
            "length(request_digest)=71 AND substr(request_digest,1,7)='sha256:' AND "
            "length(create_idempotency_digest)=71 AND "
            "substr(create_idempotency_digest,1,7)='sha256:' AND "
            "(provider_decision_idempotency_digest IS NULL OR "
            "(length(provider_decision_idempotency_digest)=71 AND "
            "substr(provider_decision_idempotency_digest,1,7)='sha256:')) AND "
            "(operator_decision_idempotency_digest IS NULL OR "
            "(length(operator_decision_idempotency_digest)=71 AND "
            "substr(operator_decision_idempotency_digest,1,7)='sha256:'))",
            name="ck_service_access_requests_digest_formats",
        ),
        sa.CheckConstraint(
            "provider_decision IS NULL OR provider_decision IN ('approve','reject')",
            name="ck_service_access_requests_provider_decision",
        ),
        sa.CheckConstraint(
            "operator_decision IS NULL OR operator_decision IN ('approve','reject')",
            name="ck_service_access_requests_operator_decision",
        ),
        sa.CheckConstraint(
            "(status='submitted' AND provider_decision IS NULL AND "
            "provider_decision_summary IS NULL AND provider_decided_by IS NULL AND "
            "provider_decided_at IS NULL AND provider_decision_idempotency_digest IS NULL AND "
            "operator_decision IS NULL AND operator_decision_summary IS NULL AND "
            "operator_decided_by IS NULL AND operator_decided_at IS NULL AND "
            "operator_decision_idempotency_digest IS NULL) OR "
            "(status='provider_approved' AND provider_decision='approve' AND "
            "provider_decision_summary IS NOT NULL AND provider_decided_by IS NOT NULL AND "
            "provider_decided_at IS NOT NULL AND "
            "provider_decision_idempotency_digest IS NOT NULL AND "
            "operator_decision IS NULL AND operator_decision_summary IS NULL AND "
            "operator_decided_by IS NULL AND operator_decided_at IS NULL AND "
            "operator_decision_idempotency_digest IS NULL) OR "
            "(status='approved_pending_contract' AND provider_decision='approve' AND "
            "provider_decision_summary IS NOT NULL AND provider_decided_by IS NOT NULL AND "
            "provider_decided_at IS NOT NULL AND "
            "provider_decision_idempotency_digest IS NOT NULL AND "
            "operator_decision='approve' AND operator_decision_summary IS NOT NULL AND "
            "operator_decided_by IS NOT NULL AND operator_decided_at IS NOT NULL AND "
            "operator_decision_idempotency_digest IS NOT NULL) OR "
            "(status='rejected' AND ((provider_decision='reject' AND "
            "provider_decision_summary IS NOT NULL AND provider_decided_by IS NOT NULL AND "
            "provider_decided_at IS NOT NULL AND "
            "provider_decision_idempotency_digest IS NOT NULL AND "
            "operator_decision IS NULL AND operator_decision_summary IS NULL AND "
            "operator_decided_by IS NULL AND operator_decided_at IS NULL AND "
            "operator_decision_idempotency_digest IS NULL) OR "
            "(provider_decision='approve' AND provider_decision_summary IS NOT NULL AND "
            "provider_decided_by IS NOT NULL AND provider_decided_at IS NOT NULL AND "
            "provider_decision_idempotency_digest IS NOT NULL AND operator_decision='reject' AND "
            "operator_decision_summary IS NOT NULL AND operator_decided_by IS NOT NULL AND "
            "operator_decided_at IS NOT NULL AND "
            "operator_decision_idempotency_digest IS NOT NULL)))",
            name="ck_service_access_requests_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requester_organization_id"],
            ["medtrust.organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["provider_organization_id"],
            ["medtrust.organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_decided_by"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["operator_decided_by"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "space_id",
            "request_number",
            name="uq_service_access_space_number",
        ),
        sa.UniqueConstraint(
            "create_idempotency_digest",
            name="uq_service_access_create_idempotency",
        ),
        sa.UniqueConstraint(
            "provider_decision_idempotency_digest",
            name="uq_service_access_provider_decision_idempotency",
        ),
        sa.UniqueConstraint(
            "operator_decision_idempotency_digest",
            name="uq_service_access_operator_decision_idempotency",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_service_access_requester_status",
        "service_access_requests",
        ["space_id", "requester_organization_id", "status", sa.text("requested_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_service_access_provider_status",
        "service_access_requests",
        [
            "space_id",
            "provider_organization_id",
            "product_kind",
            "status",
            sa.text("requested_at DESC"),
        ],
        schema=SCHEMA,
    )
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_service_access_request_v1()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'service access request history cannot be deleted'
              USING ERRCODE='55000';
          END IF;
          IF ROW(NEW.id,NEW.space_id,NEW.request_number,
                 NEW.requester_organization_id,NEW.requester_user_id,
                 NEW.provider_organization_id,NEW.product_kind,NEW.product_id,
                 NEW.version_id,NEW.service_mode,NEW.purpose,NEW.intended_use,
                 NEW.requested_duration_days,NEW.product_snapshot,
                 NEW.product_snapshot_digest,NEW.request_digest,
                 NEW.create_idempotency_digest,NEW.requested_at)
             IS DISTINCT FROM
             ROW(OLD.id,OLD.space_id,OLD.request_number,
                 OLD.requester_organization_id,OLD.requester_user_id,
                 OLD.provider_organization_id,OLD.product_kind,OLD.product_id,
                 OLD.version_id,OLD.service_mode,OLD.purpose,OLD.intended_use,
                 OLD.requested_duration_days,OLD.product_snapshot,
                 OLD.product_snapshot_digest,OLD.request_digest,
                 OLD.create_idempotency_digest,OLD.requested_at) THEN
            RAISE EXCEPTION 'service access request identity and snapshot are immutable'
              USING ERRCODE='55000';
          END IF;
          IF NOT ((OLD.status='submitted' AND NEW.status IN ('provider_approved','rejected'))
              OR (OLD.status='provider_approved' AND
                  NEW.status IN ('approved_pending_contract','rejected'))) THEN
            RAISE EXCEPTION 'illegal service access request transition: % -> %',
              OLD.status,NEW.status USING ERRCODE='23514';
          END IF;
          IF NEW.row_version<>OLD.row_version+1 THEN
            RAISE EXCEPTION 'service access row_version must advance exactly once'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_service_access_request_guard
        BEFORE UPDATE OR DELETE ON medtrust.service_access_requests
        FOR EACH ROW EXECUTE FUNCTION medtrust.guard_service_access_request_v1()
        """
    )
    _change_audit(True)


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM medtrust.audit_events "
            "WHERE event_type = ANY(:event_types)"
        ),
        {"event_types": list(EVENT_RESULTS)},
    ).scalar_one()
    if count:
        raise RuntimeError(
            "cannot remove service access audit vocabulary while events exist"
        )
    _change_audit(False)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_service_access_request_guard "
        "ON medtrust.service_access_requests"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.guard_service_access_request_v1()"
    )
    op.drop_index(
        "ix_service_access_provider_status",
        table_name="service_access_requests",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_service_access_requester_status",
        table_name="service_access_requests",
        schema=SCHEMA,
    )
    op.drop_table("service_access_requests", schema=SCHEMA)
