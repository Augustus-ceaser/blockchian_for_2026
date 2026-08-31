from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.identity.models import utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
TARGET_TYPES = ("data_product", "model_product")
ACTIONS = ("unpublish", "relist", "archive")
REQUEST_STATUSES = ("pending", "approved", "rejected", "returned", "cancelled")


class ProductLifecycleRequest(Base):
    __tablename__ = "product_lifecycle_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT"))
    target_type: Mapped[str] = mapped_column(String(24))
    target_product_id: Mapped[UUID] = mapped_column()
    target_version_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(16))
    requested_by_user_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    requested_by_organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, server_default=text("'{}'"))
    impact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    impact_digest: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(16))
    idempotency_digest: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now())
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint("target_type IN ('data_product','model_product')", name="target_type"),
        CheckConstraint("action IN ('unpublish','relist','archive')", name="action"),
        CheckConstraint("status IN ('pending','approved','rejected','returned','cancelled')", name="status"),
        CheckConstraint("length(impact_digest) = 71 AND substr(impact_digest, 1, 7) = 'sha256:'", name="impact_digest"),
        CheckConstraint("length(idempotency_digest) = 71 AND substr(idempotency_digest, 1, 7) = 'sha256:'", name="idempotency_digest"),
        CheckConstraint("row_version >= 1", name="row_version"),
        Index("uq_product_lifecycle_requests_open_target", "space_id", "target_type", "target_product_id", unique=True, postgresql_where=text("status = 'pending'"), sqlite_where=text("status = 'pending'")),
        Index("ix_product_lifecycle_requests_queue", "space_id", "status", "requested_at"),
        Index("ix_product_lifecycle_requests_owner", "requested_by_organization_id", "status"),
    )
