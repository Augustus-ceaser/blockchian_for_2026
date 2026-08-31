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
    ForeignKeyConstraint,
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
from app.modules.connectors.models import Connector
from app.modules.identity.models import Organization, sql_values, utc_now
from app.modules.spaces.models import Space

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

PRODUCT_TYPES = ("controlled_compute", "api", "file", "model_service")
PRODUCT_STATUSES = ("draft", "active", "suspended", "unpublished", "expired", "archived")
VERSION_STATUSES = ("draft", "under_review", "approved", "retired")
SOURCE_ROLES = ("primary", "secondary")
PUBLICATION_STATUSES = ("active", "withdrawn", "expired")
PUBLICATION_VISIBILITIES = ("space", "restricted", "invitation_only")


class DataProduct(Base):
    __tablename__ = "data_products"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    provider_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    product_code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    product_type: Mapped[str] = mapped_column(String(32))
    domain: Mapped[str] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft"
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
    unpublished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    space: Mapped[Space] = relationship(foreign_keys=[space_id])
    provider_organization: Mapped[Organization] = relationship(
        foreign_keys=[provider_organization_id]
    )
    versions: Mapped[list[DataProductVersion]] = relationship(
        back_populates="product", passive_deletes="all", overlaps="space"
    )
    publications: Mapped[list[DataProductPublication]] = relationship(
        back_populates="product", passive_deletes="all"
    )

    __table_args__ = (
        CheckConstraint(
            f"product_type IN ({sql_values(PRODUCT_TYPES)})", name="product_type"
        ),
        CheckConstraint(
            f"lifecycle_status IN ({sql_values(PRODUCT_STATUSES)})",
            name="lifecycle_status",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint("space_id", "product_code", name="uq_data_products_space_code"),
        UniqueConstraint("space_id", "id", name="uq_data_products_space_id_pair"),
        UniqueConstraint(
            "space_id",
            "provider_organization_id",
            "id",
            name="uq_data_products_space_provider_id_pair",
        ),
        Index(
            "ix_data_products_space_status_domain",
            "space_id",
            "lifecycle_status",
            "domain",
        ),
        Index(
            "ix_data_products_provider_status",
            "provider_organization_id",
            "lifecycle_status",
        ),
        Index("ix_data_products_created_by", "created_by"),
    )


class DataProductVersion(Base):
    __tablename__ = "data_product_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    data_product_id: Mapped[UUID] = mapped_column()
    version_no: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft"
    )
    content_summary: Mapped[str] = mapped_column(Text)
    scope_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    linkage_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    quality_report: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    classification_level: Mapped[str] = mapped_column(Text)
    default_use_mode: Mapped[str] = mapped_column(Text)
    default_policy_template: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    default_policy_digest: Mapped[str] = mapped_column(Text)
    provenance_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    snapshot_digest: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    space: Mapped[Space] = relationship(foreign_keys=[space_id], overlaps="product,versions")
    product: Mapped[DataProduct] = relationship(
        back_populates="versions",
        foreign_keys=[space_id, data_product_id],
        overlaps="space",
    )
    resources: Mapped[list[DataResource]] = relationship(
        back_populates="version", passive_deletes="all", overlaps="space"
    )
    publications: Mapped[list[DataProductPublication]] = relationship(
        back_populates="version",
        passive_deletes="all",
        overlaps="product,publications",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "data_product_id"],
            [f"{SCHEMA}.data_products.space_id", f"{SCHEMA}.data_products.id"],
            name="fk_product_versions_space_product",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint(f"status IN ({sql_values(VERSION_STATUSES)})", name="status"),
        CheckConstraint(
            "(approved_at IS NULL AND approved_by IS NULL) OR "
            "(approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="approval_pair",
        ),
        CheckConstraint(
            "status = 'draft' OR snapshot_digest IS NOT NULL",
            name="snapshot_required_after_draft",
        ),
        UniqueConstraint(
            "data_product_id", "version_no", name="uq_product_versions_product_no"
        ),
        UniqueConstraint(
            "data_product_id", "version_label", name="uq_product_versions_product_label"
        ),
        UniqueConstraint(
            "data_product_id", "id", name="uq_product_versions_product_id_pair"
        ),
        UniqueConstraint("space_id", "id", name="uq_product_versions_space_id_pair"),
        UniqueConstraint(
            "data_product_id",
            "snapshot_digest",
            name="uq_product_versions_product_digest",
        ),
        UniqueConstraint(
            "id",
            "snapshot_digest",
            name="uq_product_versions_id_digest",
        ),
        Index(
            "ix_product_versions_space_status_created",
            "space_id",
            "status",
            text("created_at DESC"),
        ),
        Index(
            "ix_product_versions_product_status_no",
            "data_product_id",
            "status",
            text("version_no DESC"),
        ),
        Index("ix_product_versions_approved_by", "approved_by"),
        Index("ix_product_versions_created_by", "created_by"),
    )


