from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductSource,
    DataProductVersion,
    DataResource,
)
from app.modules.catalog.product_lifecycle import (
    ProductLifecycleError,
    approve_and_publish_product_version,
    create_product_draft,
    return_product_version,
    submit_product_version,
    update_product_draft,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.data_services.projection import resolve_data_service_capability
from app.modules.identity.models import Organization, User
from app.modules.marketplace.service_modes import build_service_offerings
from app.modules.marketplace.services import MarketplaceServiceError, require_actor
from app.modules.lifecycle.models import ProductLifecycleRequest
from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ExternalDatasetGovernanceReview,
    ExternalDatasetRecord,
)
from app.modules.external_catalog.productization import (
    ExternalProductDraftError,
    approve_and_publish_external_metadata_version,
    return_external_metadata_version,
    submit_external_metadata_version,
)


router = APIRouter(tags=["data-product-lifecycle"])
DEMO_ROLES = {
    "space_operator",
    "data_provider",
    "model_provider",
    "data_requester",
    "catalog_curator",
}


class BasicInformation(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    short_name: str = Field(default="", max_length=80)
    department: str = Field(min_length=2, max_length=120)
    disease_domain: str = Field(min_length=2, max_length=120)
    modality: str = Field(min_length=2, max_length=120)
    source_type: Literal[
        "public_demo_dataset",
        "hospital_research_data",
        "multicenter_collaboration",
        "other",
    ]
    description: str = Field(min_length=20, max_length=2000)
    data_owner: str = Field(min_length=2, max_length=120)
    contact_department: str = Field(min_length=2, max_length=120)
    is_demo: bool = True


class CompositionInformation(BaseModel):
    case_count: int = Field(ge=0, le=1_000_000_000)
    slide_count: int = Field(ge=0, le=1_000_000_000)
    image_count: int = Field(ge=0, le=1_000_000_000)
    data_format: str = Field(min_length=2, max_length=80)
    image_specification: str = Field(min_length=2, max_length=240)
    annotation_type: str = Field(min_length=2, max_length=120)
    annotation_coverage: int = Field(ge=0, le=100)
    completeness_rate: int = Field(ge=0, le=100)
    quality_status: Literal["pending", "passed", "conditional"]
    data_version: str = Field(pattern=r"^v[0-9]+(?:\.[0-9]+){0,2}$")
    version_notes: str = Field(min_length=10, max_length=1000)
    resource_summary: str = Field(min_length=10, max_length=500)

    @field_validator("image_count")
    @classmethod
    def require_data_scale(cls, value: int, info):
        if (
            value == 0
            and info.data.get("case_count", 0) == 0
            and info.data.get("slide_count", 0) == 0
        ):
            raise ValueError("at least one data scale count must be greater than zero")
        return value


class PolicyInformation(BaseModel):
    service_modes: list[
        Literal["controlled_compute", "deidentified_data_delivery"]
    ] = Field(default_factory=lambda: ["controlled_compute"], min_length=1, max_length=2)
    allowed_purposes: list[
        Literal[
            "research_analysis",
            "model_validation",
            "external_performance_validation",
            "teaching_demo",
        ]
    ] = Field(min_length=1)
    prohibited_purposes: list[str] = Field(min_length=1, max_length=12)
    max_runs: int = Field(ge=1, le=10000)
    valid_days: int = Field(ge=1, le=3650)
    fixed_model_version: bool = True
    requires_egress_review: bool = True
    internet_allowed: bool = False
    input_read_only: bool = True
    allowed_outputs: list[
        Literal[
            "aggregate_metrics",
            "confusion_matrix",
            "execution_summary",
            "raw_images",
            "model_weights",
            "connector_credentials",
        ]
    ] = Field(min_length=1)
    prohibited_outputs: list[str] = Field(min_length=1, max_length=16)
    hard_isolation: bool = False

    @field_validator("service_modes")
    @classmethod
    def require_unique_service_modes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("service modes must be unique")
        return value


class BindingInformation(BaseModel):
    connector_id: UUID
    resource_identifier: str = Field(min_length=3, max_length=64)
    data_ready: bool


class DataProductDraftRequest(BaseModel):
    basic: BasicInformation
    composition: CompositionInformation
    policy: PolicyInformation
    binding: BindingInformation


class ProductReviewRequest(BaseModel):
    review_opinion: str = Field(min_length=5, max_length=1000)
    additional_conditions: str = Field(default="", max_length=1000)
    requested_materials: str = Field(default="", max_length=1000)
    risk_level: Literal["low", "medium", "high"]
    allow_catalog: bool = False


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Data-product command API is disabled")


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    return value.strip()


async def _actor(
    session: AsyncSession,
    identity: str,
    expected: str | None = None,
) -> tuple[Any, DemoActor]:
    if identity not in DEMO_ROLES or (expected is not None and identity != expected):
        raise HTTPException(status_code=403, detail="Demo identity is not authorized")
    context = await get_phase4_context(session)
    actor = context.actors[identity]
    try:
        await require_actor(
            session,
            space_id=context.space_id,
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            role_code=identity,
        )
    except MarketplaceServiceError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context, actor


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _resource_payload(
    session: AsyncSession, version: DataProductVersion
) -> dict[str, Any] | None:
    resource = await session.scalar(
        select(DataResource).where(DataResource.data_product_version_id == version.id)
    )
    if resource is None:
        return None
    source = await session.scalar(
        select(DataProductSource).where(DataProductSource.data_resource_id == resource.id)
    )
    connector = None if source is None else await session.get(Connector, source.connector_id)
    return {
        "resource_identifier": resource.resource_code,
        "name": resource.name,
        "modality": resource.modality,
        "format": resource.format,
        "schema": resource.schema_metadata,
        "scope": resource.scope_metadata,
        "quality": resource.quality_report,
        "resource_digest": resource.resource_digest,
        "connector": (
            None
            if connector is None
            else {
                "id": str(connector.id),
                "name": connector.name,
                "runtime_status": connector.runtime_status,
                "verification_status": connector.verification_status,
                "last_heartbeat_at": _iso(connector.last_heartbeat_at),
            }
        ),
    }


async def _detail_payload(
    session: AsyncSession,
    *,
    product: DataProduct,
    version: DataProductVersion,
    identity: str,
) -> dict[str, Any]:
    provider = await session.get(Organization, product.provider_organization_id)
    external_link = await session.scalar(
        select(DataProductExternalSourceLink).where(
            DataProductExternalSourceLink.data_product_version_id == version.id
        )
    )
    service_capability = await resolve_data_service_capability(
        session,
        version=version,
        external_link=external_link,
    )
    publication = await session.scalar(
        select(DataProductPublication)
        .where(DataProductPublication.data_product_version_id == version.id)
        .order_by(DataProductPublication.published_at.desc())
        .limit(1)
    )
    latest_return = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.subject_type == "data_product_version",
            AuditEvent.subject_id == version.id,
            AuditEvent.event_type == "data_product.version.returned",
        )
        .order_by(AuditEvent.stream_sequence.desc())
        .limit(1)
    )
    latest_submit = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.subject_type == "data_product_version",
            AuditEvent.subject_id == version.id,
            AuditEvent.event_type == "data_product.version.submitted",
        )
        .order_by(AuditEvent.stream_sequence.desc())
        .limit(1)
    )
    current_lifecycle = await session.scalar(
        select(ProductLifecycleRequest)
        .where(
            ProductLifecycleRequest.target_type == "data_product",
            ProductLifecycleRequest.target_product_id == product.id,
            ProductLifecycleRequest.status == "pending",
        )
        .order_by(ProductLifecycleRequest.requested_at.desc())
        .limit(1)
    )
    allowed_actions: list[str] = []
    if (
        identity == "catalog_curator"
        and external_link is not None
        and version.status == "draft"
        and product.lifecycle_status != "archived"
    ):
        allowed_actions = ["submit"]
    elif (
        identity == "data_provider"
        and external_link is None
        and version.status == "draft"
    ):
        allowed_actions = ["edit", "submit"]
    elif identity == "space_operator" and version.status == "under_review":
        allowed_actions = ["approve", "return"]
    if identity == "data_provider" and current_lifecycle is None:
        if product.lifecycle_status == "active":
            allowed_actions.extend(["request_unpublish"])
        elif product.lifecycle_status == "unpublished":
            allowed_actions.extend(["request_relist", "request_archive"])
    publication_active = publication is not None and publication.status == "active"
    public_state = "published" if publication_active else (
        product.lifecycle_status if product.lifecycle_status in {"unpublished", "archived"} else version.status
    )
    external_metadata = None
    if external_link is not None:
        record = await session.get(
            ExternalDatasetRecord, external_link.external_dataset_record_id
        )
        source_review = await session.get(
            ExternalDatasetGovernanceReview, external_link.source_review_id
        )
        license_review = await session.get(
            ExternalDatasetGovernanceReview, external_link.license_review_id
        )
        access_review = await session.get(
            ExternalDatasetGovernanceReview, external_link.access_review_id
        )
        curator = await session.scalar(
            select(Organization).where(
                Organization.external_identity_ref == "phase4:catalog_curator"
            )
        )
        external_metadata = {
            "external_id": external_link.external_id,
            "catalog_version": external_link.catalog_version,
            "official_source_url": external_link.upstream_official_url,
            "upstream_rights_holder": external_link.upstream_rights_holder,
            "catalog_steward": provider.display_name if provider else "",
            "curator": curator.display_name if curator else "",
            "source_record_digest": external_link.source_record_digest,
            "governance_snapshot_digest": external_link.governance_snapshot_digest,
            "materialization_status": external_link.materialization_status,
            "data_holder_status": external_link.data_holder_status,
            "redistribution_status": external_link.redistribution_status,
            "execution_readiness": external_link.execution_readiness,
            "application_eligibility": False,
            "record": (
                None
                if record is None
                else {
                    "canonical_name": record.canonical_name,
                    "modalities": record.modalities,
                    "disease_areas": record.disease_areas,
                    "organs": record.organs,
                    "sample_count": record.sample_count,
                    "patient_count": record.patient_count,
                    "file_count": record.file_count,
                    "approximate_size_bytes": record.approximate_size_bytes,
                    "data_formats": record.data_formats,
                }
            ),
            "source_review": (
                None
                if source_review is None
                else {
                    "decision": source_review.decision,
                    "evidence_reference": source_review.evidence_reference,
                    "evidence_note": source_review.evidence_note,
                }
            ),
            "license_review": (
                None
                if license_review is None
                else {
                    "decision": license_review.decision,
                    "details": license_review.decision_payload,
                    "evidence_reference": license_review.evidence_reference,
                }
            ),
            "access_review": (
                None
                if access_review is None
                else {
                    "decision": access_review.decision,
                    "details": access_review.decision_payload,
                    "evidence_reference": access_review.evidence_reference,
                }
            ),
        }
    return {
        "product_id": str(product.id),
        "version_id": str(version.id),
        "product_code": product.product_code,
        "name": product.name,
        "description": product.description,
        "domain": product.domain,
        "provider": provider.display_name if provider else "",
        "provider_organization_id": str(product.provider_organization_id),
        "provider_label": (
            "catalog_steward" if external_link is not None else "data_provider"
        ),
        "status": public_state,
        "version_status": version.status,
        "version_label": version.version_label,
        "content_summary": version.content_summary,
        "scope": version.scope_metadata,
        "linkage": version.linkage_metadata,
        "quality": version.quality_report,
        "policy": version.default_policy_template,
        "provenance": version.provenance_summary,
        "snapshot_digest": version.snapshot_digest,
        "created_at": _iso(version.created_at),
        "updated_at": _iso(product.updated_at),
        "submitted_at": _iso(latest_submit.occurred_at) if latest_submit else None,
        "approved_at": _iso(version.approved_at),
        "published_at": _iso(publication.published_at) if publication else None,
        "unpublished_at": _iso(product.unpublished_at),
        "deleted_at": _iso(product.deleted_at),
        "publication_id": str(publication.id) if publication else None,
        "resource": await _resource_payload(session, version),
        "service_capability": service_capability.to_payload(),
        "offerings": build_service_offerings(
            "data",
            version.default_policy_template,
            controlled_compute_requestable=service_capability.application_eligible,
            authorization_requestable=(
                publication_active
                and version.status == "approved"
                and product.lifecycle_status == "active"
            ),
            external=external_link is not None,
        ),
        "latest_return": (
            None
            if latest_return is None
            else {
                "event_id": str(latest_return.event_id),
                "review_opinion": latest_return.evidence_snapshot.get("review_opinion"),
                "requested_materials": latest_return.evidence_snapshot.get(
                    "requested_materials"
                ),
                "risk_level": latest_return.evidence_snapshot.get("risk_level"),
                "occurred_at": _iso(latest_return.occurred_at),
            }
        ),
        "allowed_actions": allowed_actions,
        "current_lifecycle_request": (
            None
            if current_lifecycle is None
            else {
                "id": str(current_lifecycle.id),
                "action": current_lifecycle.action,
                "status": current_lifecycle.status,
                "requested_at": _iso(current_lifecycle.requested_at),
                "reason": current_lifecycle.reason,
                "impact": current_lifecycle.impact_snapshot,
            }
        ),
        "capability": {
            "hard_isolation": False,
            "raw_data_download": False,
            "clinical_use": False,
        },
        "external_metadata": external_metadata,
    }


