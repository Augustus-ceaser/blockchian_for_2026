from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
from typing import Any, Literal, Mapping, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.applications.models import Application, ApplicationItem
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    begin_audited_command,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
)
from app.modules.contracts.models import Contract, ContractRevision
from app.modules.contracts.security import validate_contract_security
from app.modules.identity.models import Organization
# Load the compute package first so its service imports finish registering the
# marketplace models before this module imports those models directly.
from app.modules.compute import models as _compute_models  # noqa: F401
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.service_modes import resolve_service_modes
from app.modules.service_access.models import ServiceAccessRequest

from .models import (
    CommercialDownloadGrant,
    CommercialFulfillment,
    CommercialOrder,
    CommercialOrderLine,
    DemoPayment,
)
from .packages import automatic_delivery_profile, build_delivery_zip
from .pricing import (
    CHANNEL_FEE_RATE_BPS,
    CURRENCY,
    PLATFORM_FEE_RATE_BPS,
    channel_fee_for,
    demo_price_plan_eligible,
    resolve_offer_snapshot,
    split_gross_amount,
)


ProductKind = Literal["data", "model"]


class CommerceError(ValueError):
    pass


def require_contract_checkout_security(decision: Mapping[str, Any]) -> None:
    if decision.get("overall") != "PASS":
        raise CommerceError("安全合约验证未全部通过，不能创建受控计算结算订单")


class CommerceActor(Protocol):
    role: str
    organization_id: UUID
    user_id: UUID


@dataclass(frozen=True)
class ProductOfferContext:
    product_kind: ProductKind
    product_id: UUID
    version_id: UUID
    product_name: str
    product_code: str
    provider_organization_id: UUID
    policy: Mapping[str, Any]
    is_demo: bool
    service_modes: tuple[str, ...]


_INTERNAL_OFFER_FIELDS = {
    "channel_fee_rate_bps",
    "includes_platform_fee",
    "platform_fee_rate_bps",
    "pricing_plan_version",
    "pricing_source",
    "provider_share_rate_bps",
    "revenue_basis",
}


def commercial_offer_payload_for_role(
    offer: Mapping[str, Any], *, role: str
) -> dict[str, Any]:
    """Project an offer without exposing platform economics to market actors."""

    if role == "space_operator":
        return dict(offer)
    return {
        key: value
        for key, value in offer.items()
        if key not in _INTERNAL_OFFER_FIELDS
    }


