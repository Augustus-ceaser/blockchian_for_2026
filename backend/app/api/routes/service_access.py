from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.audit import AuditInvariantError, IdempotencyConflict
from app.modules.identity.models import Organization
from app.modules.marketplace.services import MarketplaceServiceError, require_actor
from app.modules.service_access.models import ServiceAccessRequest
from app.modules.service_access.services import (
    ServiceAccessError,
    create_service_access_request,
    decide_service_access_by_operator,
    decide_service_access_by_provider,
)


router = APIRouter(tags=["service-access"])
DEMO_ROLES = {"space_operator", "data_provider", "model_provider", "data_requester"}


class ServiceAccessCreateRequest(BaseModel):
    product_kind: Literal["data", "model"]
    version_id: UUID
    service_mode: Literal[
        "deidentified_data_delivery", "model_artifact_license"
    ]
    purpose: str = Field(min_length=4, max_length=500)
    intended_use: str = Field(min_length=10, max_length=2000)
    requested_duration_days: int = Field(ge=1, le=3650)

    @field_validator("purpose", "intended_use", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ServiceAccessDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    summary: str = Field(min_length=5, max_length=1000)

    @field_validator("summary", mode="before")
    @classmethod
    def strip_summary(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(
            status_code=403, detail="Service access command API is disabled"
        )


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    return value.strip()


async def _actor(
    session: AsyncSession,
    identity: str,
    expected: str | None = None,
) -> tuple[object, DemoActor]:
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


def _next_step(access_request: ServiceAccessRequest) -> str:
    return {
        "submitted": "provider_review",
        "provider_approved": "operator_review",
        "approved_pending_contract": "contract_and_fulfillment_required",
        "rejected": "closed_rejected",
    }[access_request.status]


async def _payload(
    session: AsyncSession,
    access_request: ServiceAccessRequest,
    *,
    identity: str,
    actor: DemoActor,
) -> dict[str, object]:
    requester = await session.get(
        Organization, access_request.requester_organization_id
    )
    provider = await session.get(
        Organization, access_request.provider_organization_id
    )
    allowed_actions: list[str] = []
    expected_provider_role = (
        "data_provider"
        if access_request.product_kind == "data"
        else "model_provider"
    )
    if (
        identity == expected_provider_role
        and actor.organization_id == access_request.provider_organization_id
        and access_request.status == "submitted"
    ):
        allowed_actions.append("provider_decide")
    if identity == "space_operator" and access_request.status == "provider_approved":
        allowed_actions.append("operator_decide")
    product_snapshot = dict(access_request.product_snapshot)
    return {
        "request_id": str(access_request.id),
        "id": str(access_request.id),
        "request_number": access_request.request_number,
        "space_id": str(access_request.space_id),
        "product_kind": access_request.product_kind,
        "product_id": str(access_request.product_id),
        "version_id": str(access_request.version_id),
        "service_mode": access_request.service_mode,
        "status": access_request.status,
        "purpose": access_request.purpose,
        "intended_use": access_request.intended_use,
        "requested_duration_days": access_request.requested_duration_days,
        "requester_organization_id": str(
            access_request.requester_organization_id
        ),
        "provider_organization_id": str(access_request.provider_organization_id),
        "requester": {
            "organization_id": str(access_request.requester_organization_id),
            "name": requester.display_name if requester is not None else "",
        },
        "provider": {
            "organization_id": str(access_request.provider_organization_id),
            "name": provider.display_name if provider is not None else "",
        },
        "product": {
            "product_id": str(access_request.product_id),
            "version_id": str(access_request.version_id),
            "code": product_snapshot.get("product_code"),
            "name": product_snapshot.get("name"),
            "version": product_snapshot.get("version_label"),
            "version_label": product_snapshot.get("version_label"),
        },
        "product_snapshot": product_snapshot,
        "product_snapshot_digest": access_request.product_snapshot_digest,
        "request_digest": access_request.request_digest,
        "requested_at": access_request.requested_at.isoformat(),
        "created_at": access_request.requested_at.isoformat(),
        "updated_at": access_request.updated_at.isoformat(),
        "provider_decision": (
            {
                "decision": access_request.provider_decision,
                "summary": access_request.provider_decision_summary,
                "decided_by": (
                    str(access_request.provider_decided_by)
                    if access_request.provider_decided_by is not None
                    else None
                ),
                "decided_at": (
                    access_request.provider_decided_at.isoformat()
                    if access_request.provider_decided_at is not None
                    else None
                ),
            }
            if access_request.provider_decision is not None
            else None
        ),
        "operator_decision": (
            {
                "decision": access_request.operator_decision,
                "summary": access_request.operator_decision_summary,
                "decided_by": (
                    str(access_request.operator_decided_by)
                    if access_request.operator_decided_by is not None
                    else None
                ),
                "decided_at": (
                    access_request.operator_decided_at.isoformat()
                    if access_request.operator_decided_at is not None
                    else None
                ),
            }
            if access_request.operator_decision is not None
            else None
        ),
        "next_step": _next_step(access_request),
        "allowed_actions": allowed_actions,
        "contract_created": False,
        "fulfillment_created": False,
        "download_available": False,
    }


@router.get("/service-access-requests")
async def list_service_access_requests(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    query = (
        select(ServiceAccessRequest)
        .where(ServiceAccessRequest.space_id == context.space_id)
        .order_by(ServiceAccessRequest.requested_at.desc())
    )
    if identity == "data_requester":
        query = query.where(
            ServiceAccessRequest.requester_organization_id == actor.organization_id
        )
    elif identity == "data_provider":
        query = query.where(
            ServiceAccessRequest.provider_organization_id == actor.organization_id,
            ServiceAccessRequest.product_kind == "data",
        )
    elif identity == "model_provider":
        query = query.where(
            ServiceAccessRequest.provider_organization_id == actor.organization_id,
            ServiceAccessRequest.product_kind == "model",
        )
    rows = list((await session.scalars(query)).all())
    items = [
        await _payload(session, row, identity=identity, actor=actor) for row in rows
    ]
    return {"items": items, "total": len(items)}


@router.post(
    "/service-access-requests", status_code=status.HTTP_201_CREATED
)
async def create_service_access(
    payload: ServiceAccessCreateRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, "data_requester")
            access_request, event = await create_service_access_request(
                session,
                space_id=context.space_id,
                actor=actor,
                product_kind=payload.product_kind,
                version_id=payload.version_id,
                service_mode=payload.service_mode,
                purpose=payload.purpose,
                intended_use=payload.intended_use,
                requested_duration_days=payload.requested_duration_days,
                raw_key=_key(idempotency_key),
            )
        result = await _payload(
            session, access_request, identity=identity, actor=actor
        )
        result["event_id"] = str(event.event_id)
        return result
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        ServiceAccessError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/service-access-requests/{request_id}/provider-decision")
async def provider_decision(
    request_id: UUID,
    payload: ServiceAccessDecisionRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity)
            access_request, event = await decide_service_access_by_provider(
                session,
                request_id=request_id,
                actor=actor,
                decision=payload.decision,
                summary=payload.summary,
                raw_key=_key(idempotency_key),
            )
        result = await _payload(
            session, access_request, identity=identity, actor=actor
        )
        result["event_id"] = str(event.event_id)
        return result
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        ServiceAccessError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/service-access-requests/{request_id}/operator-decision")
async def operator_decision(
    request_id: UUID,
    payload: ServiceAccessDecisionRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            access_request, event = await decide_service_access_by_operator(
                session,
                request_id=request_id,
                actor=actor,
                decision=payload.decision,
                summary=payload.summary,
                raw_key=_key(idempotency_key),
            )
        result = await _payload(
            session, access_request, identity=identity, actor=actor
        )
        result["event_id"] = str(event.event_id)
        return result
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        ServiceAccessError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
