from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.db.base import Base
from app.modules.identity.models import utc_now


SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

ORDER_STATUSES = ("agreement_pending", "awaiting_payment", "paid")
SOURCE_TYPES = ("service_access", "contract")
PAYMENT_METHODS = ("wechat_demo", "alipay_demo", "bank_card_demo")
PAYMENT_STATUSES = ("succeeded",)
FULFILLMENT_KINDS = (
    "data_document_package",
    "model_license_package",
    "execution_entitlement",
)
FULFILLMENT_STATUSES = ("ready",)
DOWNLOAD_GRANT_STATUSES = ("active", "consumed", "expired")


class CommerceInvariantError(ValueError):
    pass


class CommercialOrder(Base):
    __tablename__ = "commercial_orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    order_number: Mapped[str] = mapped_column(String(32))
    requester_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    source_type: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[UUID] = mapped_column()
    service_access_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.service_access_requests.id", ondelete="RESTRICT")
    )
    contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.contracts.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        String(24), default="agreement_pending", server_default="agreement_pending"
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="CNY", server_default="CNY"
    )
    gross_amount_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_rate_bps: Mapped[int] = mapped_column(
        Integer, default=500, server_default="500"
    )
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    provider_net_minor: Mapped[int] = mapped_column(Integer)
    quote_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    quote_digest: Mapped[str] = mapped_column(String(71))
    agreement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    agreement_digest: Mapped[str] = mapped_column(String(71))
    agreement_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    agreement_accepted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    create_idempotency_digest: Mapped[str] = mapped_column(String(71))
    agreement_idempotency_digest: Mapped[str | None] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    lines: Mapped[list["CommercialOrderLine"]] = relationship(
        back_populates="order", order_by="CommercialOrderLine.line_no"
    )
    payment: Mapped["DemoPayment | None"] = relationship(
        back_populates="order", uselist=False
    )
    fulfillments: Mapped[list["CommercialFulfillment"]] = relationship(
        back_populates="order", order_by="CommercialFulfillment.created_at"
    )

    __table_args__ = (
        CheckConstraint("source_type IN ('service_access','contract')", name="source_type"),
        CheckConstraint(
            "(source_type='service_access' AND service_access_request_id=source_id "
            "AND contract_id IS NULL) OR "
            "(source_type='contract' AND contract_id=source_id "
            "AND service_access_request_id IS NULL)",
            name="source_shape",
        ),
        CheckConstraint(
            "status IN ('agreement_pending','awaiting_payment','paid')",
            name="status",
        ),
        CheckConstraint("currency='CNY'", name="currency_cny"),
        CheckConstraint("platform_fee_rate_bps=500", name="platform_rate_frozen"),
        CheckConstraint(
            "gross_amount_minor>=0 AND platform_fee_minor>=0 AND "
            "provider_net_minor>=0 AND "
            "platform_fee_minor+provider_net_minor=gross_amount_minor",
            name="amounts_balance",
        ),
        CheckConstraint(
            "(status='agreement_pending' AND agreement_accepted_at IS NULL AND "
            "agreement_accepted_by IS NULL AND agreement_idempotency_digest IS NULL) OR "
            "(status IN ('awaiting_payment','paid') AND agreement_accepted_at IS NOT NULL "
            "AND agreement_accepted_by IS NOT NULL AND agreement_idempotency_digest IS NOT NULL)",
            name="agreement_shape",
        ),
        CheckConstraint("row_version>=1", name="row_version_positive"),
        CheckConstraint(
            "length(quote_digest)=71 AND substr(quote_digest,1,7)='sha256:' AND "
            "length(agreement_digest)=71 AND substr(agreement_digest,1,7)='sha256:' AND "
            "length(create_idempotency_digest)=71 AND "
            "substr(create_idempotency_digest,1,7)='sha256:' AND "
            "(agreement_idempotency_digest IS NULL OR "
            "(length(agreement_idempotency_digest)=71 AND "
            "substr(agreement_idempotency_digest,1,7)='sha256:'))",
            name="digest_formats",
        ),
        UniqueConstraint("space_id", "order_number", name="uq_commercial_order_number"),
        UniqueConstraint("source_type", "source_id", name="uq_commercial_order_source"),
        UniqueConstraint(
            "create_idempotency_digest", name="uq_commercial_order_create_idempotency"
        ),
        UniqueConstraint(
            "agreement_idempotency_digest", name="uq_commercial_order_agreement_idempotency"
        ),
        Index(
            "ix_commercial_orders_requester_status",
            "space_id",
            "requester_organization_id",
            "status",
            text("created_at DESC"),
        ),
    )


