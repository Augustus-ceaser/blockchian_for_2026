from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context, load_pathmnist_model_registry
from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.identity.models import Organization, User
from app.modules.external_catalog.models import (
    ExternalModelGovernanceReview,
    ExternalModelRecord,
    ModelProductExternalSourceLink,
)
from app.modules.marketplace.model_lifecycle import (
    ModelLifecycleError,
    approve_and_publish_model,
    create_model_draft,
    return_model_draft,
    submit_model_draft,
    update_model_draft,
)
from app.modules.marketplace.models import ModelProduct, ModelPublication, ModelVersion
from app.modules.marketplace.service_modes import build_service_offerings
from app.modules.marketplace.services import MarketplaceServiceError, require_actor
from app.modules.lifecycle.models import ProductLifecycleRequest


router = APIRouter(tags=["model-product-lifecycle"])
WORKSPACE = Path(__file__).resolve().parents[4]
DEMO_ROLES = {
    "space_operator",
    "data_provider",
    "model_provider",
    "data_requester",
    "catalog_curator",
}


class ModelBasicInformation(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    short_name: str = Field(default="", max_length=80)
    team: str = Field(min_length=2, max_length=120)
    task_type: Literal[
        "image_classification",
        "lesion_detection",
        "image_segmentation",
        "risk_prediction",
        "prognosis_prediction",
        "quality_control",
        "other",
    ]
    task_description: str = Field(min_length=2, max_length=160)
    disease_domain: str = Field(min_length=2, max_length=120)
    modality: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=20, max_length=2000)
    source_type: Literal[
        "platform_allowlisted",
        "partner_preregistered",
        "public_research",
        "other",
    ]
    model_owner: str = Field(min_length=2, max_length=120)
    contact_department: str = Field(min_length=2, max_length=120)
    is_demo: bool = True
    clinical_use: bool = False


class ModelRuntimeInformation(BaseModel):
    version_label: str = Field(pattern=r"^v[0-9]+(?:\.[0-9]+){0,2}$")
    version_notes: str = Field(min_length=10, max_length=1000)
    framework: str = Field(min_length=2, max_length=80)
    runtime: str = Field(min_length=2, max_length=120)
    model_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    entrypoint_id: str = Field(min_length=3, max_length=96)
    input_schema_version: str = Field(min_length=3, max_length=120)
    output_schema_version: str = Field(min_length=3, max_length=120)
    device: Literal["cpu"]
    cpu_limit: int = Field(ge=1, le=64)
    memory_limit_mb: int = Field(ge=128, le=262144)
    timeout_seconds: int = Field(ge=1, le=86400)
    network_access: bool = False
    input_read_only: bool = True
    dynamic_dependencies: bool = False
    arbitrary_code: bool = False
    model_ready: bool
    executor_type: Literal["local_builtin"]


class ModelSchemaInformation(BaseModel):
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_outputs: list[
        Literal[
            "aggregate_metrics",
            "confusion_matrix",
            "execution_summary",
            "model_weights",
            "intermediate_features",
            "raw_input_images",
            "arbitrary_scripts",
            "unapproved_sample_predictions",
            "runtime_credentials",
        ]
    ] = Field(min_length=1)
    prohibited_outputs: list[str] = Field(min_length=1, max_length=16)


class ModelPolicyInformation(BaseModel):
    service_modes: list[
        Literal["controlled_compute", "model_artifact_license"]
    ] = Field(default_factory=lambda: ["controlled_compute"], min_length=1, max_length=2)
    allowed_purposes: list[str] = Field(min_length=1, max_length=12)
    prohibited_purposes: list[str] = Field(min_length=1, max_length=12)
    max_runs: int = Field(ge=1, le=10000)
    valid_days: int = Field(ge=1, le=3650)
    multi_center_validation: bool = False
    commercial_validation: bool = False
    research_publication: bool = True
    provider_result_confirmation: bool = True
    model_download: bool = False
    reverse_engineering: bool = False
    redistribution: bool = False
    dynamic_script_execution: bool = False
    unauthorized_network: bool = False

    @field_validator("service_modes")
    @classmethod
    def require_unique_service_modes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("service modes must be unique")
        return value