def _agreement_payload_for_role(
    snapshot: Mapping[str, Any] | None, *, role: str
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    projected = dict(snapshot)
    if role != "space_operator":
        projected.pop("platform_fee_included", None)
    return projected


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _command(
    actor: CommerceActor,
    *,
    space_id: UUID,
    subject_id: UUID,
    action: str,
    raw_key: str,
) -> AuditCommandContext:
    scope = (
        f"medtrust:commerce:{space_id}:{actor.user_id}:"
        f"{subject_id}:{action}:{raw_key}"
    )
    return AuditCommandContext(
        command_id=uuid5(NAMESPACE_URL, f"{scope}:command"),
        idempotency_key=digest_idempotency_key(scope),
        correlation_id=uuid5(
            NAMESPACE_URL, f"medtrust:commerce:{space_id}:{subject_id}:correlation"
        ),
        actor_type="user",
        actor_organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )


async def _require_actor(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: CommerceActor,
    roles: set[str],
) -> None:
    if actor.role not in roles:
        raise CommerceError("this role is not authorized for the commercial action")
    from app.modules.marketplace.services import require_actor

    await require_actor(
        session,
        space_id=space_id,
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        role_code=actor.role,
    )


async def _offer_context(
    session: AsyncSession,
    *,
    space_id: UUID,
    product_kind: str,
    version_id: UUID,
) -> ProductOfferContext:
    if product_kind == "data":
        row = (
            await session.execute(
                select(DataProduct, DataProductVersion, DataProductPublication)
                .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
                .join(
                    DataProductPublication,
                    (DataProductPublication.data_product_id == DataProduct.id)
                    & (DataProductPublication.data_product_version_id == DataProductVersion.id),
                )
                .where(
                    DataProduct.space_id == space_id,
                    DataProduct.lifecycle_status == "active",
                    DataProductVersion.id == version_id,
                    DataProductVersion.status == "approved",
                    DataProductPublication.status == "active",
                )
            )
        ).first()
        if row is None:
            raise CommerceError("data product version is not an active publication")
        product, version, _publication = row
        kind: ProductKind = "data"
    elif product_kind == "model":
        row = (
            await session.execute(
                select(ModelProduct, ModelVersion, ModelPublication)
                .join(ModelVersion, ModelVersion.model_product_id == ModelProduct.id)
                .join(
                    ModelPublication,
                    (ModelPublication.model_product_id == ModelProduct.id)
                    & (ModelPublication.model_version_id == ModelVersion.id),
                )
                .where(
                    ModelProduct.space_id == space_id,
                    ModelProduct.lifecycle_status == "active",
                    ModelVersion.id == version_id,
                    ModelVersion.status == "approved",
                    ModelPublication.status == "active",
                )
            )
        ).first()
        if row is None:
            raise CommerceError("model product version is not an active publication")
        product, version, _publication = row
        kind = "model"
    else:
        raise CommerceError("product_kind must be data or model")
    try:
        modes = resolve_service_modes(kind, version.default_policy_template)
    except ValueError as exc:
        raise CommerceError(f"invalid product service-mode policy: {exc}") from exc
    return ProductOfferContext(
        product_kind=kind,
        product_id=product.id,
        version_id=version.id,
        product_name=product.name,
        product_code=product.product_code,
        provider_organization_id=product.provider_organization_id,
        policy=version.default_policy_template,
        is_demo=product.is_demo,
        service_modes=tuple(modes),
    )


async def commercial_offers_for_version(
    session: AsyncSession,
    *,
    space_id: UUID,
    product_kind: str,
    version_id: UUID,
) -> list[dict[str, Any]]:
    context = await _offer_context(
        session,
        space_id=space_id,
        product_kind=product_kind,
        version_id=version_id,
    )
    offers: list[dict[str, Any]] = []
    for mode in context.service_modes:
        try:
            offer = resolve_offer_snapshot(
                product_kind=context.product_kind,
                version_id=str(context.version_id),
                service_mode=mode,
                policy=context.policy,
                is_demo=context.is_demo,
                demo_price_plan_eligible=demo_price_plan_eligible(
                    product_kind=context.product_kind,
                    product_code=context.product_code,
                ),
            )
        except ValueError:
            continue
        offer.update(
            {
                "product_id": str(context.product_id),
                "product_name": context.product_name,
                "product_code": context.product_code,
                "provider_organization_id": str(context.provider_organization_id),
            }
        )
        offers.append(offer)
    return offers


async def _offer_for_mode(
    session: AsyncSession,
    *,
    space_id: UUID,
    product_kind: str,
    version_id: UUID,
    service_mode: str,
) -> tuple[ProductOfferContext, dict[str, Any]]:
    context = await _offer_context(
        session,
        space_id=space_id,
        product_kind=product_kind,
        version_id=version_id,
    )
    if service_mode not in context.service_modes:
        raise CommerceError("the product version does not offer this service mode")
    try:
        offer = resolve_offer_snapshot(
            product_kind=context.product_kind,
            version_id=str(context.version_id),
            service_mode=service_mode,
            policy=context.policy,
            is_demo=context.is_demo,
            demo_price_plan_eligible=demo_price_plan_eligible(
                product_kind=context.product_kind,
                product_code=context.product_code,
            ),
        )
    except ValueError as exc:
        raise CommerceError(str(exc)) from exc
    offer.update(
        {
            "product_id": str(context.product_id),
            "product_name": context.product_name,
            "product_code": context.product_code,
            "provider_organization_id": str(context.provider_organization_id),
        }
    )
    return context, offer


def _line_document(
    *,
    line_no: int,
    context: ProductOfferContext,
    offer: dict[str, Any],
) -> dict[str, Any]:
    amount = int(offer["unit_amount_minor"])
    platform_fee, provider_net = split_gross_amount(amount)
    return {
        "line_no": line_no,
        "provider_organization_id": str(context.provider_organization_id),
        "product_kind": context.product_kind,
        "product_id": str(context.product_id),
        "version_id": str(context.version_id),
        "product_name": context.product_name,
        "service_mode": offer["service_mode"],
        "currency": CURRENCY,
        "quantity": 1,
        "unit_amount_minor": amount,
        "gross_amount_minor": amount,
        "platform_fee_minor": platform_fee,
        "provider_net_minor": provider_net,
        "offer_snapshot": offer,
        "offer_digest": canonical_json_digest_v1(offer),
    }


async def _existing_source_order(
    session: AsyncSession, *, source_type: str, source_id: UUID
) -> CommercialOrder | None:
    return await session.scalar(
        select(CommercialOrder).where(
            CommercialOrder.source_type == source_type,
            CommercialOrder.source_id == source_id,
        )
    )


async def _persist_order(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: CommerceActor,
    source_type: str,
    source_id: UUID,
    service_access_request_id: UUID | None,
    contract_id: UUID | None,
    line_documents: list[dict[str, Any]],
    agreement_snapshot: dict[str, Any],
    raw_key: str,
) -> tuple[CommercialOrder, AuditEvent]:
    if not line_documents:
        raise CommerceError("commercial order requires at least one priced line")
    existing = await _existing_source_order(
        session, source_type=source_type, source_id=source_id
    )
    if existing is not None:
        if existing.requester_organization_id != actor.organization_id:
            raise CommerceError("commercial source already belongs to another requester")
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.subject_type == "commercial_order",
                AuditEvent.subject_id == existing.id,
                AuditEvent.event_type == "commercial.order.created",
            )
        )
        if event is None:
            raise CommerceError("existing commercial order has incomplete audit evidence")
        return existing, event

    gross = sum(item["gross_amount_minor"] for item in line_documents)
    platform_fee = sum(item["platform_fee_minor"] for item in line_documents)
    provider_net = sum(item["provider_net_minor"] for item in line_documents)
    pricing_plan_versions = sorted(
        {
            str(item["offer_snapshot"]["pricing_plan_version"])
            for item in line_documents
        }
    )
    pricing_sources = sorted(
        {str(item["offer_snapshot"]["pricing_source"]) for item in line_documents}
    )
    order_id = uuid5(
        NAMESPACE_URL, f"medtrust:commercial-order:{space_id}:{source_type}:{source_id}"
    )
    quote_snapshot = {
        "schema_version": "medtrust.commercial-quote/v1",
        "source_type": source_type,
        "source_id": str(source_id),
        "currency": CURRENCY,
        "gross_amount_minor": gross,
        "platform_fee_rate_bps": PLATFORM_FEE_RATE_BPS,
        "platform_fee_minor": platform_fee,
        "provider_net_minor": provider_net,
        "pricing_plan_versions": pricing_plan_versions,
        "pricing_sources": pricing_sources,
        "lines": line_documents,
    }
    agreement_snapshot = {
        **agreement_snapshot,
        "schema_version": "medtrust.commercial-agreement/v1",
        "order_id": str(order_id),
        "quote_digest": canonical_json_digest_v1(quote_snapshot),
        "platform_fee_included": True,
        "payment_boundary": "local_demo_simulation_no_real_funds",
        "no_ownership_transfer": True,
    }
    command = _command(
        actor,
        space_id=space_id,
        subject_id=order_id,
        action="create-order",
        raw_key=raw_key,
    )
    command_document = {
        "schema_version": "medtrust.commercial-order-command/v1",
        "order_id": str(order_id),
        "source_type": source_type,
        "source_id": str(source_id),
        "quote_digest": canonical_json_digest_v1(quote_snapshot),
        "agreement_digest": canonical_json_digest_v1(agreement_snapshot),
    }
    replay, command_digest = await begin_audited_command(
        session,
        space_id=space_id,
        event_type="commercial.order.created",
        subject_type="commercial_order",
        command=command,
        request_snapshot=command_document,
        expected_subject_id=order_id,
    )
    if replay is not None:
        existing = await session.get(CommercialOrder, order_id)
        if existing is None:
            raise CommerceError("idempotent order replay is incomplete")
        return existing, replay

    now = _now()
    order = CommercialOrder(
        id=order_id,
        space_id=space_id,
        order_number=f"MTO-{order_id.hex[:12].upper()}",
        requester_organization_id=actor.organization_id,
        requester_user_id=actor.user_id,
        source_type=source_type,
        source_id=source_id,
        service_access_request_id=service_access_request_id,
        contract_id=contract_id,
        status="agreement_pending",
        currency=CURRENCY,
        gross_amount_minor=gross,
        platform_fee_rate_bps=PLATFORM_FEE_RATE_BPS,
        platform_fee_minor=platform_fee,
        provider_net_minor=provider_net,
        quote_snapshot=quote_snapshot,
        quote_digest=command_document["quote_digest"],
        agreement_snapshot=agreement_snapshot,
        agreement_digest=command_document["agreement_digest"],
        create_idempotency_digest=command.idempotency_key,
        created_at=now,
        updated_at=now,
        row_version=1,
    )
    session.add(order)
    for item in line_documents:
        session.add(
            CommercialOrderLine(
                order_id=order.id,
                line_no=item["line_no"],
                provider_organization_id=UUID(item["provider_organization_id"]),
                product_kind=item["product_kind"],
                product_id=UUID(item["product_id"]),
                version_id=UUID(item["version_id"]),
                product_name=item["product_name"],
                service_mode=item["service_mode"],
                currency=CURRENCY,
                quantity=1,
                unit_amount_minor=item["unit_amount_minor"],
                gross_amount_minor=item["gross_amount_minor"],
                platform_fee_minor=item["platform_fee_minor"],
                provider_net_minor=item["provider_net_minor"],
                offer_snapshot=item["offer_snapshot"],
                offer_digest=item["offer_digest"],
                created_at=now,
            )
        )
    await session.flush()
    appended = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type="commercial.order.created",
        subject_type="commercial_order",
        subject_id=order.id,
        result="success",
        evidence_snapshot={
            "schema_version": "medtrust.commercial-order-created/v1",
            "command_request_digest": command_digest,
            "order_number": order.order_number,
            "source_type": source_type,
            "source_id": str(source_id),
            "quote_digest": order.quote_digest,
            "agreement_digest": order.agreement_digest,
            "gross_amount_minor": gross,
            "platform_fee_minor": platform_fee,
            "provider_net_minor": provider_net,
            "pricing_plan_versions": pricing_plan_versions,
            "pricing_sources": pricing_sources,
            "state_after": "agreement_pending",
        },
        **command.append_kwargs(),
    )
    return order, appended.event


