from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import event, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import NO_VALUE

from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductSource,
    DataProductVersion,
    DataResource,
)
from app.modules.connectors.models import Connector, ConnectorCapability


class CatalogInvariantError(ValueError):
    """Raised when a Catalog command violates a frozen domain invariant."""


def _require_schema_version(payload: object, label: str) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("schema_version"), str):
        raise CatalogInvariantError(f"{label} requires a schema_version")


def _old_value(target: object, attribute_name: str, current: str) -> str:
    history = inspect(target).attrs[attribute_name].history
    return history.deleted[0] if history.deleted else current


def _changed_columns(target: object) -> set[str]:
    state = inspect(target)
    return {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }


def _version_for_resource(session: Session, resource: DataResource) -> DataProductVersion | None:
    loaded_version = inspect(resource).attrs.version.loaded_value
    if loaded_version is not NO_VALUE:
        return loaded_version
    if resource.data_product_version_id is None:
        return None
    return session.get(DataProductVersion, resource.data_product_version_id)


def _version_for_source(
    session: Session, source: DataProductSource
) -> DataProductVersion | None:
    loaded_resource = inspect(source).attrs.resource.loaded_value
    resource = loaded_resource if loaded_resource is not NO_VALUE else None
    if resource is None and source.data_resource_id is not None:
        resource = session.get(DataResource, source.data_resource_id)
    return _version_for_resource(session, resource) if resource is not None else None


def _guard_version_update(version: DataProductVersion) -> None:
    changed = _changed_columns(version)
    if not changed:
        return

    old_status = _old_value(version, "status", version.status)
    new_status = version.status
    allowed_transitions = {
        "draft": {"draft", "under_review"},
        "under_review": {"draft", "approved"},
        "approved": {"retired"},
        "retired": set(),
    }
    if new_status not in allowed_transitions[old_status]:
        raise CatalogInvariantError(
            f"invalid product version transition: {old_status} -> {new_status}"
        )

    if old_status == "under_review":
        allowed = {"status", "approved_at", "approved_by"}
        if changed - allowed:
            raise CatalogInvariantError(
                "under_review product version must return to draft before content changes"
            )
        if new_status == "approved" and (
            version.approved_at is None or version.approved_by is None
        ):
            raise CatalogInvariantError("approval actor and timestamp are required")
        if new_status == "draft" and (
            version.approved_at is not None or version.approved_by is not None
        ):
            raise CatalogInvariantError("draft product version cannot retain approval data")

    if old_status == "approved" and changed != {"status"}:
        raise CatalogInvariantError("approved product version content is immutable")

    if old_status == "retired":
        raise CatalogInvariantError("retired product version is immutable")


def _guard_publication_update(publication: DataProductPublication) -> None:
    changed = _changed_columns(publication)
    if not changed:
        return
    old_status = _old_value(publication, "status", publication.status)
    if old_status != "active" or publication.status not in {"withdrawn", "expired"}:
        raise CatalogInvariantError(
            f"invalid publication transition: {old_status} -> {publication.status}"
        )
    allowed = {"status", "withdrawn_at", "withdrawn_by", "withdrawal_reason"}
    if changed - allowed:
        raise CatalogInvariantError("publication identity and visibility are immutable")


@event.listens_for(Session, "before_flush")
def guard_catalog_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.new:
        if isinstance(target, DataProductVersion) and target.status not in (None, "draft"):
            raise CatalogInvariantError("new product version must start as draft")
        if isinstance(target, DataProductPublication) and target.status not in (None, "active"):
            raise CatalogInvariantError("new publication must start as active")
        if isinstance(target, (DataResource, DataProductSource)):
            version = (
                _version_for_resource(session, target)
                if isinstance(target, DataResource)
                else _version_for_source(session, target)
            )
            if version is not None and version.status != "draft":
                raise CatalogInvariantError("resources and sources can only change in draft")

    for target in session.dirty:
        if isinstance(target, DataProductVersion):
            _guard_version_update(target)
        elif isinstance(target, DataProductPublication):
            _guard_publication_update(target)
        elif isinstance(target, (DataResource, DataProductSource)):
            version = (
                _version_for_resource(session, target)
                if isinstance(target, DataResource)
                else _version_for_source(session, target)
            )
            if version is not None and version.status != "draft":
                raise CatalogInvariantError("resources and sources can only change in draft")

    for target in session.deleted:
        if isinstance(target, DataProductVersion) and target.status != "draft":
            raise CatalogInvariantError("only an unreviewed draft version can be deleted")
        if isinstance(target, DataProductPublication):
            raise CatalogInvariantError("publication history cannot be deleted")
        if isinstance(target, (DataResource, DataProductSource)):
            version = (
                _version_for_resource(session, target)
                if isinstance(target, DataResource)
                else _version_for_source(session, target)
            )
            if version is None or version.status != "draft":
                raise CatalogInvariantError("resources and sources can only change in draft")


