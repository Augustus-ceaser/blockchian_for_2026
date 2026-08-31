from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.audit import AuditInvariantError, IdempotencyConflict
from app.modules.commerce.models import CommercialOrder
from app.modules.commerce.services import (
    CommerceError,
    accept_commercial_agreement,
    commercial_offer_payload_for_role,
    commercial_offers_for_version,
    commercial_order_payload,
    consume_commercial_download,
    create_commercial_download_grant,
    create_order_from_contract,
    create_order_from_service_access,
    list_orders_for_actor,
    pay_commercial_order,
    provider_settlements,
)
from app.modules.marketplace.services import MarketplaceServiceError, require_actor


router = APIRouter(tags=["commerce"])
DEMO_ROLES = {"space_operator", "data_provider", "model_provider", "data_requester"}


class DemoPaymentRequest(BaseModel):
    method: Literal["wechat_demo", "alipay_demo", "bank_card_demo"]


class DownloadRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Commercial demo API is disabled")


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


def _commerce_http_error(exc: Exception) -> HTTPException:
    detail = str(exc)
    lowered = detail.lower()
    denied = any(
        marker in lowered
        for marker in (
            "not authorized",
            "only the",
            "another requester",
            "another organization",
            "outside this provider",
            "belongs to another",
        )
    )
    return HTTPException(status_code=403 if denied else 409, detail=detail)


@router.get("/commercial-offers/version/{product_kind}/{version_id}")
async def get_commercial_offers(
    product_kind: Literal["data", "model"],
    version_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        context, actor = await _actor(session, identity)
        items = await commercial_offers_for_version(
            session,
            space_id=context.space_id,
            product_kind=product_kind,
            version_id=version_id,
        )
        projected = [
            commercial_offer_payload_for_role(item, role=actor.role)
            for item in items
        ]
        return {"items": projected, "total": len(projected)}
    except HTTPException:
        raise
    except (CommerceError, MarketplaceServiceError, ValueError) as exc:
        raise _commerce_http_error(exc) from exc


@router.post(
    "/commercial-orders/from-service-access/{request_id}",
    status_code=status.HTTP_201_CREATED,
)
async def checkout_service_access(
    request_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            order, event = await create_order_from_service_access(
                session,
                request_id=request_id,
                actor=actor,
                raw_key=_key(idempotency_key),
            )
        result = await commercial_order_payload(session, order=order, actor=actor)
        result["event_id"] = str(event.event_id)
        return result
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        CommerceError,
        ValueError,
    ) as exc:
        raise _commerce_http_error(exc) from exc


@router.post(
    "/commercial-orders/from-contract/{contract_id}",
    status_code=status.HTTP_201_CREATED,
)
async def checkout_contract(
    contract_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            order, event = await create_order_from_contract(
                session,
                contract_id=contract_id,
                actor=actor,
                raw_key=_key(idempotency_key),
            )
        result = await commercial_order_payload(session, order=order, actor=actor)
        result["event_id"] = str(event.event_id)
        return result
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        CommerceError,
        ValueError,
    ) as exc:
        raise _commerce_http_error(exc) from exc


@router.get("/commercial-orders")
async def list_commercial_orders(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        context, actor = await _actor(session, identity)
        rows = await list_orders_for_actor(
            session, space_id=context.space_id, actor=actor
        )
        items = [
            await commercial_order_payload(session, order=row, actor=actor)
            for row in rows
        ]
        return {"items": items, "total": len(items)}
    except HTTPException:
        raise
    except (MarketplaceServiceError, CommerceError, ValueError) as exc:
        raise _commerce_http_error(exc) from exc


@router.get("/commercial-orders/{order_id}")
async def get_commercial_order(
    order_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        context, actor = await _actor(session, identity)
        order = await session.scalar(
            select(CommercialOrder).where(
                CommercialOrder.id == order_id,
                CommercialOrder.space_id == context.space_id,
            )
        )
        if order is None:
            raise HTTPException(status_code=404, detail="Commercial order not found")
        return await commercial_order_payload(session, order=order, actor=actor)
    except HTTPException:
        raise
    except (MarketplaceServiceError, CommerceError, ValueError) as exc:
        raise _commerce_http_error(exc) from exc


@router.post("/commercial-orders/{order_id}/accept-agreement")
async def accept_agreement(
    order_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            order, event = await accept_commercial_agreement(
                session,
                order_id=order_id,
                actor=actor,
                raw_key=_key(idempotency_key),
            )
        result = await commercial_order_payload(session, order=order, actor=actor)
        result["event_id"] = str(event.event_id)
        return result
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        CommerceError,
        ValueError,
    ) as exc:
        raise _commerce_http_error(exc) from exc


@router.post("/commercial-orders/{order_id}/pay")
async def pay_order(
    order_id: UUID,
    payload: DemoPaymentRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            order, payment, fulfillment, event = await pay_commercial_order(
                session,
                order_id=order_id,
                actor=actor,
                method=payload.method,
                raw_key=_key(idempotency_key),
            )
        result = await commercial_order_payload(session, order=order, actor=actor)
        result.update(
            {
                "event_id": str(event.event_id),
                "payment_id": str(payment.id),
                "fulfillment_id": str(fulfillment.id),
            }
        )
        return result
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        CommerceError,
        ValueError,
    ) as exc:
        raise _commerce_http_error(exc) from exc


@router.post("/commercial-orders/{order_id}/download-grants")
async def create_download_grant(
    order_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            grant, token, event = await create_commercial_download_grant(
                session,
                order_id=order_id,
                actor=actor,
                raw_key=_key(idempotency_key),
            )
        return {
            "grant_id": str(grant.id),
            "token": token,
            "filename": grant.filename,
            "status": grant.status,
            "expires_at": grant.expires_at.isoformat(),
            "max_downloads": grant.max_downloads,
            "download_count": grant.download_count,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        CommerceError,
        ValueError,
    ) as exc:
        raise _commerce_http_error(exc) from exc


@router.post("/commercial-downloads")
async def download_commercial_package(
    payload: DownloadRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            filename, package_bytes, grant, event = await consume_commercial_download(
                session,
                token=payload.token,
                actor=actor,
                raw_key=_key(idempotency_key),
            )
        return Response(
            content=package_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-MedTrust-Grant-Id": str(grant.id),
                "X-MedTrust-Audit-Event-Id": str(event.event_id),
                "Cache-Control": "no-store",
            },
        )
    except HTTPException:
        raise
    except (
        AuditInvariantError,
        IdempotencyConflict,
        MarketplaceServiceError,
        CommerceError,
        ValueError,
    ) as exc:
        raise _commerce_http_error(exc) from exc


@router.get("/commercial-provider-settlements")
async def get_provider_settlements(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        context, actor = await _actor(session, identity)
        return await provider_settlements(
            session, space_id=context.space_id, actor=actor
        )
    except HTTPException:
        raise
    except (MarketplaceServiceError, CommerceError, ValueError) as exc:
        raise _commerce_http_error(exc) from exc