async def create_order_from_service_access(
    session: AsyncSession,
    *, request_id: UUID,
    actor: CommerceActor,
    raw_key: str,
) -> tuple[CommercialOrder, AuditEvent]:
    access_request = await session.scalar(
        select(ServiceAccessRequest)
        .where(ServiceAccessRequest.id == request_id)
        .with_for_update()
    )
    if access_request is None:
        raise CommerceError("service access request was not found")
    await _require_actor(
        session,
        space_id=access_request.space_id,
        actor=actor,
        roles={"data_requester"},
    )
    if access_request.requester_organization_id != actor.organization_id:
        raise CommerceError("only the requesting organization may create checkout")
    if access_request.status != "approved_pending_contract":
        raise CommerceError("provider and operator approval are required before checkout")
    context, offer = await _offer_for_mode(
        session,
        space_id=access_request.space_id,
        product_kind=access_request.product_kind,
        version_id=access_request.version_id,
        service_mode=access_request.service_mode,
    )
    agreement = {
        "source_request_number": access_request.request_number,
        "purpose": access_request.purpose,
        "intended_use": access_request.intended_use,
        "requested_duration_days": access_request.requested_duration_days,
        "product_snapshot_digest": access_request.product_snapshot_digest,
        "delivery_boundary": offer["delivery_boundary"],
    }
    return await _persist_order(
        session,
        space_id=access_request.space_id,
        actor=actor,
        source_type="service_access",
        source_id=access_request.id,
        service_access_request_id=access_request.id,
        contract_id=None,
        line_documents=[_line_document(line_no=1, context=context, offer=offer)],
        agreement_snapshot=agreement,
        raw_key=raw_key,
    )


async def create_order_from_contract(
    session: AsyncSession,
    *, contract_id: UUID,
    actor: CommerceActor,
    raw_key: str,
) -> tuple[CommercialOrder, AuditEvent]:
    contract = await session.scalar(
        select(Contract).where(Contract.id == contract_id).with_for_update()
    )
    if contract is None:
        raise CommerceError("contract was not found")
    await _require_actor(
        session,
        space_id=contract.space_id,
        actor=actor,
        roles={"data_requester"},
    )
    application = await session.get(Application, contract.application_id)
    if application is None or application.applicant_organization_id != actor.organization_id:
        raise CommerceError("only the contract requester may create checkout")
    if application.status != "approved":
        raise CommerceError("approved application is required before checkout")
    active_revision = await session.scalar(
        select(ContractRevision).where(
            ContractRevision.contract_id == contract.id,
            ContractRevision.status == "active",
        )
    )
    if active_revision is None:
        raise CommerceError("an ACTIVE contract is required before compute checkout")
    require_contract_checkout_security(
        await validate_contract_security(
            session,
            active_revision,
            stage="commercial_checkout",
        )
    )
    items = list(
        (
            await session.scalars(
                select(ApplicationItem)
                .where(ApplicationItem.application_id == application.id)
                .order_by(ApplicationItem.position_no)
            )
        ).all()
    )
    selection = await session.scalar(
        select(ApplicationModelSelection).where(
            ApplicationModelSelection.application_id == application.id
        )
    )
    if len(items) != 1 or selection is None:
        raise CommerceError(
            "commercial demo checkout requires exactly one data and one model selection"
        )
    from .gating import contract_commerce_requirement

    requirement = await contract_commerce_requirement(
        session, contract_id=contract.id
    )
    if requirement["configuration_error"] is not None:
        raise CommerceError(requirement["configuration_error"])
    if not requirement["required"]:
        raise CommerceError(
            "this legacy contract has no published commercial compute bundle"
        )
    matches = {item["product_kind"]: item for item in requirement["matches"]}
    if set(matches) != {"data", "model"}:
        raise CommerceError("commercial compute bundle resolution is incomplete")
    data_context, data_offer = await _offer_for_mode(
        session,
        space_id=contract.space_id,
        product_kind="data",
        version_id=UUID(matches["data"]["pricing_version_id"]),
        service_mode="controlled_compute",
    )
    model_context, model_offer = await _offer_for_mode(
        session,
        space_id=contract.space_id,
        product_kind="model",
        version_id=UUID(matches["model"]["pricing_version_id"]),
        service_mode="controlled_compute",
    )
    data_offer.update(
        {
            "contract_selected_version_id": str(items[0].data_product_version_id),
            "contract_asset_match_type": matches["data"]["match_type"],
        }
    )
    model_offer.update(
        {
            "contract_selected_version_id": str(selection.model_version_id),
            "contract_asset_match_type": matches["model"]["match_type"],
        }
    )
    agreement = {
        "contract_id": str(contract.id),
        "contract_number": contract.contract_number,
        "active_revision_id": str(active_revision.id),
        "contract_content_digest": active_revision.content_digest,
        "application_id": str(application.id),
        "application_number": application.application_number,
        "purpose": application.purpose,
        "requested_run_limit": application.requested_run_limit,
        "requested_duration_seconds": application.requested_duration_seconds,
        "delivery_boundary": "controlled_execution_only_no_raw_data_or_model_weights",
        "commercial_requirement": requirement,
    }
    return await _persist_order(
        session,
        space_id=contract.space_id,
        actor=actor,
        source_type="contract",
        source_id=contract.id,
        service_access_request_id=None,
        contract_id=contract.id,
        line_documents=[
            _line_document(line_no=1, context=data_context, offer=data_offer),
            _line_document(line_no=2, context=model_context, offer=model_offer),
        ],
        agreement_snapshot=agreement,
        raw_key=raw_key,
    )