async def _version_for_access(
    session: AsyncSession,
    *,
    version_id: UUID,
    identity: str,
    actor: DemoActor,
) -> tuple[DataProduct, DataProductVersion]:
    version = await session.get(DataProductVersion, version_id)
    product = None if version is None else await session.get(DataProduct, version.data_product_id)
    if version is None or product is None:
        raise HTTPException(status_code=404, detail="Data product version not found")
    if identity == "catalog_curator":
        external_link = await session.scalar(
            select(DataProductExternalSourceLink.id).where(
                DataProductExternalSourceLink.data_product_version_id == version.id
            )
        )
        if external_link is None:
            raise HTTPException(
                status_code=404, detail="External metadata product not found"
            )
    elif identity == "data_provider" and product.provider_organization_id != actor.organization_id:
        raise HTTPException(status_code=403, detail="Hospital cannot access another provider draft")
    if identity in {"data_requester", "model_provider"}:
        publication = await session.scalar(
            select(DataProductPublication.id).where(
                DataProductPublication.data_product_version_id == version.id,
                DataProductPublication.status == "active",
            )
        )
        if publication is None:
            raise HTTPException(status_code=404, detail="Published data product not found")
    return product, version


@router.get("/data-product-connectors")
async def data_product_connectors(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity, "data_provider")
    rows = list(
        (
            await session.scalars(
                select(Connector).where(
                    Connector.space_id == context.space_id,
                    Connector.owner_organization_id == actor.organization_id,
                )
            )
        ).all()
    )
    items = []
    for connector in rows:
        capabilities = list(
            (
                await session.scalars(
                    select(ConnectorCapability.capability_code).where(
                        ConnectorCapability.connector_id == connector.id,
                        ConnectorCapability.status == "verified",
                    )
                )
            ).all()
        )
        items.append(
            {
                "id": str(connector.id),
                "name": connector.name,
                "organization": actor.organization_name,
                "runtime_status": connector.runtime_status,
                "verification_status": connector.verification_status,
                "last_heartbeat_at": _iso(connector.last_heartbeat_at),
                "capabilities": capabilities,
            }
        )
    return {"items": items}