class CommercialOrderLine(Base):
    __tablename__ = "commercial_order_lines"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.commercial_orders.id", ondelete="RESTRICT")
    )
    line_no: Mapped[int] = mapped_column(Integer)
    provider_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    product_kind: Mapped[str] = mapped_column(String(16))
    product_id: Mapped[UUID] = mapped_column()
    version_id: Mapped[UUID] = mapped_column()
    product_name: Mapped[str] = mapped_column(Text)
    service_mode: Mapped[str] = mapped_column(String(48))
    currency: Mapped[str] = mapped_column(String(3), default="CNY", server_default="CNY")
    quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    unit_amount_minor: Mapped[int] = mapped_column(Integer)
    gross_amount_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    provider_net_minor: Mapped[int] = mapped_column(Integer)
    offer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    offer_digest: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    order: Mapped[CommercialOrder] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint("line_no>0", name="line_no_positive"),
        CheckConstraint("product_kind IN ('data','model')", name="product_kind"),
        CheckConstraint("currency='CNY'", name="currency_cny"),
        CheckConstraint("quantity=1", name="quantity_one"),
        CheckConstraint(
            "unit_amount_minor>=0 AND gross_amount_minor=unit_amount_minor*quantity AND "
            "platform_fee_minor>=0 AND provider_net_minor>=0 AND "
            "platform_fee_minor+provider_net_minor=gross_amount_minor",
            name="amounts_balance",
        ),
        CheckConstraint(
            "length(offer_digest)=71 AND substr(offer_digest,1,7)='sha256:'",
            name="offer_digest_format",
        ),
        UniqueConstraint("order_id", "line_no", name="uq_commercial_order_line_no"),
        Index("ix_commercial_order_lines_provider", "provider_organization_id", "order_id"),
    )


class DemoPayment(Base):
    __tablename__ = "commercial_demo_payments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.commercial_orders.id", ondelete="RESTRICT")
    )
    method: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="succeeded", server_default="succeeded")
    currency: Mapped[str] = mapped_column(String(3), default="CNY", server_default="CNY")
    amount_minor: Mapped[int] = mapped_column(Integer)
    channel_fee_rate_bps: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    channel_fee_minor: Mapped[int] = mapped_column(Integer)
    transaction_number: Mapped[str] = mapped_column(String(40))
    receipt_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    receipt_digest: Mapped[str] = mapped_column(String(71))
    idempotency_digest: Mapped[str] = mapped_column(String(71))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    order: Mapped[CommercialOrder] = relationship(back_populates="payment")

    __table_args__ = (
        CheckConstraint(
            "method IN ('wechat_demo','alipay_demo','bank_card_demo')", name="method"
        ),
        CheckConstraint("status='succeeded'", name="status_succeeded"),
        CheckConstraint("currency='CNY'", name="currency_cny"),
        CheckConstraint("amount_minor>=0", name="amount_nonnegative"),
        CheckConstraint("channel_fee_rate_bps=60", name="channel_rate_frozen"),
        CheckConstraint(
            "channel_fee_minor>=0 AND channel_fee_minor<=amount_minor",
            name="channel_fee_range",
        ),
        CheckConstraint(
            "length(receipt_digest)=71 AND substr(receipt_digest,1,7)='sha256:' AND "
            "length(idempotency_digest)=71 AND substr(idempotency_digest,1,7)='sha256:'",
            name="digest_formats",
        ),
        UniqueConstraint("order_id", name="uq_commercial_payment_order"),
        UniqueConstraint("transaction_number", name="uq_commercial_payment_transaction"),
        UniqueConstraint("idempotency_digest", name="uq_commercial_payment_idempotency"),
    )


class CommercialFulfillment(Base):
    __tablename__ = "commercial_fulfillments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.commercial_orders.id", ondelete="RESTRICT")
    )
    order_line_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.commercial_order_lines.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="ready", server_default="ready")
    contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.contracts.id", ondelete="RESTRICT")
    )
    entitlement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    entitlement_digest: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    order: Mapped[CommercialOrder] = relationship(back_populates="fulfillments")
    grants: Mapped[list["CommercialDownloadGrant"]] = relationship(
        back_populates="fulfillment", order_by="CommercialDownloadGrant.created_at"
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('data_document_package','model_license_package','execution_entitlement')",
            name="kind",
        ),
        CheckConstraint("status='ready'", name="status_ready"),
        CheckConstraint(
            "(kind='execution_entitlement' AND contract_id IS NOT NULL AND order_line_id IS NULL) OR "
            "(kind IN ('data_document_package','model_license_package') AND "
            "contract_id IS NULL AND order_line_id IS NOT NULL)",
            name="kind_shape",
        ),
        CheckConstraint(
            "length(entitlement_digest)=71 AND substr(entitlement_digest,1,7)='sha256:'",
            name="entitlement_digest_format",
        ),
        UniqueConstraint("order_id", "kind", name="uq_commercial_fulfillment_order_kind"),
        Index("ix_commercial_fulfillments_space_order", "space_id", "order_id"),
    )


