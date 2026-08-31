from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    begin_audited_command,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from .models import ServiceAccessRequest


ProductKind = Literal["data", "model"]
Decision = Literal["approve", "reject"]
PRODUCT_SNAPSHOT_SCHEMA = "medtrust.service-access-product-snapshot/v1"
REQUEST_SCHEMA = "medtrust.service-access-request/v1"
DEIDENTIFIED_DATA_DELIVERY = "deidentified_data_delivery"
MODEL_ARTIFACT_LICENSE = "model_artifact_license"


class ServiceAccessError(ValueError):
    pass


class AccessActor(Protocol):
    role: str
    organization_id: UUID
    user_id: UUID


def validate_request_type(product_kind: str, service_mode: str) -> ProductKind:
    expected = {
        "data": DEIDENTIFIED_DATA_DELIVERY,
        "model": MODEL_ARTIFACT_LICENSE,
    }
    if product_kind not in expected:
        raise ServiceAccessError("product_kind must be data or model")
    if service_mode != expected[product_kind]:
        raise ServiceAccessError(
            "service mode does not match the requested product kind"
        )
    return cast(ProductKind, product_kind)


def provider_status_after(status: str, decision: Decision) -> str:
    if status != "submitted":
        raise ServiceAccessError("provider decision requires a submitted request")
    return "provider_approved" if decision == "approve" else "rejected"


def operator_status_after(status: str, decision: Decision) -> str:
    if status != "provider_approved":
        raise ServiceAccessError(
            "operator decision requires provider approval first"
        )
    return "approved_pending_contract" if decision == "approve" else "rejected"


def _clean_text(value: str, *, name: str, minimum: int, maximum: int) -> str:
    cleaned = value.strip()
    if len(cleaned) < minimum or len(cleaned) > maximum:
        raise ServiceAccessError(
            f"{name} must contain {minimum}-{maximum} characters"
        )
    return cleaned


def _command(
    actor: AccessActor,
    *,
    space_id: UUID,
    request_id: UUID,
    action: str,
    raw_key: str,
) -> AuditCommandContext:
    scope = (
        f"medtrust:service-access:{space_id}:{actor.user_id}:"
        f"{request_id}:{action}:{raw_key}"
    )
    return AuditCommandContext(
        command_id=uuid5(NAMESPACE_URL, f"{scope}:command"),
        idempotency_key=digest_idempotency_key(scope),
        correlation_id=uuid5(
            NAMESPACE_URL, f"medtrust:service-access:{request_id}:correlation"
        ),
        actor_type="user",
        actor_organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )


def _request_id(
    *, space_id: UUID, requester_user_id: UUID, raw_key: str
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"medtrust:service-access:{space_id}:{requester_user_id}:create:{raw_key}",
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _published_product_snapshot(
    session: AsyncSession,
    *,
    space_id: UUID,
    product_kind: ProductKind,
    version_id: UUID,
    service_mode: str,
) -> tuple[UUID, UUID, dict[str, Any]]:
    from app.modules.catalog.models import (
        DataProduct,
        DataProductPublication,
        DataProductVersion,
    )
    from app.modules.external_catalog.models import (
        DataProductExternalSourceLink,
        ModelProductExternalSourceLink,
    )
    from app.modules.marketplace.models import (
        ModelProduct,
        ModelPublication,
        ModelVersion,
    )
    from app.modules.marketplace.service_modes import (
        resolve_service_modes,
        service_mode_enabled,
    )

    if product_kind == "data":
        row = (
            await session.execute(
                select(DataProduct, DataProductVersion, DataProductPublication)
                .join(
                    DataProductVersion,
                    DataProductVersion.data_product_id == DataProduct.id,
                )
                .join(
                    DataProductPublication,
                    (DataProductPublication.data_product_id == DataProduct.id)
                    & (
                        DataProductPublication.data_product_version_id
                        == DataProductVersion.id
                    ),
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
            raise ServiceAccessError(
                "data product version is not an active internal publication"
            )
        product, version, publication = row
        external = await session.scalar(
            select(DataProductExternalSourceLink.id).where(
                DataProductExternalSourceLink.data_product_version_id == version.id
            )
        )
        if external is not None:
            raise ServiceAccessError(
                "external metadata-only catalog entries cannot grant data delivery"
            )
        policy = version.default_policy_template
        version_snapshot_digest = version.snapshot_digest
        policy_digest = version.default_policy_digest
        version_no = version.version_no
        version_label = version.version_label
    else:
        row = (
            await session.execute(
                select(ModelProduct, ModelVersion, ModelPublication)
                .join(
                    ModelVersion,
                    ModelVersion.model_product_id == ModelProduct.id,
                )
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
            raise ServiceAccessError(
                "model product version is not an active internal publication"
            )
        product, version, publication = row
        external = await session.scalar(
            select(ModelProductExternalSourceLink.id).where(
                ModelProductExternalSourceLink.model_version_id == version.id
            )
        )
        if external is not None:
            raise ServiceAccessError(
                "external metadata-only catalog entries cannot grant model artifacts"
            )
        policy = version.default_policy_template
        version_snapshot_digest = version.snapshot_digest
        policy_digest = version.default_policy_digest
        version_no = version.version_no
        version_label = version.version_label

    try:
        enabled = service_mode_enabled(
            product_kind, policy, service_mode, external=False
        )
        offered_modes = list(
            resolve_service_modes(product_kind, policy, external=False)
        )
    except ValueError as exc:
        raise ServiceAccessError(f"invalid product service-mode policy: {exc}") from exc
    if not enabled:
        raise ServiceAccessError(
            "the published product does not offer the requested authorization mode"
        )

    snapshot = {
        "schema_version": PRODUCT_SNAPSHOT_SCHEMA,
        "catalog_scope": "internal_published",
        "product_kind": product_kind,
        "product_id": str(product.id),
        "product_code": product.product_code,
        "name": product.name,
        "description": product.description,
        "domain": product.domain,
        "provider_organization_id": str(product.provider_organization_id),
        "version_id": str(version.id),
        "version_no": version_no,
        "version_label": version_label,
        "version_snapshot_digest": version_snapshot_digest,
        "policy_digest": policy_digest,
        "offered_service_modes": offered_modes,
        "requested_service_mode": service_mode,
        "publication_id": str(publication.id),
        "publication_visibility": publication.visibility,
        "published_at": _iso(publication.published_at),
    }
    return product.id, product.provider_organization_id, snapshot


async def create_service_access_request(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: AccessActor,
    product_kind: str,
    version_id: UUID,
    service_mode: str,
    purpose: str,
    intended_use: str,
    requested_duration_days: int,
    raw_key: str,
) -> tuple[ServiceAccessRequest, AuditEvent]:
    kind = validate_request_type(product_kind, service_mode)
    if not 1 <= requested_duration_days <= 3650:
        raise ServiceAccessError("requested duration must be between 1 and 3650 days")
    purpose = _clean_text(purpose, name="purpose", minimum=4, maximum=500)
    intended_use = _clean_text(
        intended_use, name="intended use", minimum=10, maximum=2000
    )
    if actor.role != "data_requester":
        raise ServiceAccessError("only a data requester may create this request")
    from app.modules.marketplace.services import require_actor

    await require_actor(
        session,
        space_id=space_id,
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        role_code="data_requester",
    )
    product_id, provider_organization_id, product_snapshot = (
        await _published_product_snapshot(
            session,
            space_id=space_id,
            product_kind=kind,
            version_id=version_id,
            service_mode=service_mode,
        )
    )
    request_id = _request_id(
        space_id=space_id, requester_user_id=actor.user_id, raw_key=raw_key
    )
    request_document = {
        "schema_version": REQUEST_SCHEMA,
        "request_id": str(request_id),
        "space_id": str(space_id),
        "requester_organization_id": str(actor.organization_id),
        "requester_user_id": str(actor.user_id),
        "provider_organization_id": str(provider_organization_id),
        "product_kind": kind,
        "product_id": str(product_id),
        "version_id": str(version_id),
        "service_mode": service_mode,
        "purpose": purpose,
        "intended_use": intended_use,
        "requested_duration_days": requested_duration_days,
        "product_snapshot_digest": canonical_json_digest_v1(product_snapshot),
    }
    command = _command(
        actor,
        space_id=space_id,
        request_id=request_id,
        action="create",
        raw_key=raw_key,
    )
    replay, request_digest = await begin_audited_command(
        session,
        space_id=space_id,
        event_type="service_access.request.created",
        subject_type="service_access_request",
        command=command,
        request_snapshot=request_document,
        expected_subject_id=request_id,
    )
    if replay is not None:
        existing = await session.get(ServiceAccessRequest, request_id)
        if existing is None:
            raise ServiceAccessError("idempotent request replay is incomplete")
        return existing, replay

    now = datetime.now(timezone.utc)
    access_request = ServiceAccessRequest(
        id=request_id,
        space_id=space_id,
        request_number=f"SAR-{request_id.hex[:12].upper()}",
        requester_organization_id=actor.organization_id,
        requester_user_id=actor.user_id,
        provider_organization_id=provider_organization_id,
        product_kind=kind,
        product_id=product_id,
        version_id=version_id,
        service_mode=service_mode,
        purpose=purpose,
        intended_use=intended_use,
        requested_duration_days=requested_duration_days,
        status="submitted",
        product_snapshot=product_snapshot,
        product_snapshot_digest=request_document["product_snapshot_digest"],
        request_digest=request_digest,
        create_idempotency_digest=command.idempotency_key,
        requested_at=now,
        updated_at=now,
        row_version=1,
    )
    session.add(access_request)
    await session.flush([access_request])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type="service_access.request.created",
        subject_type="service_access_request",
        subject_id=request_id,
        result="success",
        evidence_snapshot={
            "schema_version": "medtrust.service-access-request-created/v1",
            "command_request_digest": request_digest,
            "request_number": access_request.request_number,
            "product_kind": kind,
            "product_id": str(product_id),
            "version_id": str(version_id),
            "service_mode": service_mode,
            "product_snapshot_digest": access_request.product_snapshot_digest,
            "state_before": None,
            "state_after": "submitted",
            "fulfillment_created": False,
        },
        **command.append_kwargs(),
    )
    return access_request, appended.event


async def _locked_request(
    session: AsyncSession, request_id: UUID
) -> ServiceAccessRequest:
    access_request = await session.scalar(
        select(ServiceAccessRequest)
        .where(ServiceAccessRequest.id == request_id)
        .with_for_update()
    )
    if access_request is None:
        raise ServiceAccessError("service access request was not found")
    return access_request


async def decide_service_access_by_provider(
    session: AsyncSession,
    *,
    request_id: UUID,
    actor: AccessActor,
    decision: Decision,
    summary: str,
    raw_key: str,
) -> tuple[ServiceAccessRequest, AuditEvent]:
    access_request = await _locked_request(session, request_id)
    expected_role = (
        "data_provider" if access_request.product_kind == "data" else "model_provider"
    )
    if actor.role != expected_role:
        raise ServiceAccessError("this provider role cannot decide the request")
    from app.modules.marketplace.services import require_actor

    await require_actor(
        session,
        space_id=access_request.space_id,
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        role_code=expected_role,
    )
    if actor.organization_id != access_request.provider_organization_id:
        raise ServiceAccessError("only the owning provider may decide the request")
    summary = _clean_text(summary, name="decision summary", minimum=5, maximum=1000)
    event_type = (
        "service_access.provider.approved"
        if decision == "approve"
        else "service_access.provider.rejected"
    )
    command = _command(
        actor,
        space_id=access_request.space_id,
        request_id=request_id,
        action="provider-decision",
        raw_key=raw_key,
    )
    command_document = {
        "schema_version": "medtrust.service-access-provider-decision/v1",
        "request_id": str(request_id),
        "request_digest": access_request.request_digest,
        "decision": decision,
        "summary": summary,
    }
    replay, command_request_digest = await begin_audited_command(
        session,
        space_id=access_request.space_id,
        event_type=event_type,
        subject_type="service_access_request",
        command=command,
        request_snapshot=command_document,
        expected_subject_id=request_id,
    )
    if replay is not None:
        return access_request, replay

    state_before = access_request.status
    access_request.status = provider_status_after(state_before, decision)
    access_request.provider_decision = decision
    access_request.provider_decision_summary = summary
    access_request.provider_decided_by = actor.user_id
    access_request.provider_decided_at = datetime.now(timezone.utc)
    access_request.provider_decision_idempotency_digest = command.idempotency_key
    access_request.updated_at = datetime.now(timezone.utc)
    access_request.row_version += 1
    access_request._transition_validated = True
    await session.flush([access_request])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=access_request.space_id,
        event_type=event_type,
        subject_type="service_access_request",
        subject_id=request_id,
        result="success" if decision == "approve" else "denied",
        evidence_snapshot={
            "schema_version": "medtrust.service-access-provider-decision/v1",
            "command_request_digest": command_request_digest,
            "request_digest": access_request.request_digest,
            "product_snapshot_digest": access_request.product_snapshot_digest,
            "decision": decision,
            "summary": summary,
            "state_before": state_before,
            "state_after": access_request.status,
            "fulfillment_created": False,
        },
        **command.append_kwargs(),
    )
    return access_request, appended.event


async def decide_service_access_by_operator(
    session: AsyncSession,
    *,
    request_id: UUID,
    actor: AccessActor,
    decision: Decision,
    summary: str,
    raw_key: str,
) -> tuple[ServiceAccessRequest, AuditEvent]:
    access_request = await _locked_request(session, request_id)
    if actor.role != "space_operator":
        raise ServiceAccessError("only the Space operator may make this decision")
    from app.modules.marketplace.services import require_actor

    await require_actor(
        session,
        space_id=access_request.space_id,
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        role_code="space_operator",
    )
    summary = _clean_text(summary, name="decision summary", minimum=5, maximum=1000)
    event_type = (
        "service_access.operator.approved"
        if decision == "approve"
        else "service_access.operator.rejected"
    )
    command = _command(
        actor,
        space_id=access_request.space_id,
        request_id=request_id,
        action="operator-decision",
        raw_key=raw_key,
    )
    command_document = {
        "schema_version": "medtrust.service-access-operator-decision/v1",
        "request_id": str(request_id),
        "request_digest": access_request.request_digest,
        "decision": decision,
        "summary": summary,
    }
    replay, command_request_digest = await begin_audited_command(
        session,
        space_id=access_request.space_id,
        event_type=event_type,
        subject_type="service_access_request",
        command=command,
        request_snapshot=command_document,
        expected_subject_id=request_id,
    )
    if replay is not None:
        return access_request, replay

    state_before = access_request.status
    access_request.status = operator_status_after(state_before, decision)
    access_request.operator_decision = decision
    access_request.operator_decision_summary = summary
    access_request.operator_decided_by = actor.user_id
    access_request.operator_decided_at = datetime.now(timezone.utc)
    access_request.operator_decision_idempotency_digest = command.idempotency_key
    access_request.updated_at = datetime.now(timezone.utc)
    access_request.row_version += 1
    access_request._transition_validated = True
    await session.flush([access_request])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=access_request.space_id,
        event_type=event_type,
        subject_type="service_access_request",
        subject_id=request_id,
        result="success" if decision == "approve" else "denied",
        evidence_snapshot={
            "schema_version": "medtrust.service-access-operator-decision/v1",
            "command_request_digest": command_request_digest,
            "request_digest": access_request.request_digest,
            "product_snapshot_digest": access_request.product_snapshot_digest,
            "decision": decision,
            "summary": summary,
            "state_before": state_before,
            "state_after": access_request.status,
            "contract_created": False,
            "fulfillment_created": False,
        },
        **command.append_kwargs(),
    )
    return access_request, appended.event