async def accept_commercial_agreement(
    session: AsyncSession,
    *,
    order_id: UUID,
    actor: CommerceActor,
    raw_key: str,
) -> tuple[CommercialOrder, AuditEvent]:
    order = await session.scalar(
        select(CommercialOrder).where(CommercialOrder.id == order_id).with_for_update()
    )
    if order is None:
        raise CommerceError("commercial order was not found")
    await _require_actor(
        session, space_id=order.space_id, actor=actor, roles={"data_requester"}
    )
    if order.requester_organization_id != actor.organization_id:
        raise CommerceError("only the requester may accept the agreement")
    command = _command(
        actor,
        space_id=order.space_id,
        subject_id=order.id,
        action="accept-agreement",
        raw_key=raw_key,
    )
    if order.status != "agreement_pending":
        if order.agreement_idempotency_digest == command.idempotency_key:
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.event_type == "commercial.agreement.accepted",
                    AuditEvent.subject_id == order.id,
                    AuditEvent.idempotency_key == command.idempotency_key,
                )
            )
            if event is not None:
                return order, event
        raise CommerceError("commercial agreement has already been decided")
    document = {
        "schema_version": "medtrust.commercial-agreement-acceptance/v1",
        "order_id": str(order.id),
        "agreement_digest": order.agreement_digest,
    }
    replay, command_digest = await begin_audited_command(
        session,
        space_id=order.space_id,
        event_type="commercial.agreement.accepted",
        subject_type="commercial_order",
        command=command,
        request_snapshot=document,
        expected_subject_id=order.id,
    )
    if replay is not None:
        return order, replay
    before = order.status
    order.status = "awaiting_payment"
    order.agreement_accepted_at = _now()
    order.agreement_accepted_by = actor.user_id
    order.agreement_idempotency_digest = command.idempotency_key
    order.updated_at = _now()
    order.row_version += 1
    order._transition_validated = True
    await session.flush([order])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=order.space_id,
        event_type="commercial.agreement.accepted",
        subject_type="commercial_order",
        subject_id=order.id,
        result="success",
        evidence_snapshot={
            "schema_version": "medtrust.commercial-agreement-accepted/v1",
            "command_request_digest": command_digest,
            "agreement_digest": order.agreement_digest,
            "accepted_by": str(actor.user_id),
            "state_before": before,
            "state_after": order.status,
        },
        **command.append_kwargs(),
    )
    return order, appended.event