class DataResource(Base):
    __tablename__ = "data_resources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    data_product_version_id: Mapped[UUID] = mapped_column()
    resource_code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(Text)
    modality: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(Text)
    schema_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    scope_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    quality_report: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    classification_level: Mapped[str] = mapped_column(Text)
    resource_digest: Mapped[str | None] = mapped_column(Text)
    position_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )

    space: Mapped[Space] = relationship(foreign_keys=[space_id], overlaps="resources,version")
    version: Mapped[DataProductVersion] = relationship(
        back_populates="resources",
        foreign_keys=[space_id, data_product_version_id],
        overlaps="space",
    )
    sources: Mapped[list[DataProductSource]] = relationship(
        back_populates="resource", passive_deletes="all"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "data_product_version_id"],
            [
                f"{SCHEMA}.data_product_versions.space_id",
                f"{SCHEMA}.data_product_versions.id",
            ],
            name="fk_data_resources_space_version",
            ondelete="CASCADE",
        ),
        CheckConstraint("position_no > 0", name="position_no_positive"),
        UniqueConstraint(
            "data_product_version_id",
            "resource_code",
            name="uq_data_resources_version_code",
        ),
        UniqueConstraint(
            "data_product_version_id",
            "position_no",
            name="uq_data_resources_version_position",
        ),
        UniqueConstraint(
            "data_product_version_id", "id", name="uq_data_resources_version_id_pair"
        ),
        Index(
            "ix_data_resources_space_version_position",
            "space_id",
            "data_product_version_id",
            "position_no",
        ),
        Index("ix_data_resources_created_by", "created_by"),
    )


class DataProductSource(Base):
    __tablename__ = "product_sources"

    data_resource_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.data_resources.id",
            name="fk_product_sources_resource",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.connectors.id",
            name="fk_product_sources_connector",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    local_resource_alias: Mapped[str] = mapped_column(Text, primary_key=True)
    source_digest: Mapped[str] = mapped_column(Text)
    source_role: Mapped[str] = mapped_column(String(16))
    source_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    resource: Mapped[DataResource] = relationship(back_populates="sources")
    connector: Mapped[Connector] = relationship(foreign_keys=[connector_id])

    __table_args__ = (
        CheckConstraint(f"source_role IN ({sql_values(SOURCE_ROLES)})", name="source_role"),
        Index(
            "ix_product_sources_connector_resource", "connector_id", "data_resource_id"
        ),
    )


class DataProductPublication(Base):
    __tablename__ = "data_product_publications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    data_product_id: Mapped[UUID] = mapped_column()
    data_product_version_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    visibility: Mapped[str] = mapped_column(String(24))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    published_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(Text)

    space: Mapped[Space] = relationship(
        foreign_keys=[space_id], overlaps="product,publications"
    )
    product: Mapped[DataProduct] = relationship(
        back_populates="publications",
        foreign_keys=[space_id, data_product_id],
        overlaps="publications,space,version",
    )
    version: Mapped[DataProductVersion] = relationship(
        back_populates="publications",
        foreign_keys=[data_product_id, data_product_version_id],
        overlaps="product,publications",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "data_product_id"],
            [f"{SCHEMA}.data_products.space_id", f"{SCHEMA}.data_products.id"],
            name="fk_publications_space_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["data_product_id", "data_product_version_id"],
            [
                f"{SCHEMA}.data_product_versions.data_product_id",
                f"{SCHEMA}.data_product_versions.id",
            ],
            name="fk_publications_product_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({sql_values(PUBLICATION_STATUSES)})", name="status"
        ),
        CheckConstraint(
            f"visibility IN ({sql_values(PUBLICATION_VISIBILITIES)})", name="visibility"
        ),
        CheckConstraint(
            "(withdrawn_at IS NULL AND withdrawn_by IS NULL) OR "
            "(withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)",
            name="withdrawal_pair",
        ),
        CheckConstraint(
            "status != 'withdrawn' OR "
            "(withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)",
            name="withdrawn_requires_actor",
        ),
        CheckConstraint(
            "status = 'withdrawn' OR "
            "(withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawal_reason IS NULL)",
            name="nonwithdrawn_has_no_withdrawal",
        ),
        Index(
            "uq_publications_active_product",
            "data_product_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "uq_publications_active_version",
            "data_product_version_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_publications_space_status_published",
            "space_id",
            "status",
            text("published_at DESC"),
        ),
        Index(
            "ix_publications_version_published",
            "data_product_version_id",
            text("published_at DESC"),
        ),
        Index("ix_publications_published_by", "published_by"),
        Index("ix_publications_withdrawn_by", "withdrawn_by"),
    )
