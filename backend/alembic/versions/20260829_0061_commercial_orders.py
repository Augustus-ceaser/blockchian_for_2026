"""Add auditable local-demo commercial checkout and safe fulfillment.

Revision ID: 20260829_0061
Revises: 20260829_0060
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260829_0061"
down_revision = "20260829_0060"
branch_labels = None
depends_on = None

SCHEMA = "medtrust"
EVENT_RESULTS = {
    "commercial.order.created": ("commercial_order", "success"),
    "commercial.agreement.accepted": ("commercial_order", "success"),
    "commercial.payment.succeeded": ("commercial_payment", "success"),
    "commercial.fulfillment.created": ("commercial_fulfillment", "success"),
    "commercial.download.grant.created": ("commercial_download_grant", "success"),
    "commercial.download.completed": ("commercial_download_grant", "success"),
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
        sa.text(f"SELECT pg_get_functiondef('medtrust.{name}()'::regprocedure)")
    ).scalar_one()


def _audit_cases() -> str:
    return """
                WHEN 'commercial.order.created' THEN
                    IF NEW.subject_type<>'commercial_order' OR NEW.result<>'success' THEN
                      RAISE EXCEPTION 'invalid commercial order event shape' USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.commercial_orders o
                      WHERE o.id=NEW.subject_id AND o.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'commercial.agreement.accepted' THEN
                    IF NEW.subject_type<>'commercial_order' OR NEW.result<>'success' THEN
                      RAISE EXCEPTION 'invalid commercial agreement event shape' USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.commercial_orders o
                      WHERE o.id=NEW.subject_id AND o.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'commercial.payment.succeeded' THEN
                    IF NEW.subject_type<>'commercial_payment' OR NEW.result<>'success' THEN
                      RAISE EXCEPTION 'invalid commercial payment event shape' USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.commercial_demo_payments p
                      JOIN medtrust.commercial_orders o ON o.id=p.order_id
                      WHERE p.id=NEW.subject_id AND o.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'commercial.fulfillment.created' THEN
                    IF NEW.subject_type<>'commercial_fulfillment' OR NEW.result<>'success' THEN
                      RAISE EXCEPTION 'invalid commercial fulfillment event shape' USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.commercial_fulfillments f
                      WHERE f.id=NEW.subject_id AND f.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'commercial.download.grant.created' THEN
                    IF NEW.subject_type<>'commercial_download_grant' OR NEW.result<>'success' THEN
                      RAISE EXCEPTION 'invalid commercial grant event shape' USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.commercial_download_grants g
                      WHERE g.id=NEW.subject_id AND g.space_id=NEW.space_id) INTO v_subject_ok;
                WHEN 'commercial.download.completed' THEN
                    IF NEW.subject_type<>'commercial_download_grant' OR NEW.result<>'success' THEN
                      RAISE EXCEPTION 'invalid commercial download event shape' USING ERRCODE='23514';
                    END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.commercial_download_grants g
                      WHERE g.id=NEW.subject_id AND g.space_id=NEW.space_id) INTO v_subject_ok;