class CommercialDownloadGrant(Base):
    __tablename__ = "commercial_download_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    fulfillment_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.commercial_fulfillments.id", ondelete="RESTRICT")
    )
    requester_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    token_digest: Mapped[str] = mapped_column(String(71))
    filename: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    max_downloads: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    download_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    create_idempotency_digest: Mapped[str] = mapped_column(String(71))
    consume_idempotency_digest: Mapped[str | None] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    fulfillment: Mapped[CommercialFulfillment] = relationship(back_populates="grants")

    __table_args__ = (
        CheckConstraint("status IN ('active','consumed','expired')", name="status"),
        CheckConstraint("max_downloads=1", name="single_use"),
        CheckConstraint(
            "download_count BETWEEN 0 AND max_downloads", name="download_count_range"
        ),
        CheckConstraint(
            "(status='active' AND download_count=0 AND consumed_at IS NULL AND "
            "consume_idempotency_digest IS NULL) OR "
            "(status='consumed' AND download_count=1 AND consumed_at IS NOT NULL AND "
            "consume_idempotency_digest IS NOT NULL) OR "
            "(status='expired' AND download_count=0 AND consumed_at IS NULL)",
            name="lifecycle_shape",
        ),
        CheckConstraint("row_version>=1", name="row_version_positive"),
        CheckConstraint(
            "length(token_digest)=71 AND substr(token_digest,1,7)='sha256:' AND "
            "length(create_idempotency_digest)=71 AND "
            "substr(create_idempotency_digest,1,7)='sha256:' AND "
            "(consume_idempotency_digest IS NULL OR "
            "(length(consume_idempotency_digest)=71 AND "
            "substr(consume_idempotency_digest,1,7)='sha256:'))",
            name="digest_formats",
        ),
        UniqueConstraint("token_digest", name="uq_commercial_download_token"),
        UniqueConstraint(
            "fulfillment_id", name="uq_commercial_download_fulfillment_once"
        ),
        UniqueConstraint(
            "create_idempotency_digest", name="uq_commercial_download_create_idempotency"
        ),
        UniqueConstraint(
            "consume_idempotency_digest", name="uq_commercial_download_consume_idempotency"
        ),
        Index("ix_commercial_download_grants_expiry", "status", "expires_at"),
    )


_ORDER_IMMUTABLE = {
    "id", "space_id", "order_number", "requester_organization_id",
    "requester_user_id", "source_type", "source_id", "service_access_request_id",
    "contract_id", "currency", "gross_amount_minor", "platform_fee_rate_bps",
    "platform_fee_minor", "provider_net_minor", "quote_snapshot", "quote_digest",
    "agreement_snapshot", "agreement_digest", "create_idempotency_digest", "created_at",
}


def _changed(target: object) -> set[str]:
    state = inspect(target)
    return {
        item.key
        for item in state.mapper.column_attrs
        if state.attrs[item.key].history.has_changes()
    }


@event.listens_for(Session, "before_flush")
def guard_commerce_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    immutable_types = (CommercialOrderLine, DemoPayment, CommercialFulfillment)
    for target in session.deleted:
        if isinstance(
            target,
            (
                CommercialOrder,
                CommercialOrderLine,
                DemoPayment,
                CommercialFulfillment,
                CommercialDownloadGrant,
            ),
        ):
            raise CommerceInvariantError("commercial evidence cannot be deleted")
    for target in session.new:
        if isinstance(target, CommercialOrder) and target.status != "agreement_pending":
            raise CommerceInvariantError("commercial order must start agreement_pending")
        if isinstance(target, CommercialDownloadGrant) and target.status != "active":
            raise CommerceInvariantError("download grant must start active")
    for target in session.dirty:
        changed = _changed(target)
        if not changed:
            continue
        if isinstance(target, immutable_types):
            raise CommerceInvariantError("commercial evidence is immutable")
        if isinstance(target, CommercialOrder):
            if changed & _ORDER_IMMUTABLE:
                raise CommerceInvariantError("commercial order price snapshot is immutable")
            history = inspect(target).attrs.status.history
            old = history.deleted[0] if history.deleted else target.status
            legal = {"agreement_pending": {"awaiting_payment"}, "awaiting_payment": {"paid"}, "paid": set()}
            if target.status not in legal.get(old, set()) or not getattr(
                target, "_transition_validated", False
            ):
                raise CommerceInvariantError(f"illegal commercial order transition: {old} -> {target.status}")
        if isinstance(target, CommercialDownloadGrant):
            stable = {
                "id", "space_id", "fulfillment_id", "requester_organization_id",
                "token_digest", "filename", "max_downloads", "expires_at",
                "create_idempotency_digest", "created_at",
            }
            if changed & stable:
                raise CommerceInvariantError("download grant identity is immutable")
            history = inspect(target).attrs.status.history
            old = history.deleted[0] if history.deleted else target.status
            if old != "active" or target.status not in {"consumed", "expired"} or not getattr(
                target, "_transition_validated", False
            ):
                raise CommerceInvariantError(f"illegal download grant transition: {old} -> {target.status}")


@event.listens_for(Session, "after_flush_postexec")
def clear_commerce_transition_markers(session: Session, _flush_context: object) -> None:
    for target in session.identity_map.values():
        if isinstance(target, (CommercialOrder, CommercialDownloadGrant)) and hasattr(
            target, "_transition_validated"
        ):
            delattr(target, "_transition_validated")