async def pay_commercial_order(
    session: AsyncSession,
    *,
    order_id: UUID,
    actor: CommerceActor,
    method: str,
    raw_key: str,
) -> tuple[CommercialOrder, DemoPayment, CommercialFulfillment, AuditEvent]:
    if method not in {"wechat_demo", "alipay_demo", "bank_card_demo"}:
        raise CommerceError("unsupported local demo payment method")
    order = await session.scalar(
        select(CommercialOrder).where(CommercialOrder.id == order_id).with_for_update()
    )
    if order is None:
        raise CommerceError("commercial order was not found")
    await _require_actor(
        session, space_id=order.space_id, actor=actor, roles={"data_requester"}
    )
    if order.requester_organization_id != actor.organization_id:
        raise CommerceError("only the requester may pay this order")
    payment_id = uuid5(NAMESPACE_URL, f"medtrust:commercial-payment:{order.id}")
    command = _command(
        actor,
        space_id=order.space_id,
        subject_id=payment_id,
        action="pay",
        raw_key=raw_key,
    )
    if order.status == "paid":
        existing = await session.scalar(
            select(DemoPayment).where(DemoPayment.order_id == order.id)
        )
        fulfillment = await session.scalar(
            select(CommercialFulfillment).where(
                CommercialFulfillment.order_id == order.id
            )
        )
        if (
            existing is not None
            and fulfillment is not None
            and existing.idempotency_digest == command.idempotency_key
            and existing.method == method
        ):
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.event_type == "commercial.payment.succeeded",
                    AuditEvent.subject_id == existing.id,
                )
            )
            if event is not None:
                return order, existing, fulfillment, event
        raise CommerceError("commercial order has already been paid")
    if order.status != "awaiting_payment":
        raise CommerceError("commercial agreement must be accepted before payment")
    lines = list(
        (
            await session.scalars(
                select(CommercialOrderLine)
                .where(CommercialOrderLine.order_id == order.id)
                .order_by(CommercialOrderLine.line_no)
            )
        ).all()
    )
    if order.source_type == "contract":
        kind = "execution_entitlement"
        line_id = None
        contract_id = order.contract_id
    else:
        if len(lines) != 1:
            raise CommerceError("service delivery order must have exactly one line")
        kind = (
            "data_document_package"
            if lines[0].product_kind == "data"
            else "model_license_package"
        )
        try:
            automatic_delivery_profile(
                kind=kind, version_id=str(lines[0].version_id)
            )
        except ValueError as exc:
            raise CommerceError(str(exc)) from exc
        line_id = lines[0].id
        contract_id = None
    payment_document = {
        "schema_version": "medtrust.local-demo-payment/v1",
        "order_id": str(order.id),
        "method": method,
        "currency": order.currency,
        "amount_minor": order.gross_amount_minor,
        "quote_digest": order.quote_digest,
    }
    replay, command_digest = await begin_audited_command(
        session,
        space_id=order.space_id,
        event_type="commercial.payment.succeeded",
        subject_type="commercial_payment",
        command=command,
        request_snapshot=payment_document,
        expected_subject_id=payment_id,
    )
    if replay is not None:
        existing = await session.get(DemoPayment, payment_id)
        fulfillment = await session.scalar(
            select(CommercialFulfillment).where(
                CommercialFulfillment.order_id == order.id
            )
        )
        if existing is None or fulfillment is None:
            raise CommerceError("idempotent payment replay is incomplete")
        return order, existing, fulfillment, replay

    now = _now()
    channel_fee = channel_fee_for(order.gross_amount_minor)
    receipt = {
        **payment_document,
        "payment_id": str(payment_id),
        "transaction_number": f"DEMO-{payment_id.hex[:16].upper()}",
        "status": "succeeded",
        "paid_at": now.isoformat(),
        "channel_fee_rate_bps": CHANNEL_FEE_RATE_BPS,
        "channel_fee_minor": channel_fee,
        "platform_margin_minor": order.platform_fee_minor - channel_fee,
        "real_funds_moved": False,
        "card_details_collected": False,
    }
    if receipt["platform_margin_minor"] < 0:
        raise CommerceError("channel cost cannot exceed the included platform fee")
    payment = DemoPayment(
        id=payment_id,
        order_id=order.id,
        method=method,
        status="succeeded",
        currency=order.currency,
        amount_minor=order.gross_amount_minor,
        channel_fee_rate_bps=CHANNEL_FEE_RATE_BPS,
        channel_fee_minor=channel_fee,
        transaction_number=receipt["transaction_number"],
        receipt_snapshot=receipt,
        receipt_digest=canonical_json_digest_v1(receipt),
        idempotency_digest=command.idempotency_key,
        paid_at=now,
        created_at=now,
    )
    session.add(payment)
    order.status = "paid"
    order.updated_at = now
    order.row_version += 1
    order._transition_validated = True

    entitled_products = [
        {
            "order_line_id": str(line.id),
            "product_kind": line.product_kind,
            "product_id": str(line.product_id),
            "product_name": line.product_name,
            "version_id": str(line.version_id),
            "provider_organization_id": str(line.provider_organization_id),
            "service_mode": line.service_mode,
            "offer_digest": line.offer_digest,
            "contract_selected_version_id": line.offer_snapshot.get(
                "contract_selected_version_id"
            ),
        }
        for line in lines
    ]
    authorized_duration_days = order.agreement_snapshot.get(
        "requested_duration_days"
    )
    if authorized_duration_days is None and len(lines) == 1:
        authorized_duration_days = lines[0].offer_snapshot.get("validity_days")
    fulfillment_id = uuid5(
        NAMESPACE_URL, f"medtrust:commercial-fulfillment:{order.id}:{kind}"
    )
    entitlement = {
        "schema_version": "medtrust.commercial-entitlement/v1",
        "fulfillment_id": str(fulfillment_id),
        "order_id": str(order.id),
        "order_number": order.order_number,
        "kind": kind,
        "status": "ready",
        "contract_id": str(contract_id) if contract_id else None,
        "quote_digest": order.quote_digest,
        "agreement_digest": order.agreement_digest,
        "payment_receipt_digest": payment.receipt_digest,
        "entitled_products": entitled_products,
        "authorized_duration_days": authorized_duration_days,
        "download_boundary": (
            "not_downloadable_execute_under_active_contract"
            if kind == "execution_entitlement"
            else "fixed_allowlisted_documentation_zip_only"
        ),
        "raw_patient_data_included": False,
        "model_weights_included": False,
    }
    fulfillment = CommercialFulfillment(
        id=fulfillment_id,
        space_id=order.space_id,
        order_id=order.id,
        order_line_id=line_id,
        kind=kind,
        status="ready",
        contract_id=contract_id,
        entitlement_snapshot=entitlement,
        entitlement_digest=canonical_json_digest_v1(entitlement),
        created_at=now,
    )
    session.add(fulfillment)
    await session.flush([order, payment, fulfillment])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=order.space_id,
        event_type="commercial.payment.succeeded",
        subject_type="commercial_payment",
        subject_id=payment.id,
        result="success",
        evidence_snapshot={
            "schema_version": "medtrust.commercial-payment-succeeded/v1",
            "command_request_digest": command_digest,
            "order_id": str(order.id),
            "transaction_number": payment.transaction_number,
            "method": method,
            "currency": payment.currency,
            "amount_minor": payment.amount_minor,
            "channel_fee_minor": payment.channel_fee_minor,
            "platform_fee_minor": order.platform_fee_minor,
            "provider_net_minor": order.provider_net_minor,
            "real_funds_moved": False,
            "state_after": "paid",
        },
        **command.append_kwargs(),
    )
    fulfillment_command = _command(
        actor,
        space_id=order.space_id,
        subject_id=fulfillment.id,
        action="create-fulfillment",
        raw_key=raw_key,
    )
    await append_audit_event_with_outbox(
        session,
        space_id=order.space_id,
        event_type="commercial.fulfillment.created",
        subject_type="commercial_fulfillment",
        subject_id=fulfillment.id,
        result="success",
        evidence_snapshot={
            "schema_version": "medtrust.commercial-fulfillment-created/v1",
            "order_id": str(order.id),
            "kind": kind,
            "contract_id": str(contract_id) if contract_id else None,
            "entitlement_digest": fulfillment.entitlement_digest,
            "entitled_products": entitled_products,
            "authorized_duration_days": authorized_duration_days,
            "raw_patient_data_included": False,
            "model_weights_included": False,
        },
        **fulfillment_command.append_kwargs(),
    )
    return order, payment, fulfillment, appended.event