"""


def _change_audit(enable: bool) -> None:
    event_name = "ck_audit_events_ck_audit_events_event_type"
    subject_name = "ck_audit_events_ck_audit_events_subject_type"
    events = _constraint_values(event_name)
    subjects = _constraint_values(subject_name)
    event_names = list(EVENT_RESULTS)
    subject_names = list(dict.fromkeys(shape[0] for shape in EVENT_RESULTS.values()))
    if enable:
        events.extend(value for value in event_names if value not in events)
        subjects.extend(value for value in subject_names if value not in subjects)
    else:
        events = [value for value in events if value not in event_names]
        subjects = [value for value in subjects if value not in subject_names]
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
            raise RuntimeError("commercial audit guard cases were not found")
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    op.create_table(
        "commercial_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(32), nullable=False),
        sa.Column("requester_organization_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("service_access_request_id", sa.Uuid()),
        sa.Column("contract_id", sa.Uuid()),
        sa.Column("status", sa.String(24), nullable=False, server_default="agreement_pending"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("platform_fee_rate_bps", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("provider_net_minor", sa.Integer(), nullable=False),
        sa.Column("quote_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("quote_digest", sa.String(71), nullable=False),
        sa.Column("agreement_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("agreement_digest", sa.String(71), nullable=False),
        sa.Column("agreement_accepted_at", sa.DateTime(timezone=True)),
        sa.Column("agreement_accepted_by", sa.Uuid()),
        sa.Column("create_idempotency_digest", sa.String(71), nullable=False),
        sa.Column("agreement_idempotency_digest", sa.String(71)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("source_type IN ('service_access','contract')", name="ck_commercial_orders_source_type"),
        sa.CheckConstraint(
            "(source_type='service_access' AND service_access_request_id=source_id AND contract_id IS NULL) OR "
            "(source_type='contract' AND contract_id=source_id AND service_access_request_id IS NULL)",
            name="ck_commercial_orders_source_shape",
        ),
        sa.CheckConstraint("status IN ('agreement_pending','awaiting_payment','paid')", name="ck_commercial_orders_status"),
        sa.CheckConstraint("currency='CNY'", name="ck_commercial_orders_currency_cny"),
        sa.CheckConstraint("platform_fee_rate_bps=500", name="ck_commercial_orders_platform_rate_frozen"),
        sa.CheckConstraint(
            "gross_amount_minor>=0 AND platform_fee_minor>=0 AND provider_net_minor>=0 AND "
            "platform_fee_minor+provider_net_minor=gross_amount_minor",
            name="ck_commercial_orders_amounts_balance",
        ),
        sa.CheckConstraint(
            "(status='agreement_pending' AND agreement_accepted_at IS NULL AND agreement_accepted_by IS NULL "
            "AND agreement_idempotency_digest IS NULL) OR "
            "(status IN ('awaiting_payment','paid') AND agreement_accepted_at IS NOT NULL "
            "AND agreement_accepted_by IS NOT NULL AND agreement_idempotency_digest IS NOT NULL)",
            name="ck_commercial_orders_agreement_shape",
        ),
        sa.CheckConstraint("row_version>=1", name="ck_commercial_orders_row_version_positive"),
        sa.CheckConstraint(
            "length(quote_digest)=71 AND substr(quote_digest,1,7)='sha256:' AND "
            "length(agreement_digest)=71 AND substr(agreement_digest,1,7)='sha256:' AND "
            "length(create_idempotency_digest)=71 AND substr(create_idempotency_digest,1,7)='sha256:' AND "
            "(agreement_idempotency_digest IS NULL OR (length(agreement_idempotency_digest)=71 AND "
            "substr(agreement_idempotency_digest,1,7)='sha256:'))",
            name="ck_commercial_orders_digest_formats",
        ),
        sa.ForeignKeyConstraint(["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_organization_id"], ["medtrust.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["medtrust.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_access_request_id"], ["medtrust.service_access_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["contract_id"], ["medtrust.contracts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agreement_accepted_by"], ["medtrust.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "order_number", name="uq_commercial_order_number"),
        sa.UniqueConstraint("source_type", "source_id", name="uq_commercial_order_source"),
        sa.UniqueConstraint("create_idempotency_digest", name="uq_commercial_order_create_idempotency"),
        sa.UniqueConstraint("agreement_idempotency_digest", name="uq_commercial_order_agreement_idempotency"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_commercial_orders_requester_status",
        "commercial_orders",
        ["space_id", "requester_organization_id", "status", sa.text("created_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "commercial_order_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("product_kind", sa.String(16), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("service_mode", sa.String(48), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_amount_minor", sa.Integer(), nullable=False),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("provider_net_minor", sa.Integer(), nullable=False),
        sa.Column("offer_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("offer_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("line_no>0", name="ck_commercial_order_lines_line_no_positive"),
        sa.CheckConstraint("product_kind IN ('data','model')", name="ck_commercial_order_lines_product_kind"),
        sa.CheckConstraint("currency='CNY'", name="ck_commercial_order_lines_currency_cny"),
        sa.CheckConstraint("quantity=1", name="ck_commercial_order_lines_quantity_one"),
        sa.CheckConstraint(
            "unit_amount_minor>=0 AND gross_amount_minor=unit_amount_minor*quantity AND "
            "platform_fee_minor>=0 AND provider_net_minor>=0 AND "
            "platform_fee_minor+provider_net_minor=gross_amount_minor",
            name="ck_commercial_order_lines_amounts_balance",
        ),
        sa.CheckConstraint("length(offer_digest)=71 AND substr(offer_digest,1,7)='sha256:'", name="ck_commercial_order_lines_offer_digest_format"),
        sa.ForeignKeyConstraint(["order_id"], ["medtrust.commercial_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["provider_organization_id"], ["medtrust.organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "line_no", name="uq_commercial_order_line_no"),
        schema=SCHEMA,
    )
    op.create_index("ix_commercial_order_lines_provider", "commercial_order_lines", ["provider_organization_id", "order_id"], schema=SCHEMA)

    op.create_table(
        "commercial_demo_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="succeeded"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("channel_fee_rate_bps", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("channel_fee_minor", sa.Integer(), nullable=False),
        sa.Column("transaction_number", sa.String(40), nullable=False),
        sa.Column("receipt_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("receipt_digest", sa.String(71), nullable=False),
        sa.Column("idempotency_digest", sa.String(71), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("method IN ('wechat_demo','alipay_demo','bank_card_demo')", name="ck_commercial_demo_payments_method"),
        sa.CheckConstraint("status='succeeded'", name="ck_commercial_demo_payments_status_succeeded"),
        sa.CheckConstraint("currency='CNY'", name="ck_commercial_demo_payments_currency_cny"),
        sa.CheckConstraint("amount_minor>=0", name="ck_commercial_demo_payments_amount_nonnegative"),
        sa.CheckConstraint("channel_fee_rate_bps=60", name="ck_commercial_demo_payments_channel_rate_frozen"),
        sa.CheckConstraint("channel_fee_minor>=0 AND channel_fee_minor<=amount_minor", name="ck_commercial_demo_payments_channel_fee_range"),
        sa.CheckConstraint(
            "length(receipt_digest)=71 AND substr(receipt_digest,1,7)='sha256:' AND "
            "length(idempotency_digest)=71 AND substr(idempotency_digest,1,7)='sha256:'",
            name="ck_commercial_demo_payments_digest_formats",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["medtrust.commercial_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_commercial_payment_order"),
        sa.UniqueConstraint("transaction_number", name="uq_commercial_payment_transaction"),
        sa.UniqueConstraint("idempotency_digest", name="uq_commercial_payment_idempotency"),
        schema=SCHEMA,
    )

    op.create_table(
        "commercial_fulfillments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("order_line_id", sa.Uuid()),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("contract_id", sa.Uuid()),
        sa.Column("entitlement_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("entitlement_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('data_document_package','model_license_package','execution_entitlement')", name="ck_commercial_fulfillments_kind"),
        sa.CheckConstraint("status='ready'", name="ck_commercial_fulfillments_status_ready"),
        sa.CheckConstraint(
            "(kind='execution_entitlement' AND contract_id IS NOT NULL AND order_line_id IS NULL) OR "
            "(kind IN ('data_document_package','model_license_package') AND contract_id IS NULL AND order_line_id IS NOT NULL)",
            name="ck_commercial_fulfillments_kind_shape",
        ),
        sa.CheckConstraint("length(entitlement_digest)=71 AND substr(entitlement_digest,1,7)='sha256:'", name="ck_commercial_fulfillments_entitlement_digest_format"),
        sa.ForeignKeyConstraint(["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["medtrust.commercial_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_line_id"], ["medtrust.commercial_order_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["contract_id"], ["medtrust.contracts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "kind", name="uq_commercial_fulfillment_order_kind"),
        schema=SCHEMA,
    )
    op.create_index("ix_commercial_fulfillments_space_order", "commercial_fulfillments", ["space_id", "order_id"], schema=SCHEMA)

    op.create_table(
        "commercial_download_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("fulfillment_id", sa.Uuid(), nullable=False),
        sa.Column("requester_organization_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(71), nullable=False),
        sa.Column("filename", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("max_downloads", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("create_idempotency_digest", sa.String(71), nullable=False),
        sa.Column("consume_idempotency_digest", sa.String(71)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("status IN ('active','consumed','expired')", name="ck_commercial_download_grants_status"),
        sa.CheckConstraint("max_downloads=1", name="ck_commercial_download_grants_single_use"),
        sa.CheckConstraint("download_count BETWEEN 0 AND max_downloads", name="ck_commercial_download_grants_download_count_range"),
        sa.CheckConstraint(
            "(status='active' AND download_count=0 AND consumed_at IS NULL AND consume_idempotency_digest IS NULL) OR "
            "(status='consumed' AND download_count=1 AND consumed_at IS NOT NULL AND consume_idempotency_digest IS NOT NULL) OR "
            "(status='expired' AND download_count=0 AND consumed_at IS NULL)",
            name="ck_commercial_download_grants_lifecycle_shape",
        ),
        sa.CheckConstraint("row_version>=1", name="ck_commercial_download_grants_row_version_positive"),
        sa.CheckConstraint(
            "length(token_digest)=71 AND substr(token_digest,1,7)='sha256:' AND "
            "length(create_idempotency_digest)=71 AND substr(create_idempotency_digest,1,7)='sha256:' AND "
            "(consume_idempotency_digest IS NULL OR (length(consume_idempotency_digest)=71 AND "
            "substr(consume_idempotency_digest,1,7)='sha256:'))",
            name="ck_commercial_download_grants_digest_formats",
        ),
        sa.ForeignKeyConstraint(["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fulfillment_id"], ["medtrust.commercial_fulfillments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_organization_id"], ["medtrust.organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_commercial_download_token"),
        sa.UniqueConstraint("fulfillment_id", name="uq_commercial_download_fulfillment_once"),
        sa.UniqueConstraint("create_idempotency_digest", name="uq_commercial_download_create_idempotency"),
        sa.UniqueConstraint("consume_idempotency_digest", name="uq_commercial_download_consume_idempotency"),
        schema=SCHEMA,
    )
    op.create_index("ix_commercial_download_grants_expiry", "commercial_download_grants", ["status", "expires_at"], schema=SCHEMA)

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_commercial_order_v1()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'commercial order cannot be deleted' USING ERRCODE='55000'; END IF;
          IF ROW(NEW.id,NEW.space_id,NEW.order_number,NEW.requester_organization_id,
                 NEW.requester_user_id,NEW.source_type,NEW.source_id,NEW.service_access_request_id,
                 NEW.contract_id,NEW.currency,NEW.gross_amount_minor,NEW.platform_fee_rate_bps,
                 NEW.platform_fee_minor,NEW.provider_net_minor,NEW.quote_snapshot,NEW.quote_digest,
                 NEW.agreement_snapshot,NEW.agreement_digest,NEW.create_idempotency_digest,NEW.created_at)
             IS DISTINCT FROM
             ROW(OLD.id,OLD.space_id,OLD.order_number,OLD.requester_organization_id,
                 OLD.requester_user_id,OLD.source_type,OLD.source_id,OLD.service_access_request_id,
                 OLD.contract_id,OLD.currency,OLD.gross_amount_minor,OLD.platform_fee_rate_bps,
                 OLD.platform_fee_minor,OLD.provider_net_minor,OLD.quote_snapshot,OLD.quote_digest,
                 OLD.agreement_snapshot,OLD.agreement_digest,OLD.create_idempotency_digest,OLD.created_at) THEN
            RAISE EXCEPTION 'commercial order quote is immutable' USING ERRCODE='55000';
          END IF;
          IF NOT ((OLD.status='agreement_pending' AND NEW.status='awaiting_payment') OR
                  (OLD.status='awaiting_payment' AND NEW.status='paid')) THEN
            RAISE EXCEPTION 'illegal commercial order transition: % -> %',OLD.status,NEW.status USING ERRCODE='23514';
          END IF;
          IF NEW.row_version<>OLD.row_version+1 THEN RAISE EXCEPTION 'commercial order row_version must advance once' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute("CREATE TRIGGER trg_commercial_order_guard BEFORE UPDATE OR DELETE ON medtrust.commercial_orders FOR EACH ROW EXECUTE FUNCTION medtrust.guard_commercial_order_v1()")
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_commercial_append_only_v1()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'commercial evidence is append-only' USING ERRCODE='55000';
        END; $$
        """
    )
    for table in ("commercial_order_lines", "commercial_demo_payments", "commercial_fulfillments"):
        op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON medtrust.{table} FOR EACH ROW EXECUTE FUNCTION medtrust.guard_commercial_append_only_v1()")
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_commercial_download_grant_v1()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'commercial download grant cannot be deleted' USING ERRCODE='55000'; END IF;
          IF ROW(NEW.id,NEW.space_id,NEW.fulfillment_id,NEW.requester_organization_id,
                 NEW.token_digest,NEW.filename,NEW.max_downloads,NEW.expires_at,
                 NEW.create_idempotency_digest,NEW.created_at)
             IS DISTINCT FROM
             ROW(OLD.id,OLD.space_id,OLD.fulfillment_id,OLD.requester_organization_id,
                 OLD.token_digest,OLD.filename,OLD.max_downloads,OLD.expires_at,
                 OLD.create_idempotency_digest,OLD.created_at) THEN
            RAISE EXCEPTION 'commercial download grant identity is immutable' USING ERRCODE='55000';
          END IF;
          IF NOT (OLD.status='active' AND NEW.status IN ('consumed','expired')) THEN
            RAISE EXCEPTION 'illegal commercial download transition: % -> %',OLD.status,NEW.status USING ERRCODE='23514';
          END IF;
          IF NEW.row_version<>OLD.row_version+1 THEN RAISE EXCEPTION 'commercial download row_version must advance once' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute("CREATE TRIGGER trg_commercial_download_grant_guard BEFORE UPDATE OR DELETE ON medtrust.commercial_download_grants FOR EACH ROW EXECUTE FUNCTION medtrust.guard_commercial_download_grant_v1()")
    _change_audit(True)


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM medtrust.audit_events WHERE event_type = ANY(:event_types)"),
        {"event_types": list(EVENT_RESULTS)},
    ).scalar_one()
    if count:
        raise RuntimeError("cannot remove commercial audit vocabulary while events exist")
    _change_audit(False)
    op.execute("DROP TRIGGER IF EXISTS trg_commercial_download_grant_guard ON medtrust.commercial_download_grants")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_commercial_download_grant_v1()")
    for table in ("commercial_order_lines", "commercial_demo_payments", "commercial_fulfillments"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON medtrust.{table}")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_commercial_append_only_v1()")
    op.execute("DROP TRIGGER IF EXISTS trg_commercial_order_guard ON medtrust.commercial_orders")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_commercial_order_v1()")
    op.drop_index("ix_commercial_download_grants_expiry", table_name="commercial_download_grants", schema=SCHEMA)
    op.drop_table("commercial_download_grants", schema=SCHEMA)
    op.drop_index("ix_commercial_fulfillments_space_order", table_name="commercial_fulfillments", schema=SCHEMA)
    op.drop_table("commercial_fulfillments", schema=SCHEMA)
    op.drop_table("commercial_demo_payments", schema=SCHEMA)
    op.drop_index("ix_commercial_order_lines_provider", table_name="commercial_order_lines", schema=SCHEMA)
    op.drop_table("commercial_order_lines", schema=SCHEMA)
    op.drop_index("ix_commercial_orders_requester_status", table_name="commercial_orders", schema=SCHEMA)
    op.drop_table("commercial_orders", schema=SCHEMA)