@router.post("/data-products", status_code=status.HTTP_201_CREATED)
async def create_data_product(
    payload: DataProductDraftRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, "data_provider")
            product, version, event = await create_product_draft(
                session,
                space_id=context.space_id,
                actor=actor,
                document=payload.model_dump(mode="json"),
                raw_key=_key(idempotency_key),
            )
        return {
            "product_id": str(product.id),
            "version_id": str(version.id),
            "product_code": product.product_code,
            "status": version.status,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except ProductLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/data-product-versions/{version_id}")
async def update_data_product(
    version_id: UUID,
    payload: DataProductDraftRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_provider")
            version = await session.get(DataProductVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Data product version not found")
            product, event = await update_product_draft(
                session,
                version=version,
                actor=actor,
                document=payload.model_dump(mode="json"),
                raw_key=_key(idempotency_key),
            )
        return {
            "product_id": str(product.id),
            "version_id": str(version.id),
            "product_code": product.product_code,
            "status": version.status,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except ProductLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/data-product-versions/{version_id}/submit")
async def submit_data_product(
    version_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity)
            version = await session.get(DataProductVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Data product version not found")
            external_link = await session.scalar(
                select(DataProductExternalSourceLink.id).where(
                    DataProductExternalSourceLink.data_product_version_id == version.id
                )
            )
            if external_link is not None:
                event, _ = await submit_external_metadata_version(
                    session,
                    version=version,
                    actor=actor,
                    raw_key=_key(idempotency_key),
                )
            else:
                if identity != "data_provider":
                    raise HTTPException(
                        status_code=403,
                        detail="Only the data provider may submit hosted data products",
                    )
                event = await submit_product_version(
                    session,
                    version=version,
                    actor=actor,
                    raw_key=_key(idempotency_key),
                )
        return {
            "version_id": str(version.id),
            "status": version.status,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except (ProductLifecycleError, ExternalProductDraftError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/data-product-versions/{version_id}/return")
async def return_data_product(
    version_id: UUID,
    payload: ProductReviewRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            version = await session.get(DataProductVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Data product version not found")
            external_link = await session.scalar(
                select(DataProductExternalSourceLink.id).where(
                    DataProductExternalSourceLink.data_product_version_id == version.id
                )
            )
            if external_link is not None:
                event, _ = await return_external_metadata_version(
                    session,
                    version=version,
                    actor=actor,
                    review=payload.model_dump(mode="json"),
                    raw_key=_key(idempotency_key),
                )
            else:
                event = await return_product_version(
                    session,
                    version=version,
                    actor=actor,
                    review=payload.model_dump(mode="json"),
                    raw_key=_key(idempotency_key),
                )
        return {
            "version_id": str(version.id),
            "status": version.status,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except (ProductLifecycleError, ExternalProductDraftError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/data-product-versions/{version_id}/approve")
async def approve_data_product(
    version_id: UUID,
    payload: ProductReviewRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            version = await session.get(DataProductVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Data product version not found")
            external_link = await session.scalar(
                select(DataProductExternalSourceLink.id).where(
                    DataProductExternalSourceLink.data_product_version_id == version.id
                )
            )
            if external_link is not None:
                publication, approved, published, _ = (
                    await approve_and_publish_external_metadata_version(
                        session,
                        version=version,
                        actor=actor,
                        review=payload.model_dump(mode="json"),
                        raw_key=_key(idempotency_key),
                    )
                )
            else:
                publication, approved, published = await approve_and_publish_product_version(
                    session,
                    version=version,
                    actor=actor,
                    review=payload.model_dump(mode="json"),
                    raw_key=_key(idempotency_key),
                )
        return {
            "version_id": str(version.id),
            "status": "published",
            "publication_id": str(publication.id),
            "approved_event_id": str(approved.event_id),
            "published_event_id": str(published.event_id),
        }
    except HTTPException:
        raise
    except (ProductLifecycleError, ExternalProductDraftError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/data-product-management")
async def data_product_management(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    query = (
        select(DataProduct, DataProductVersion)
        .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
        .where(DataProduct.space_id == context.space_id)
        .order_by(DataProduct.created_at.desc(), DataProductVersion.version_no.desc())
    )
    if identity == "data_provider":
        query = query.where(DataProduct.provider_organization_id == actor.organization_id)
    elif identity == "catalog_curator":
        query = query.join(
            DataProductExternalSourceLink,
            DataProductExternalSourceLink.data_product_version_id
            == DataProductVersion.id,
        ).where(DataProduct.lifecycle_status != "archived")
    elif identity in {"data_requester", "model_provider"}:
        query = query.join(
            DataProductPublication,
            DataProductPublication.data_product_version_id == DataProductVersion.id,
        ).where(DataProductPublication.status == "active")
    rows = (await session.execute(query)).all()
    items = [
        await _detail_payload(
            session, product=product, version=version, identity=identity
        )
        for product, version in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/data-product-catalog")
async def data_product_catalog(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    rows = (
        await session.execute(
            select(DataProduct, DataProductVersion, Organization)
            .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
            .join(
                DataProductPublication,
                DataProductPublication.data_product_version_id == DataProductVersion.id,
            )
            .join(Organization, Organization.id == DataProduct.provider_organization_id)
            .where(
                DataProduct.space_id == context.space_id,
                DataProductPublication.status == "active",
            )
            .order_by(DataProductPublication.published_at.desc())
        )
    ).all()
    items = []
    for product, version, provider in rows:
        link = await session.scalar(
            select(DataProductExternalSourceLink).where(
                DataProductExternalSourceLink.data_product_version_id == version.id
            )
        )
        service_capability = await resolve_data_service_capability(
            session,
            version=version,
            external_link=link,
        )
        items.append(
            {
                "product_id": str(product.id),
                "version_id": str(version.id),
                "version": version.version_label,
                "product_code": product.product_code,
                "name": product.name,
                "provider": provider.display_name,
                "description": product.description,
                "disease_domain": product.domain,
                "modality": version.linkage_metadata.get("modality")
                or (
                    await _resource_payload(session, version) or {}
                ).get("modality"),
                "data_scale": version.scope_metadata,
                "quality_summary": version.quality_report,
                "allowed_purposes": version.default_policy_template.get(
                    "allowed_purposes", []
                ),
                "use_mode": version.default_use_mode,
                "is_demo": product.is_demo,
                "source_kind": (
                    "external_public_metadata"
                    if link is not None
                    else "provider_data_product"
                ),
                "upstream_rights_holder": (
                    link.upstream_rights_holder
                    if link is not None
                    else provider.display_name
                ),
                "materialization_status": (
                    link.materialization_status if link is not None else "materialized"
                ),
                "execution_readiness": (
                    service_capability.execution_readiness
                ),
                "application_eligibility": service_capability.application_eligible,
                "service_capability": service_capability.to_payload(),
                "offerings": build_service_offerings(
                    "data",
                    version.default_policy_template,
                    controlled_compute_requestable=(
                        service_capability.application_eligible
                    ),
                    authorization_requestable=True,
                    external=link is not None,
                ),
                "official_source_url": (
                    link.upstream_official_url if link is not None else None
                ),
            }
        )
    return {"items": items, "total": len(items)}


@router.get("/data-product-review-queue")
async def data_product_review_queue(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity, "space_operator")
    rows = (
        await session.execute(
            select(DataProduct, DataProductVersion)
            .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
            .where(
                DataProduct.space_id == context.space_id,
                DataProductVersion.status == "under_review",
            )
            .order_by(DataProductVersion.created_at)
        )
    ).all()
    return {
        "items": [
            await _detail_payload(
                session, product=product, version=version, identity=identity
            )
            for product, version in rows
        ]
    }


@router.get("/data-product-versions/{version_id}")
async def data_product_version_detail(
    version_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    product, version = await _version_for_access(
        session, version_id=version_id, identity=identity, actor=actor
    )
    return await _detail_payload(
        session, product=product, version=version, identity=identity
    )


@router.get("/data-product-versions/{version_id}/audit-events")
async def data_product_audit_events(
    version_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    await _version_for_access(
        session, version_id=version_id, identity=identity, actor=actor
    )
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.space_id == context.space_id,
                    AuditEvent.subject_type == "data_product_version",
                    AuditEvent.subject_id == version_id,
                )
                .order_by(AuditEvent.stream_sequence.desc())
                .limit(limit)
            )
        ).all()
    )
    chain = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": context.space_id},
        )
    ).one()
    items = []
    for event in events:
        organization = (
            None
            if event.actor_organization_id is None
            else await session.get(Organization, event.actor_organization_id)
        )
        user = (
            None
            if event.actor_user_id is None
            else await session.get(User, event.actor_user_id)
        )
        outbox = list(
            (
                await session.scalars(
                    select(OutboxMessage).where(
                        OutboxMessage.audit_event_id == event.event_id
                    )
                )
            ).all()
        )
        items.append(
            {
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "result": event.result,
                "occurred_at": _iso(event.occurred_at),
                "actor": user.display_name if user else event.actor_service_code,
                "organization": organization.display_name if organization else None,
                "subject_id": str(event.subject_id),
                "state_before": event.evidence_snapshot.get("state_before"),
                "state_after": event.evidence_snapshot.get("state_after"),
                "correlation_id": str(event.correlation_id),
                "previous_hash": event.previous_event_digest,
                "current_hash": event.event_digest,
                "evidence_digest": event.evidence_digest,
                "evidence": event.evidence_snapshot,
                "outbox": [
                    {
                        "message_id": str(message.message_id),
                        "destination": message.destination,
                        "status": message.status,
                    }
                    for message in outbox
                ],
            }
        )
    return {
        "items": items,
        "audit_chain_valid": bool(chain.is_valid),
        "total": int(
            await session.scalar(
                select(func.count(AuditEvent.event_id)).where(
                    AuditEvent.subject_type == "data_product_version",
                    AuditEvent.subject_id == version_id,
                )
            )
            or 0
        ),
    }