def _grant_token(*, grant_id: UUID, raw_key: str) -> str:
    material = f"medtrust-local-demo:{grant_id}:{raw_key}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(hashlib.sha256(material).digest()).decode("ascii")
    return "mtg_" + encoded.rstrip("=")


def _token_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_commercial_download_grant(
    session: AsyncSession,
    *,
    order_id: UUID,
    actor: CommerceActor,
    raw_key: str,
) -> tuple[CommercialDownloadGrant, str, AuditEvent]:
    order = await session.scalar(
        select(CommercialOrder).where(CommercialOrder.id == order_id).with_for_update()
    )
    if order is None:
        raise CommerceError("commercial order was not found")
    await _require_actor(
        session, space_id=order.space_id, actor=actor, roles={"data_requester"}
    )
    if order.requester_organization_id != actor.organization_id:
        raise CommerceError("only the requester may create a download grant")
    if order.status != "paid" or order.source_type != "service_access":
        raise CommerceError("a paid service-delivery order is required")
    fulfillment = await session.scalar(
        select(CommercialFulfillment).where(
            CommercialFulfillment.order_id == order.id,
            CommercialFulfillment.kind.in_(
                ("data_document_package", "model_license_package")
            ),
        )
    )
    if fulfillment is None:
        raise CommerceError("downloadable fulfillment is not ready")
    grant_id = uuid5(
        NAMESPACE_URL,
        f"medtrust:commercial-download-grant:{fulfillment.id}:{actor.user_id}:{raw_key}",
    )
    command = _command(
        actor,
        space_id=order.space_id,
        subject_id=grant_id,
        action="create-download-grant",
        raw_key=raw_key,
    )
    token = _grant_token(grant_id=grant_id, raw_key=raw_key)
    existing = await session.scalar(
        select(CommercialDownloadGrant).where(
            CommercialDownloadGrant.fulfillment_id == fulfillment.id
        )
    )
    if existing is not None:
        if existing.create_idempotency_digest != command.idempotency_key:
            raise CommerceError(
                "this fulfillment already has its one permitted download grant"
            )
        token = _grant_token(grant_id=existing.id, raw_key=raw_key)
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "commercial.download.grant.created",
                AuditEvent.subject_id == existing.id,
            )
        )
        if event is None:
            raise CommerceError("download grant audit evidence is incomplete")
        return existing, token, event
    _, package_bytes = build_delivery_zip(
        kind=fulfillment.kind,
        entitlement_snapshot=fulfillment.entitlement_snapshot,
    )
    filename, _ = build_delivery_zip(
        kind=fulfillment.kind,
        entitlement_snapshot=fulfillment.entitlement_snapshot,
    )
    document = {
        "schema_version": "medtrust.commercial-download-grant-command/v1",
        "grant_id": str(grant_id),
        "fulfillment_id": str(fulfillment.id),
        "token_digest": _token_digest(token),
        "filename": filename,
        "package_size_bytes": len(package_bytes),
    }
    replay, command_digest = await begin_audited_command(
        session,
        space_id=order.space_id,
        event_type="commercial.download.grant.created",
        subject_type="commercial_download_grant",
        command=command,
        request_snapshot=document,
        expected_subject_id=grant_id,
    )
    if replay is not None:
        existing = await session.get(CommercialDownloadGrant, grant_id)
        if existing is None:
            raise CommerceError("idempotent download grant replay is incomplete")
        return existing, token, replay
    now = _now()
    grant = CommercialDownloadGrant(
        id=grant_id,
        space_id=order.space_id,
        fulfillment_id=fulfillment.id,
        requester_organization_id=actor.organization_id,
        token_digest=document["token_digest"],
        filename=filename,
        status="active",
        max_downloads=1,
        download_count=0,
        expires_at=now + timedelta(minutes=10),
        create_idempotency_digest=command.idempotency_key,
        created_at=now,
        row_version=1,
    )
    session.add(grant)
    await session.flush([grant])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=order.space_id,
        event_type="commercial.download.grant.created",
        subject_type="commercial_download_grant",
        subject_id=grant.id,
        result="success",
        evidence_snapshot={
            "schema_version": "medtrust.commercial-download-grant-created/v1",
            "command_request_digest": command_digest,
            **document,
            "expires_at": grant.expires_at.isoformat(),
            "max_downloads": 1,
        },
        **command.append_kwargs(),
    )
    return grant, token, appended.event


async def consume_commercial_download(
    session: AsyncSession,
    *,
    token: str,
    actor: CommerceActor,
    raw_key: str,
) -> tuple[str, bytes, CommercialDownloadGrant, AuditEvent]:
    if not token.startswith("mtg_") or len(token) > 128:
        raise CommerceError("download token is invalid")
    digest = _token_digest(token)
    grant = await session.scalar(
        select(CommercialDownloadGrant)
        .where(CommercialDownloadGrant.token_digest == digest)
        .with_for_update()
    )
    if grant is None:
        raise CommerceError("download token is invalid")
    await _require_actor(
        session, space_id=grant.space_id, actor=actor, roles={"data_requester"}
    )
    if grant.requester_organization_id != actor.organization_id:
        raise CommerceError("download token belongs to another organization")
    if grant.status != "active" or grant.download_count != 0:
        raise CommerceError("download token has already been consumed")
    if grant.expires_at <= _now():
        raise CommerceError("download token has expired")
    fulfillment = await session.get(CommercialFulfillment, grant.fulfillment_id)
    if fulfillment is None:
        raise CommerceError("download fulfillment is missing")
    filename, package_bytes = build_delivery_zip(
        kind=fulfillment.kind,
        entitlement_snapshot=fulfillment.entitlement_snapshot,
    )
    command = _command(
        actor,
        space_id=grant.space_id,
        subject_id=grant.id,
        action="consume-download",
        raw_key=raw_key,
    )
    document = {
        "schema_version": "medtrust.commercial-download-consume/v1",
        "grant_id": str(grant.id),
        "token_digest": digest,
        "filename": filename,
        "package_digest": "sha256:" + hashlib.sha256(package_bytes).hexdigest(),
    }
    replay, command_digest = await begin_audited_command(
        session,
        space_id=grant.space_id,
        event_type="commercial.download.completed",
        subject_type="commercial_download_grant",
        command=command,
        request_snapshot=document,
        expected_subject_id=grant.id,
    )
    if replay is not None:
        raise CommerceError("download token is single-use and has already been consumed")
    grant.status = "consumed"
    grant.download_count = 1
    grant.consumed_at = _now()
    grant.consume_idempotency_digest = command.idempotency_key
    grant.row_version += 1
    grant._transition_validated = True
    await session.flush([grant])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=grant.space_id,
        event_type="commercial.download.completed",
        subject_type="commercial_download_grant",
        subject_id=grant.id,
        result="success",
        evidence_snapshot={
            "schema_version": "medtrust.commercial-download-completed/v1",
            "command_request_digest": command_digest,
            **document,
            "download_count": 1,
            "patient_data_included": False,
            "model_weights_included": False,
        },
        **command.append_kwargs(),
    )
    return filename, package_bytes, grant, appended.event


