from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.lifecycle.models import ProductLifecycleRequest
from app.modules.lifecycle.services import (
    LifecycleGovernanceError,
    cancel_lifecycle_request,
    create_lifecycle_request,
    decide_lifecycle_request,
)
from app.modules.marketplace.services import MarketplaceServiceError, require_actor

router = APIRouter(tags=["product-lifecycle-governance"])


class LifecycleRequestPayload(BaseModel):
    action: Literal["unpublish", "relist", "archive"]
    reason: str = Field(min_length=5, max_length=2000)
    requested_effective_at: str | None = None
    unpublish_type: str | None = Field(default=None, max_length=80)
    existing_cooperation_note: str | None = Field(default=None, max_length=1000)
    safety_or_quality_issue: bool = False
    contact_note: str | None = Field(default=None, max_length=500)
    content_changed: bool = False


class LifecycleDecisionPayload(BaseModel):
    decision: Literal["approved", "rejected", "returned"]
    comment: str = Field(min_length=3, max_length=2000)


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Lifecycle command API is disabled")


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    return value.strip()


async def _actor(
    session: AsyncSession, identity: str, expected: str | None = None
) -> tuple[Any, DemoActor]:
    if expected is not None and identity != expected:
        raise HTTPException(status_code=403, detail="当前账号无权执行该操作")
    context = await get_phase4_context(session)
    actor = context.actors.get(identity)
    if actor is None:
        raise HTTPException(status_code=403, detail="未知账号")
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


def _payload(row: ProductLifecycleRequest) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "target_type": row.target_type,
        "target_product_id": str(row.target_product_id),
        "target_version_id": str(row.target_version_id) if row.target_version_id else None,
        "action": row.action,
        "reason": row.reason,
        "details": row.details,
        "status": row.status,
        "impact": row.impact_snapshot,
        "impact_digest": row.impact_digest,
        "requested_at": row.requested_at.isoformat(),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_comment": row.review_comment,
        "decision": row.decision,
    }


async def _create(
    *,
    target_type: str,
    product_id: UUID,
    payload: LifecycleRequestPayload,
    request: Request,
    identity: str,
    idempotency_key: str | None,
    session: AsyncSession,
) -> dict[str, Any]:
    _enabled(request)
    expected = "data_provider" if target_type == "data_product" else "model_provider"
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, expected)
            row = await create_lifecycle_request(
                session,
                space_id=context.space_id,
                target_type=target_type,
                product_id=product_id,
                action=payload.action,
                actor=actor,
                reason=payload.reason,
                details=payload.model_dump(exclude={"action", "reason"}, exclude_none=True),
                raw_key=_key(idempotency_key),
            )
        return _payload(row)
    except LifecycleGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/data-products/{product_id}/lifecycle-requests")
async def create_data_lifecycle_request(
    product_id: UUID,
    payload: LifecycleRequestPayload,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    return await _create(
        target_type="data_product",
        product_id=product_id,
        payload=payload,
        request=request,
        identity=identity,
        idempotency_key=idempotency_key,
        session=session,
    )


@router.post("/model-products/{product_id}/lifecycle-requests")
async def create_model_lifecycle_request(
    product_id: UUID,
    payload: LifecycleRequestPayload,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    return await _create(
        target_type="model_product",
        product_id=product_id,
        payload=payload,
        request=request,
        identity=identity,
        idempotency_key=idempotency_key,
        session=session,
    )


@router.get("/product-lifecycle-requests")
async def lifecycle_requests(
    identity: str = Header(alias="X-Demo-Identity"),
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    query = select(ProductLifecycleRequest).where(
        ProductLifecycleRequest.space_id == context.space_id
    )
    if identity != "space_operator":
        query = query.where(
            ProductLifecycleRequest.requested_by_organization_id == actor.organization_id
        )
    if status_filter:
        query = query.where(ProductLifecycleRequest.status == status_filter)
    rows = list(
        (await session.scalars(query.order_by(ProductLifecycleRequest.requested_at.desc()))).all()
    )
    return {"items": [_payload(row) for row in rows]}


@router.get("/product-lifecycle-requests/{request_id}")
async def lifecycle_request_detail(
    request_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    row = await session.get(ProductLifecycleRequest, request_id)
    if row is None or (
        identity != "space_operator"
        and row.requested_by_organization_id != actor.organization_id
    ):
        raise HTTPException(status_code=404, detail="Lifecycle request not found")
    return _payload(row)


@router.post("/product-lifecycle-requests/{request_id}/decision")
async def decide_request(
    request_id: UUID,
    payload: LifecycleDecisionPayload,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            row = await decide_lifecycle_request(
                session,
                request_id=request_id,
                actor=actor,
                decision=payload.decision,
                comment=payload.comment,
                raw_key=_key(idempotency_key),
            )
        return _payload(row)
    except LifecycleGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/product-lifecycle-requests/{request_id}/cancel")
async def cancel_request(
    request_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity)
            row = await cancel_lifecycle_request(
                session,
                request_id=request_id,
                actor=actor,
                raw_key=_key(idempotency_key),
            )
        return _payload(row)
    except LifecycleGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