async def submit_version_for_review(
    session: AsyncSession,
    version: DataProductVersion,
    *,
    require_bound_source: bool = True,
) -> None:
    if version.status != "draft":
        raise CatalogInvariantError("only a draft version can be submitted for review")
    if not version.snapshot_digest or not version.default_policy_digest:
        raise CatalogInvariantError("version and default policy digests are required")
    for label, payload in (
        ("scope_metadata", version.scope_metadata),
        ("linkage_metadata", version.linkage_metadata),
        ("quality_report", version.quality_report),
        ("default_policy_template", version.default_policy_template),
        ("provenance_summary", version.provenance_summary),
    ):
        _require_schema_version(payload, label)

    await session.flush()
    resources = list(
        (
            await session.scalars(
                select(DataResource).where(
                    DataResource.data_product_version_id == version.id
                )
            )
        ).all()
    )
    if not resources:
        raise CatalogInvariantError("a version must contain at least one data resource")
    if any(not resource.resource_digest for resource in resources):
        raise CatalogInvariantError("every data resource requires a digest")
    for resource in resources:
        for label, payload in (
            ("schema_metadata", resource.schema_metadata),
            ("scope_metadata", resource.scope_metadata),
            ("quality_report", resource.quality_report),
        ):
            _require_schema_version(payload, f"resource {resource.resource_code} {label}")

    source_counts = dict(
        (
            await session.execute(
                select(
                    DataProductSource.data_resource_id,
                    func.count().label("source_count"),
                )
                .where(
                    DataProductSource.data_resource_id.in_(
                        [resource.id for resource in resources]
                    )
                )
                .group_by(DataProductSource.data_resource_id)
            )
        ).all()
    )
    if require_bound_source and any(
        source_counts.get(resource.id, 0) < 1 for resource in resources
    ):
        raise CatalogInvariantError("every data resource requires at least one source")

    version.status = "under_review"
    await session.flush()


async def return_version_to_draft(
    session: AsyncSession, version: DataProductVersion
) -> None:
    if version.status != "under_review":
        raise CatalogInvariantError("only an under_review version can return to draft")
    version.approved_at = None
    version.approved_by = None
    version.status = "draft"
    await session.flush()


async def approve_version(
    session: AsyncSession,
    version: DataProductVersion,
    *,
    approved_by: UUID,
) -> None:
    if version.status != "under_review":
        raise CatalogInvariantError("only an under_review version can be approved")
    version.approved_at = datetime.now(timezone.utc)
    version.approved_by = approved_by
    version.status = "approved"
    await session.flush()


async def retire_version(session: AsyncSession, version: DataProductVersion) -> None:
    if version.status != "approved":
        raise CatalogInvariantError("only an approved version can be retired")
    version.status = "retired"
    await session.flush()


async def add_product_source(
    session: AsyncSession,
    resource: DataResource,
    connector: Connector,
    *,
    local_resource_alias: str,
    source_digest: str,
    source_role: str,
    source_snapshot_at: datetime,
) -> DataProductSource:
    await session.flush()
    version = await session.get(DataProductVersion, resource.data_product_version_id)
    if version is None or version.status != "draft":
        raise CatalogInvariantError("sources can only be added to a draft version")
    product = await session.get(DataProduct, version.data_product_id)
    if product is None:
        raise CatalogInvariantError("source product does not exist")
    if connector.space_id != version.space_id:
        raise CatalogInvariantError("source connector must belong to the product space")
    if connector.owner_organization_id != product.provider_organization_id:
        raise CatalogInvariantError(
            "V1 source connector must be owned by the product provider"
        )
    if connector.verification_status != "verified" or connector.runtime_status not in {
        "online",
        "degraded",
    }:
        raise CatalogInvariantError("source connector is not available for cataloging")

    capability_exists = await session.scalar(
        select(ConnectorCapability.connector_id).where(
            ConnectorCapability.connector_id == connector.id,
            ConnectorCapability.capability_code == "product_publish",
            ConnectorCapability.status == "verified",
        )
    )
    if capability_exists is None:
        raise CatalogInvariantError("source connector lacks verified product_publish capability")

    source = DataProductSource(
        resource=resource,
        connector=connector,
        local_resource_alias=local_resource_alias,
        source_digest=source_digest,
        source_role=source_role,
        source_snapshot_at=source_snapshot_at,
    )
    session.add(source)
    await session.flush()
    return source


async def publish_version(
    session: AsyncSession,
    product: DataProduct,
    version: DataProductVersion,
    *,
    published_by: UUID,
    visibility: str,
) -> DataProductPublication:
    await session.flush()
    if version.status != "approved":
        raise CatalogInvariantError("only an approved version can be published")
    if version.data_product_id != product.id or version.space_id != product.space_id:
        raise CatalogInvariantError("publication product and version do not match")
    if product.lifecycle_status not in {"draft", "active"}:
        raise CatalogInvariantError("product lifecycle does not allow publication")

    existing = await session.scalar(
        select(DataProductPublication.id).where(
            DataProductPublication.data_product_id == product.id,
            DataProductPublication.status == "active",
        )
    )
    if existing is not None:
        raise CatalogInvariantError("product already has an active publication")

    publication = DataProductPublication(
        space_id=product.space_id,
        data_product_id=product.id,
        data_product_version_id=version.id,
        status="active",
        visibility=visibility,
        published_by=published_by,
    )
    if product.lifecycle_status == "draft":
        product.lifecycle_status = "active"
    session.add(publication)
    await session.flush()
    return publication


async def withdraw_publication(
    session: AsyncSession,
    publication: DataProductPublication,
    *,
    withdrawn_by: UUID,
    reason: str,
) -> None:
    if publication.status != "active":
        raise CatalogInvariantError("only an active publication can be withdrawn")
    publication.status = "withdrawn"
    publication.withdrawn_at = datetime.now(timezone.utc)
    publication.withdrawn_by = withdrawn_by
    publication.withdrawal_reason = reason
    await session.flush()