async def commercial_order_payload(
    session: AsyncSession,
    *,
    order: CommercialOrder,
    actor: CommerceActor,
) -> dict[str, Any]:
    if actor.role == "data_requester" and order.requester_organization_id != actor.organization_id:
        raise CommerceError("commercial order belongs to another requester")
    lines = list(
        (
            await session.scalars(
                select(CommercialOrderLine)
                .where(CommercialOrderLine.order_id == order.id)
                .order_by(CommercialOrderLine.line_no)
            )
        ).all()
    )
    if actor.role in {"data_provider", "model_provider"}:
        if not any(line.provider_organization_id == actor.organization_id for line in lines):
            raise CommerceError("commercial order is outside this provider organization")
    payment = await session.scalar(
        select(DemoPayment).where(DemoPayment.order_id == order.id)
    )
    fulfillments = list(
        (
            await session.scalars(
                select(CommercialFulfillment)
                .where(CommercialFulfillment.order_id == order.id)
                .order_by(CommercialFulfillment.created_at)
            )
        ).all()
    )
    downloadable_fulfillment_ids = {
        item.id
        for item in fulfillments
        if item.kind in {"data_document_package", "model_license_package"}
    }
    download_grants = (
        []
        if not downloadable_fulfillment_ids
        else list(
            (
                await session.scalars(
                    select(CommercialDownloadGrant).where(
                        CommercialDownloadGrant.fulfillment_id.in_(
                            downloadable_fulfillment_ids
                        )
                    )
                )
            ).all()
        )
    )
    grant_by_fulfillment = {
        item.fulfillment_id: item for item in download_grants
    }
    allowed_actions: list[str] = []
    if actor.role == "data_requester" and actor.organization_id == order.requester_organization_id:
        if order.status == "agreement_pending":
            allowed_actions.append("accept_agreement")
        elif order.status == "awaiting_payment":
            allowed_actions.append("pay")
        elif order.status == "paid" and any(
            item.id not in grant_by_fulfillment
            for item in fulfillments
            if item.kind in {"data_document_package", "model_license_package"}
        ):
            allowed_actions.append("create_download_grant")
        elif order.status == "paid" and any(
            item.kind == "execution_entitlement" for item in fulfillments
        ):
            allowed_actions.append("proceed_to_execution")
    next_action = (
        allowed_actions[0]
        if allowed_actions
        else "complete" if order.status == "paid" else "view_status"
    )
    provider_names = {
        row.id: row.display_name
        for row in (
            await session.scalars(
                select(Organization).where(
                    Organization.id.in_({line.provider_organization_id for line in lines})
                )
            )
        ).all()
    }
    provider_view = actor.role in {"data_provider", "model_provider"}
    visible_lines = [
        line
        for line in lines
        if not provider_view
        or line.provider_organization_id == actor.organization_id
    ]
    visible_gross = sum(line.gross_amount_minor for line in visible_lines)
    visible_provider_net = sum(line.provider_net_minor for line in visible_lines)
    payment_payload = None
    if payment is not None:
        if provider_view:
            payment_payload = {
                "status": payment.status,
                "paid_at": payment.paid_at.isoformat(),
                "provider_settlement_amount_minor": visible_provider_net,
                "currency": payment.currency,
            }
        elif actor.role == "space_operator":
            payment_payload = {
                "id": str(payment.id),
                "method": payment.method,
                "status": payment.status,
                "transaction_number": payment.transaction_number,
                "currency": payment.currency,
                "amount_minor": payment.amount_minor,
                "channel_fee_rate_bps": payment.channel_fee_rate_bps,
                "channel_fee_minor": payment.channel_fee_minor,
                "platform_margin_minor": order.platform_fee_minor
                - payment.channel_fee_minor,
                "paid_at": payment.paid_at.isoformat(),
                "real_funds_moved": False,
            }
        else:
            payment_payload = {
                "id": str(payment.id),
                "method": payment.method,
                "status": payment.status,
                "transaction_number": payment.transaction_number,
                "currency": payment.currency,
                "amount_minor": payment.amount_minor,
                "paid_at": payment.paid_at.isoformat(),
                "real_funds_moved": False,
            }
    payload = {
        "id": str(order.id),
        "order_id": str(order.id),
        "order_number": order.order_number,
        "space_id": str(order.space_id),
        "source_type": order.source_type,
        "source_id": str(order.source_id),
        "contract_id": str(order.contract_id) if order.contract_id else None,
        "service_access_request_id": (
            str(order.service_access_request_id)
            if order.service_access_request_id
            else None
        ),
        "requester_organization_id": str(order.requester_organization_id),
        "status": order.status,
        "currency": order.currency,
        "quote_digest": order.quote_digest,
        "agreement": {
            "digest": order.agreement_digest,
            "snapshot": _agreement_payload_for_role(
                order.agreement_snapshot, role=actor.role
            ),
            "accepted_at": (
                order.agreement_accepted_at.isoformat()
                if order.agreement_accepted_at
                else None
            ),
        },
        "lines": [
            {
                "id": str(line.id),
                "line_no": line.line_no,
                "provider_organization_id": str(line.provider_organization_id),
                "provider_name": provider_names.get(line.provider_organization_id, ""),
                "product_kind": line.product_kind,
                "product_id": str(line.product_id),
                "version_id": str(line.version_id),
                "product_name": line.product_name,
                "service_mode": line.service_mode,
                "currency": line.currency,
                "unit_amount_minor": line.unit_amount_minor,
                "gross_amount_minor": line.gross_amount_minor,
                "offer_snapshot": commercial_offer_payload_for_role(
                    line.offer_snapshot, role=actor.role
                ),
                **(
                    {
                        "platform_fee_minor": line.platform_fee_minor,
                        "provider_net_minor": line.provider_net_minor,
                    }
                    if actor.role == "space_operator"
                    else {"provider_net_minor": line.provider_net_minor}
                    if provider_view
                    else {}
                ),
            }
            for line in visible_lines
        ],
        "payment": payment_payload,
        "fulfillments": [
            {
                "id": str(item.id),
                "kind": item.kind,
                "status": item.status,
                "downloadable": item.kind
                in {"data_document_package", "model_license_package"},
                "contract_id": str(item.contract_id) if item.contract_id else None,
                "entitlement_digest": item.entitlement_digest,
                "download_grant_status": (
                    grant_by_fulfillment[item.id].status
                    if item.id in grant_by_fulfillment
                    else None
                ),
            }
            for item in fulfillments
        ],
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
        "next_action": next_action,
        "allowed_actions": allowed_actions,
    }
    if provider_view:
        payload.update(
            {
                "subtotal_amount_minor": visible_gross,
                "provider_net_minor": visible_provider_net,
            }
        )
    elif actor.role == "space_operator":
        payload.update(
            {
                "gross_amount_minor": order.gross_amount_minor,
                "platform_fee_rate_bps": order.platform_fee_rate_bps,
                "platform_fee_minor": order.platform_fee_minor,
                "provider_net_minor": order.provider_net_minor,
            }
        )
    else:
        payload["gross_amount_minor"] = order.gross_amount_minor
    return payload


