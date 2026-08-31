from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.modules.identity.models import Organization, sql_values, utc_now
from app.modules.spaces.models import Space

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

CONNECTOR_VERIFICATION_STATUSES = ("pending", "verified", "failed", "revoked")
CONNECTOR_RUNTIME_STATUSES = (
    "unknown",
    "online",
    "degraded",
    "offline",
    "maintenance",
)
CONNECTOR_CAPABILITY_STATUSES = ("declared", "verified", "disabled")


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    owner_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    external_connector_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    runtime_status: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default="unknown"
    )
    endpoint_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    certificate_fingerprint: Mapped[str | None] = mapped_column(Text)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_policy_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    space: Mapped[Space] = relationship(foreign_keys=[space_id])
    owner_organization: Mapped[Organization] = relationship(
        foreign_keys=[owner_organization_id]
    )
    capabilities: Mapped[list[ConnectorCapability]] = relationship(
        back_populates="connector",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            f"verification_status IN ({sql_values(CONNECTOR_VERIFICATION_STATUSES)})",
            name="verification_status",
        ),
        CheckConstraint(
            f"runtime_status IN ({sql_values(CONNECTOR_RUNTIME_STATUSES)})",
            name="runtime_status",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "space_id",
            "owner_organization_id",
            "name",
            name="uq_connectors_space_owner_name",
        ),
        Index(
            "uq_connectors_space_external_id",
            "space_id",
            "external_connector_id",
            unique=True,
            postgresql_where=text("external_connector_id IS NOT NULL"),
            sqlite_where=text("external_connector_id IS NOT NULL"),
        ),
        Index(
            "ix_connectors_space_verification_runtime",
            "space_id",
            "verification_status",
            "runtime_status",
        ),
        Index(
            "ix_connectors_owner_runtime",
            "owner_organization_id",
            "runtime_status",
        ),
        Index(
            "ix_connectors_unhealthy_heartbeat",
            "space_id",
            "last_heartbeat_at",
            postgresql_where=text("runtime_status IN ('degraded', 'offline')"),
            sqlite_where=text("runtime_status IN ('degraded', 'offline')"),
        ),
        Index("ix_connectors_created_by", "created_by"),
    )


class ConnectorCapability(Base):
    __tablename__ = "connector_capabilities"

    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.connectors.id",
            name="fk_connector_capabilities_connector",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    capability_code: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_version: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(
        String(16), default="declared", server_default="declared"
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    connector: Mapped[Connector] = relationship(back_populates="capabilities")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_values(CONNECTOR_CAPABILITY_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            "(status <> 'declared' OR verified_at IS NULL) AND "
            "(status <> 'verified' OR verified_at IS NOT NULL)",
            name="capability_verification_shape",
        ),
        Index(
            "ix_connector_capabilities_code_status",
            "capability_code",
            "status",
            "connector_id",
        ),
    )