class ModelDraftRequest(BaseModel):
    basic: ModelBasicInformation
    runtime: ModelRuntimeInformation
    schema_information: ModelSchemaInformation = Field(alias="schema")
    policy: ModelPolicyInformation


class ModelReviewRequest(BaseModel):
    review_opinion: str = Field(min_length=5, max_length=1000)
    technical_risk: Literal["low", "medium", "high"]
    license_risk: Literal["low", "medium", "high"]
    additional_conditions: str = Field(default="", max_length=1000)
    requested_materials: str = Field(default="", max_length=1000)
    allow_catalog: bool = False


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Model-product command API is disabled")


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    return value.strip()


async def _actor(
    session: AsyncSession, identity: str, expected: str | None = None
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


def _registry():
    return load_pathmnist_model_registry(WORKSPACE)


def _asset_payload(entry) -> dict[str, Any]:
    return {
        "asset_id": entry.model_digest,
        "name": entry.model_name,
        "version": entry.model_version,
        "model_digest": entry.model_digest,
        "registry_digest": entry.registration_digest,
        "entrypoint_id": entry.entrypoint_id,
        "runtime": entry.runtime,
        "input_schema_version": entry.input_schema_version,
        "output_schema_version": entry.output_schema_version,
        "network_access": entry.network_access,
        "cpu_limit": entry.cpu_limit,
        "memory_limit_mb": entry.memory_limit,
        "timeout_seconds": entry.timeout_seconds,
        "executor_type": "local_builtin",
        "runtime_status": "ready",
        "model_ready": entry.enabled,
        "allowed_output_files": list(entry.allowed_output_files),
    }


async def _detail(
    session: AsyncSession,
    *,
    product: ModelProduct,
    version: ModelVersion,
    identity: str,
) -> dict[str, Any]:
    provider = await session.get(Organization, product.provider_organization_id)
    external_link = await session.scalar(
        select(ModelProductExternalSourceLink).where(
            ModelProductExternalSourceLink.model_version_id == version.id
        )
    )
    external_record = (
        None
        if external_link is None
        else await session.get(
            ExternalModelRecord, external_link.external_model_record_id
        )
    )
    external_reviews: dict[str, ExternalModelGovernanceReview] = {}
    if external_link is not None:
        for dimension, review_id in external_link.review_ids.items():
            review = await session.get(ExternalModelGovernanceReview, UUID(review_id))
            if review is not None:
                external_reviews[dimension] = review
    publication = await session.scalar(
        select(ModelPublication)
        .where(ModelPublication.model_version_id == version.id)
        .order_by(ModelPublication.published_at.desc())
        .limit(1)
    )
    latest_return = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.subject_type == "model_version",
            AuditEvent.subject_id == version.id,
            AuditEvent.event_type == "model_product.version.returned",
        )
        .order_by(AuditEvent.stream_sequence.desc())
        .limit(1)
    )
    latest_submit = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.subject_type == "model_version",
            AuditEvent.subject_id == version.id,
            AuditEvent.event_type == "model_product.version.submitted",
        )
        .order_by(AuditEvent.stream_sequence.desc())
        .limit(1)
    )
    current_lifecycle = await session.scalar(
        select(ProductLifecycleRequest)
        .where(
            ProductLifecycleRequest.target_type == "model_product",
            ProductLifecycleRequest.target_product_id == product.id,
            ProductLifecycleRequest.status == "pending",
        )
        .order_by(ProductLifecycleRequest.requested_at.desc())
        .limit(1)
    )
    actions: list[str] = []
    if external_link is not None:
        actions = []
    elif identity == "model_provider" and version.status == "draft":
        actions = ["edit", "submit"]
    elif identity == "space_operator" and version.status == "under_review":
        actions = ["approve", "return"]
    if identity == "model_provider" and current_lifecycle is None:
        if product.lifecycle_status == "active":
            actions.extend(["request_unpublish"])
        elif product.lifecycle_status == "unpublished":
            actions.extend(["request_relist", "request_archive"])
    publication_active = publication is not None and publication.status == "active"
    public_state = "published" if publication_active else (
        product.lifecycle_status if product.lifecycle_status in {"unpublished", "archived"} else version.status
    )
    return {
        "product_id": str(product.id),
        "version_id": str(version.id),
        "product_code": product.product_code,
        "name": product.name,
        "description": product.description,
        "domain": product.domain,
        "provider": provider.display_name if provider else "",
        "provider_label": (
            "catalog_steward" if external_link is not None else "model_provider"
        ),
        "provider_organization_id": str(product.provider_organization_id),
        "status": public_state,
        "version_status": version.status,
        "version_label": version.version_label,
        "entrypoint_id": version.entrypoint_id,
        "model_digest": version.model_digest,
        "manifest_digest": version.manifest_digest,
        "registry_digest": version.registry_digest,
        "runtime": version.runtime,
        "input_schema_version": version.input_schema_version,
        "output_schema_version": version.output_schema_version,
        "compatibility": version.compatibility_metadata,
        "license": version.license_metadata,
        "policy": version.default_policy_template,
        "offerings": build_service_offerings(
            "model",
            version.default_policy_template,
            controlled_compute_requestable=external_link is None,
            authorization_requestable=(
                publication_active
                and version.status == "approved"
                and product.lifecycle_status == "active"
            ),
            external=external_link is not None,
        ),
        "snapshot_digest": version.snapshot_digest,
        "created_at": _iso(version.created_at),
        "updated_at": _iso(product.updated_at),
        "submitted_at": _iso(latest_submit.occurred_at) if latest_submit else None,
        "approved_at": _iso(version.approved_at),
        "published_at": _iso(publication.published_at) if publication else None,
        "unpublished_at": _iso(product.unpublished_at),
        "deleted_at": _iso(product.deleted_at),
        "publication_id": str(publication.id) if publication else None,
        "latest_return": None if latest_return is None else {
            "event_id": str(latest_return.event_id),
            "review_opinion": latest_return.evidence_snapshot.get("review_opinion"),
            "requested_materials": latest_return.evidence_snapshot.get("requested_materials"),
            "technical_risk": latest_return.evidence_snapshot.get("technical_risk"),
            "license_risk": latest_return.evidence_snapshot.get("license_risk"),
            "occurred_at": _iso(latest_return.occurred_at),
        },
        "allowed_actions": actions,
        "external_source": (
            None
            if external_link is None or external_record is None
            else {
                "source_kind": "external_public_model",
                "external_record_id": str(external_record.id),
                "external_model_id": external_record.external_model_id,
                "catalog_version": external_link.catalog_version,
                "source_record_digest": external_link.source_record_digest,
                "governance_snapshot_digest": (
                    external_link.governance_snapshot_digest
                ),
                "review_count": len(external_link.review_ids),
                "reviews": {
                    dimension: {
                        "decision": review.decision,
                        "decision_payload": review.decision_payload,
                        "evidence_reference": review.evidence_reference,
                    }
                    for dimension, review in external_reviews.items()
                },
                "upstream_provider": external_link.upstream_provider,
                "upstream_official_url": external_link.upstream_official_url,
                "materialization_status": external_link.materialization_status,
                "weight_holder_status": external_link.weight_holder_status,
                "executor_registered": False,
                "execution_readiness": external_link.execution_readiness,
                "platform_validation": external_link.platform_validation,
                "application_eligibility": False,
                "compute_eligibility": False,
                "data_compatibility_assessed": False,
            }
        ),
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
            "model_download": False,
            "clinical_use": False,
            "arbitrary_code": False,
        },
    }


