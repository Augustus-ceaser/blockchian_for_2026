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
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from app.modules.identity.models import utc_now


SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

SERVICE_ACCESS_STATUSES = (
    "submitted",
    "provider_approved",
    "approved_pending_contract",
    "rejected",
)


class ServiceAccessRequest(Base):
    """A review request only; it is not a payment, contract, or delivery grant."""

    __tablename__ = "service_access_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    request_number: Mapped[str] = mapped_column(String(32))
    requester_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    provider_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    product_kind: Mapped[str] = mapped_column(String(16))
    product_id: Mapped[UUID] = mapped_column()
    version_id: Mapped[UUID] = mapped_column()
    service_mode: Mapped[str] = mapped_column(String(48))
    purpose: Mapped[str] = mapped_column(Text)
    intended_use: Mapped[str] = mapped_column(Text)
    requested_duration_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(32), default="submitted", server_default="submitted"
    )
    product_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    product_snapshot_digest: Mapped[str] = mapped_column(String(71))
    request_digest: Mapped[str] = mapped_column(String(71))
    create_idempotency_digest: Mapped[str] = mapped_column(String(71))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    provider_decision: Mapped[str | None] = mapped_column(String(16))
    provider_decision_summary: Mapped[str | None] = mapped_column(Text)
    provider_decided_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    provider_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    provider_decision_idempotency_digest: Mapped[str | None] = mapped_column(
        String(71)
    )
    operator_decision: Mapped[str | None] = mapped_column(String(16))
    operator_decision_summary: Mapped[str | None] = mapped_column(Text)
    operator_decided_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    operator_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    operator_decision_idempotency_digest: Mapped[str | None] = mapped_column(
        String(71)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "product_kind IN ('data','model')", name="product_kind"
        ),
        CheckConstraint(
            "(product_kind='data' AND service_mode='deidentified_data_delivery') OR "
            "(product_kind='model' AND service_mode='model_artifact_license')",
            name="kind_mode_pair",
        ),
        CheckConstraint(
            "status IN ('submitted','provider_approved',"
            "'approved_pending_contract','rejected')",
            name="status",
        ),
        CheckConstraint(
            "requested_duration_days BETWEEN 1 AND 3650", name="duration_range"
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint(
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
            name="digest_formats",
        ),
        CheckConstraint(
            "provider_decision IS NULL OR provider_decision IN ('approve','reject')",
            name="provider_decision",
        ),
        CheckConstraint(
            "operator_decision IS NULL OR operator_decision IN ('approve','reject')",
            name="operator_decision",
        ),
        CheckConstraint(
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
            name="lifecycle_shape",
        ),
        UniqueConstraint(
            "space_id", "request_number", name="uq_service_access_space_number"
        ),
        UniqueConstraint(
            "create_idempotency_digest", name="uq_service_access_create_idempotency"
        ),
        UniqueConstraint(
            "provider_decision_idempotency_digest",
            name="uq_service_access_provider_decision_idempotency",
        ),
        UniqueConstraint(
            "operator_decision_idempotency_digest",
            name="uq_service_access_operator_decision_idempotency",
        ),
        Index(
            "ix_service_access_requester_status",
            "space_id",
            "requester_organization_id",
            "status",
            text("requested_at DESC"),
        ),
        Index(
            "ix_service_access_provider_status",
            "space_id",
            "provider_organization_id",
            "product_kind",
            "status",
            text("requested_at DESC"),
        ),
    )


class ServiceAccessInvariantError(ValueError):
    pass


_IMMUTABLE_FIELDS = {
    "id",
    "space_id",
    "request_number",
    "requester_organization_id",
    "requester_user_id",
    "provider_organization_id",
    "product_kind",
    "product_id",
    "version_id",
    "service_mode",
    "purpose",
    "intended_use",
    "requested_duration_days",
    "product_snapshot",
    "product_snapshot_digest",
    "request_digest",
    "create_idempotency_digest",
    "requested_at",
}
_MUTABLE_FIELDS = {
    "status",
    "provider_decision",
    "provider_decision_summary",
    "provider_decided_by",
    "provider_decided_at",
    "provider_decision_idempotency_digest",
    "operator_decision",
    "operator_decision_summary",
    "operator_decided_by",
    "operator_decided_at",
    "operator_decision_idempotency_digest",
    "updated_at",
    "row_version",
}


def _changed_columns(target: object) -> set[str]:
    state = inspect(target)
    return {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }


def _old_status(target: ServiceAccessRequest) -> str:
    history = inspect(target).attrs.status.history
    return history.deleted[0] if history.deleted else target.status


@event.listens_for(Session, "before_flush")
def guard_service_access_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.deleted:
        if isinstance(target, ServiceAccessRequest):
            raise ServiceAccessInvariantError(
                "service access request history cannot be deleted"
            )
    for target in session.new:
        if isinstance(target, ServiceAccessRequest) and target.status != "submitted":
            raise ServiceAccessInvariantError(
                "a service access request must start submitted"
            )
    for target in session.dirty:
        if not isinstance(target, ServiceAccessRequest):
            continue
        changed = _changed_columns(target)
        if not changed:
            continue
        if changed & _IMMUTABLE_FIELDS or changed - _MUTABLE_FIELDS:
            raise ServiceAccessInvariantError(
                "service access request identity and snapshots are immutable"
            )
        old = _old_status(target)
        legal = {
            "submitted": {"provider_approved", "rejected"},
            "provider_approved": {"approved_pending_contract", "rejected"},
            "approved_pending_contract": set(),
            "rejected": set(),
        }
        if target.status not in legal.get(old, set()):
            raise ServiceAccessInvariantError(
                f"illegal service access transition: {old} -> {target.status}"
            )
        if not getattr(target, "_transition_validated", False):
            raise ServiceAccessInvariantError(
                "service access transition requires the lifecycle service"
            )


@event.listens_for(Session, "after_flush_postexec")
def clear_service_access_transition_markers(
    session: Session, _flush_context: object
) -> None:
    for target in session.identity_map.values():
        if isinstance(target, ServiceAccessRequest) and hasattr(
            target, "_transition_validated"
        ):
            delattr(target, "_transition_validated")
