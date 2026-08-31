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

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

SPACE_TYPES = ("industry", "enterprise", "city")
SPACE_STATUSES = ("draft", "active", "suspended", "closed")
PARTICIPANT_ADMISSION_STATUSES = (
    "applied",
    "reviewing",
    "admitted",
    "rejected",
    "suspended",
    "exited",
)
SPACE_PARTICIPANT_ROLE_CODES = (
    "provider",
    "consumer",
    "service_provider",
    "operator",
    "space_operator",
    "data_provider",
    "model_provider",
    "data_requester",
    "catalog_curator",
)


class Space(Base):
    __tablename__ = "spaces"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    space_type: Mapped[str] = mapped_column(String(16))
    operator_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    ruleset_version: Mapped[str] = mapped_column(Text)
    classification_scheme_version: Mapped[str] = mapped_column(Text)
    default_retention_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
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

    operator_organization: Mapped[Organization] = relationship(
        foreign_keys=[operator_organization_id]
    )
    participants: Mapped[list[SpaceParticipant]] = relationship(back_populates="space")

    __table_args__ = (
        CheckConstraint(f"space_type IN ({sql_values(SPACE_TYPES)})", name="space_type"),
        CheckConstraint(f"status IN ({sql_values(SPACE_STATUSES)})", name="status"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint("code", name="uq_spaces_code"),
        Index("ix_spaces_operator_status", "operator_organization_id", "status"),
        Index("ix_spaces_type_status", "space_type", "status"),
        Index("ix_spaces_created_by", "created_by"),
    )


class SpaceParticipant(Base):
    __tablename__ = "space_participants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    admission_status: Mapped[str] = mapped_column(
        String(16), default="applied", server_default="applied"
    )
    ruleset_accepted_version: Mapped[str | None] = mapped_column(Text)
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    space: Mapped[Space] = relationship(back_populates="participants")
    organization: Mapped[Organization] = relationship(foreign_keys=[organization_id])
    roles: Mapped[list[SpaceParticipantRole]] = relationship(
        back_populates="space_participant",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            f"admission_status IN ({sql_values(PARTICIPANT_ADMISSION_STATUSES)})",
            name="admission_status",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "space_id",
            "organization_id",
            name="uq_space_participants_space_organization",
        ),
        Index(
            "ix_space_participants_organization_status",
            "organization_id",
            "admission_status",
        ),
        Index(
            "ix_space_participants_admitted",
            "space_id",
            "admitted_at",
            postgresql_where=text("admission_status = 'admitted'"),
            sqlite_where=text("admission_status = 'admitted'"),
        ),
        Index("ix_space_participants_created_by", "created_by"),
    )


class SpaceParticipantRole(Base):
    __tablename__ = "space_participant_roles"

    space_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.space_participants.id",
            name="fk_space_roles_participant",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    role_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    granted_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    space_participant: Mapped[SpaceParticipant] = relationship(back_populates="roles")

    __table_args__ = (
        CheckConstraint(
            f"role_code IN ({sql_values(SPACE_PARTICIPANT_ROLE_CODES)})",
            name="role_code",
        ),
        Index(
            "ix_space_participant_roles_role_participant",
            "role_code",
            "space_participant_id",
        ),
        Index("ix_space_participant_roles_granted_by", "granted_by"),
    )