async def _for_access(
    session: AsyncSession, *, version_id: UUID, identity: str, actor: DemoActor
) -> tuple[ModelProduct, ModelVersion]:
    version = await session.get(ModelVersion, version_id)
    product = None if version is None else await session.get(ModelProduct, version.model_product_id)
    if version is None or product is None:
        raise HTTPException(status_code=404, detail="Model product version not found")
    if identity == "model_provider" and product.provider_organization_id != actor.organization_id:
        raise HTTPException(status_code=403, detail="Model provider cannot access another provider draft")
    if identity in {"data_requester", "data_provider"}:
        publication = await session.scalar(
            select(ModelPublication.id).where(
                ModelPublication.model_version_id == version.id,
                ModelPublication.status == "active",
            )
        )
        if publication is None:
            raise HTTPException(status_code=404, detail="Published model product not found")
    return product, version


@router.get("/model-assets")
async def model_assets(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    await _actor(session, identity, "model_provider")
    registry = _registry()
    entry = registry.require_enabled(
        "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
    )
    return {"items": [_asset_payload(entry)]}


@router.post("/model-products", status_code=status.HTTP_201_CREATED)
async def create_model_product(
    payload: ModelDraftRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, "model_provider")
            product, version, event = await create_model_draft(
                session,
                space_id=context.space_id,
                actor=actor,
                document=payload.model_dump(mode="json", by_alias=True),
                registry=_registry(),
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
    except ModelLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/model-product-versions/{version_id}")
async def update_model_product(
    version_id: UUID,
    payload: ModelDraftRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "model_provider")
            version = await session.get(ModelVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Model product version not found")
            product, event = await update_model_draft(
                session,
                version=version,
                actor=actor,
                document=payload.model_dump(mode="json", by_alias=True),
                registry=_registry(),
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
    except ModelLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/model-product-versions/{version_id}/submit")
async def submit_model_product(
    version_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "model_provider")
            version = await session.get(ModelVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Model product version not found")
            event = await submit_model_draft(
                session,
                version=version,
                actor=actor,
                registry=_registry(),
                raw_key=_key(idempotency_key),
            )
        return {"version_id": str(version.id), "status": version.status, "event_id": str(event.event_id)}
    except HTTPException:
        raise
    except ModelLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/model-product-versions/{version_id}/return")
async def return_model_product(
    version_id: UUID,
    payload: ModelReviewRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            version = await session.get(ModelVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Model product version not found")
            event = await return_model_draft(
                session,
                version=version,
                actor=actor,
                review=payload.model_dump(mode="json"),
                raw_key=_key(idempotency_key),
            )
        return {"version_id": str(version.id), "status": version.status, "event_id": str(event.event_id)}
    except HTTPException:
        raise
    except ModelLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/model-product-versions/{version_id}/approve")
async def approve_model_product(
    version_id: UUID,
    payload: ModelReviewRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            version = await session.get(ModelVersion, version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Model product version not found")
            publication, approved, published = await approve_and_publish_model(
                session,
                version=version,
                actor=actor,
                registry=_registry(),
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
    except ModelLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/model-product-management")
async def model_product_management(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    query = (
        select(ModelProduct, ModelVersion)
        .join(ModelVersion, ModelVersion.model_product_id == ModelProduct.id)
        .where(ModelProduct.space_id == context.space_id)
        .order_by(ModelProduct.created_at.desc(), ModelVersion.version_no.desc())
    )
    if identity == "model_provider":
        query = query.where(ModelProduct.provider_organization_id == actor.organization_id)
    elif identity in {"data_requester", "data_provider"}:
        query = query.join(
            ModelPublication, ModelPublication.model_version_id == ModelVersion.id
        ).where(ModelPublication.status == "active")
    rows = (await session.execute(query)).all()
    items = [
        await _detail(session, product=product, version=version, identity=identity)
        for product, version in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/model-product-catalog")
async def model_product_catalog(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    rows = (
        await session.execute(
            select(
                ModelProduct,
                ModelVersion,
                Organization,
                ModelProductExternalSourceLink,
                ExternalModelRecord,
            )
            .join(ModelVersion, ModelVersion.model_product_id == ModelProduct.id)
            .join(ModelPublication, ModelPublication.model_version_id == ModelVersion.id)
            .join(Organization, Organization.id == ModelProduct.provider_organization_id)
            .outerjoin(
                ModelProductExternalSourceLink,
                ModelProductExternalSourceLink.model_version_id == ModelVersion.id,
            )
            .outerjoin(
                ExternalModelRecord,
                ExternalModelRecord.id
                == ModelProductExternalSourceLink.external_model_record_id,
            )
            .where(
                ModelProduct.space_id == context.space_id,
                ModelPublication.status == "active",
            )
            .order_by(ModelPublication.published_at.desc())
        )
    ).all()
    items = [
        {
            "product_id": str(product.id),
            "version_id": str(version.id),
            "product_code": product.product_code,
            "name": product.name,
            "provider": provider.display_name,
            "description": product.description,
            "disease_domain": product.domain,
            "task_type": version.compatibility_metadata.get("task_type"),
            "modality": version.compatibility_metadata.get("modality"),
            "input_summary": version.compatibility_metadata.get("input_schema"),
            "output_summary": version.compatibility_metadata.get("output_schema"),
            "version": version.version_label,
            "license_summary": {
                "allowed_purposes": version.license_metadata.get("allowed_purposes", []),
                "model_download": False,
                "redistribution": False,
            },
            "is_demo": product.is_demo,
            "non_clinical": True,
            "source_kind": (
                "external_public_model" if source_link is not None else "internal"
            ),
            "catalog_steward": provider.display_name,
            "upstream_provider": (
                record.upstream_provider if record is not None else None
            ),
            "materialization_status": (
                source_link.materialization_status
                if source_link is not None
                else "materialized"
            ),
            "executor_registered": source_link is None,
            "execution_readiness": (
                source_link.execution_readiness
                if source_link is not None
                else "ready"
            ),
            "platform_validation": (
                source_link.platform_validation
                if source_link is not None
                else "validated"
            ),
            "application_eligibility": source_link is None,
            "compute_eligibility": source_link is None,
            "offerings": build_service_offerings(
                "model",
                version.default_policy_template,
                controlled_compute_requestable=source_link is None,
                authorization_requestable=True,
                external=source_link is not None,
            ),
        }
        for product, version, provider, source_link, record in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/model-product-review-queue")
async def model_product_review_queue(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity, "space_operator")
    rows = (
        await session.execute(
            select(ModelProduct, ModelVersion)
            .join(ModelVersion, ModelVersion.model_product_id == ModelProduct.id)
            .where(
                ModelProduct.space_id == context.space_id,
                ModelVersion.status == "under_review",
            )
            .order_by(ModelVersion.created_at)
        )
    ).all()
    return {"items": [
        await _detail(session, product=product, version=version, identity=identity)
        for product, version in rows
    ]}


@router.get("/model-product-versions/{version_id}")
async def model_product_detail(
    version_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    product, version = await _for_access(
        session, version_id=version_id, identity=identity, actor=actor
    )
    return await _detail(session, product=product, version=version, identity=identity)


@router.get("/model-product-versions/{version_id}/audit-events")
async def model_product_audit_events(
    version_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    await _for_access(session, version_id=version_id, identity=identity, actor=actor)
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.space_id == context.space_id,
                    AuditEvent.subject_type == "model_version",
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
        organization = None if event.actor_organization_id is None else await session.get(Organization, event.actor_organization_id)
        user = None if event.actor_user_id is None else await session.get(User, event.actor_user_id)
        outbox = list(
            (
                await session.scalars(
                    select(OutboxMessage).where(OutboxMessage.audit_event_id == event.event_id)
                )
            ).all()
        )
        items.append({
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
            "outbox": [
                {
                    "message_id": str(message.message_id),
                    "destination": message.destination,
                    "status": message.status,
                }
                for message in outbox
            ],
        })
    return {
        "items": items,
        "audit_chain_valid": bool(chain.is_valid),
        "total": int(
            await session.scalar(
                select(func.count(AuditEvent.event_id)).where(
                    AuditEvent.subject_type == "model_version",
                    AuditEvent.subject_id == version_id,
                )
            )
            or 0
        ),
    }