async def list_orders_for_actor(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: CommerceActor,
) -> list[CommercialOrder]:
    await _require_actor(
        session,
        space_id=space_id,
        actor=actor,
        roles={"data_requester", "data_provider", "model_provider", "space_operator"},
    )
    query = select(CommercialOrder).where(CommercialOrder.space_id == space_id)
    if actor.role == "data_requester":
        query = query.where(
            CommercialOrder.requester_organization_id == actor.organization_id
        )
    elif actor.role in {"data_provider", "model_provider"}:
        line_orders = select(CommercialOrderLine.order_id).where(
            CommercialOrderLine.provider_organization_id == actor.organization_id
        )
        query = query.where(CommercialOrder.id.in_(line_orders))
    return list(
        (
            await session.scalars(query.order_by(CommercialOrder.created_at.desc()))
        ).all()
    )


async def provider_settlements(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: CommerceActor,
) -> dict[str, Any]:
    await _require_actor(
        session,
        space_id=space_id,
        actor=actor,
        roles={"data_provider", "model_provider", "space_operator"},
    )
    query = (
        select(CommercialOrderLine, CommercialOrder)
        .join(CommercialOrder, CommercialOrder.id == CommercialOrderLine.order_id)
        .where(
            CommercialOrder.space_id == space_id,
            CommercialOrder.status == "paid",
        )
    )
    if actor.role != "space_operator":
        query = query.where(
            CommercialOrderLine.provider_organization_id == actor.organization_id
        )
    rows = list((await session.execute(query)).all())
    organization_ids = {line.provider_organization_id for line, _ in rows}
    organizations = {
        item.id: item.display_name
        for item in (
            await session.scalars(
                select(Organization).where(Organization.id.in_(organization_ids))
            )
        ).all()
    }
    buckets: dict[UUID, dict[str, Any]] = {}
    for line, order in rows:
        bucket = buckets.setdefault(
            line.provider_organization_id,
            {
                "provider_organization_id": str(line.provider_organization_id),
                "provider_name": organizations.get(line.provider_organization_id, ""),
                "currency": CURRENCY,
                "gross_amount_minor": 0,
                "platform_fee_minor": 0,
                "provider_net_minor": 0,
                "paid_order_ids": set(),
            },
        )
        bucket["gross_amount_minor"] += line.gross_amount_minor
        bucket["platform_fee_minor"] += line.platform_fee_minor
        bucket["provider_net_minor"] += line.provider_net_minor
        bucket["paid_order_ids"].add(order.id)
    items = []
    for bucket in buckets.values():
        order_ids = bucket.pop("paid_order_ids")
        bucket["paid_order_count"] = len(order_ids)
        items.append(bucket)
    if actor.role != "space_operator":
        projected_items = [
            {
                key: value
                for key, value in item.items()
                if key != "platform_fee_minor"
            }
            for item in items
        ]
        return {
            "items": sorted(projected_items, key=lambda item: item["provider_name"]),
            "total": len(projected_items),
            "summary": {
                "currency": CURRENCY,
                "gross_amount_minor": sum(
                    item["gross_amount_minor"] for item in projected_items
                ),
                "provider_net_minor": sum(
                    item["provider_net_minor"] for item in projected_items
                ),
                "real_funds_moved": False,
            },
        }

    visible_order_ids = {order.id for _, order in rows}
    payments = list(
        (
            await session.scalars(
                select(DemoPayment)
                .join(CommercialOrder, CommercialOrder.id == DemoPayment.order_id)
                .where(
                    CommercialOrder.space_id == space_id,
                    CommercialOrder.status == "paid",
                    CommercialOrder.id.in_(visible_order_ids),
                )
            )
        ).all()
    )
    return {
        "items": sorted(items, key=lambda item: item["provider_name"]),
        "total": len(items),
        "summary": {
            "currency": CURRENCY,
            "gross_amount_minor": sum(item["gross_amount_minor"] for item in items),
            "platform_fee_minor": sum(
                item["platform_fee_minor"] for item in items
            ),
            "provider_net_minor": sum(item["provider_net_minor"] for item in items),
            "channel_fee_minor": sum(item.channel_fee_minor for item in payments),
            "real_funds_moved": False,
        },
    }
