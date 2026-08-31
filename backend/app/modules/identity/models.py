from __future__ import annotations

from datetime import datetime, timezone
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

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

ORGANIZATION_TYPES = (
    "hospital",
    "research_institute",
    "ai_company",
    "service_provider",
    "operator",
)
ORGANIZATION_VERIFICATION_STATUSES = ("unverified", "pending", "verified", "failed")
ORGANIZATION_STATUSES = ("active", "suspended", "withdrawn")
USER_STATUSES = ("invited", "active", "suspended", "disabled")
MFA_STATUSES = ("unknown", "disabled", "enabled")
MEMBER_STATUSES = ("invited", "active", "suspended", "removed")
ORGANIZATION_ROLE_CODES = (
    "provider_data_admin",
    "provider_output_reviewer",
    "consumer_researcher",
    "consumer_ai_developer",
    "contract_signer",
    "connector_operator",
    "auditor",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    legal_name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    organization_type: Mapped[str] = mapped_column(String(32))
    verification_status: Mapped[str] = mapped_column(
        String(16), default="unverified", server_default="unverified"
    )
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    external_identity_ref: Mapped[str | None] = mapped_column(Text)
    contact_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization",
        foreign_keys="OrganizationMember.organization_id",
    )

    __table_args__ = (
        CheckConstraint(
            f"organization_type IN ({sql_values(ORGANIZATION_TYPES)})",
            name="organization_type",
        ),
        CheckConstraint(
            f"verification_status IN ({sql_values(ORGANIZATION_VERIFICATION_STATUSES)})",
            name="verification_status",
        ),
        CheckConstraint(
            f"status IN ({sql_values(ORGANIZATION_STATUSES)})",
            name="status",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint("external_identity_ref", name="uq_organizations_external_identity_ref"),
        Index("ix_organizations_type_status", "organization_type", "status"),
        Index(
            "ix_organizations_verification_status",
            "verification_status",
            "status",
        ),
        Index("ix_organizations_created_by", "created_by"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    identity_issuer: Mapped[str] = mapped_column(Text)
    identity_subject: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="invited", server_default="invited")
    mfa_status: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default="unknown"
    )
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user",
        foreign_keys="OrganizationMember.user_id",
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(USER_STATUSES)})", name="status"),
        CheckConstraint(f"mfa_status IN ({sql_values(MFA_STATUSES)})", name="mfa_status"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "identity_issuer",
            "identity_subject",
            name="uq_users_issuer_subject",
        ),
        Index(
            "ix_users_email_lower",
            func.lower(email),
            postgresql_where=email.is_not(None),
            sqlite_where=email.is_not(None),
        ),
        Index("ix_users_status_last_authenticated", "status", "last_authenticated_at"),
    )


class LocalDemoCredential(Base):
    __tablename__ = "local_demo_credentials"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"), primary_key=True
    )
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class LocalDemoSession(Base):
    __tablename__ = "local_demo_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    session_digest: Mapped[str] = mapped_column(String(71), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "length(session_digest) = 71 AND substr(session_digest, 1, 7) = 'sha256:'",
            name="session_digest_format",
        ),
        Index("ix_local_demo_sessions_user_active", "user_id", "expires_at"),
    )


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16), default="invited", server_default="invited")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    organization: Mapped[Organization] = relationship(
        back_populates="memberships",
        foreign_keys=[organization_id],
    )
    user: Mapped[User] = relationship(
        back_populates="memberships",
        foreign_keys=[user_id],
    )
    roles: Mapped[list[OrganizationMemberRole]] = relationship(
        back_populates="organization_member",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(MEMBER_STATUSES)})", name="status"),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="valid_period",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_members_organization_user",
        ),
        Index("ix_organization_members_user_status", "user_id", "status"),
        Index(
            "ix_organization_members_organization_status",
            "organization_id",
            "status",
        ),
        Index("ix_organization_members_created_by", "created_by"),
    )


class OrganizationMemberRole(Base):
    __tablename__ = "organization_member_roles"

    organization_member_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.organization_members.id",
            name="fk_member_roles_member",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    role_code: Mapped[str] = mapped_column(String(48), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    granted_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    organization_member: Mapped[OrganizationMember] = relationship(back_populates="roles")

    __table_args__ = (
        CheckConstraint(
            f"role_code IN ({sql_values(ORGANIZATION_ROLE_CODES)})",
            name="role_code",
        ),
        Index(
            "ix_organization_member_roles_role_member",
            "role_code",
            "organization_member_id",
        ),
        Index("ix_organization_member_roles_granted_by", "granted_by"),
    )
