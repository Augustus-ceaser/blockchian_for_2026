from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.applications.models import Application
from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.contracts.lifecycle import (
    ContractLifecycleError,
    activate_productized_contract,
    confirm_contract,
    generate_contract,
)
from app.modules.contracts.models import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    ContractSignature,
    Policy,
)
from app.modules.contracts.security import validate_contract_security
from app.modules.identity.models import Organization
from app.modules.marketplace.models import ContractModelObject
from app.modules.marketplace.services import MarketplaceServiceError, require_actor


router = APIRouter(tags=["contract-lifecycle"])
ROLES = {"space_operator", "data_provider", "model_provider", "data_requester"}


class ConfirmationRequest(BaseModel):
    contract_revision_id: UUID
    content_digest: str = Field(min_length=71, max_length=71)
    declaration_accepted: bool


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Contract command API is disabled")


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    return value.strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _actor(
    session: AsyncSession, identity: str, required: str | None = None
) -> tuple[Any, DemoActor]:
    if identity not in ROLES or (required is not None and identity != required):
        raise HTTPException(status_code=403, detail="Unknown demo identity")
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


async def _revision_for_access(
    session: AsyncSession, revision_id: UUID, actor: DemoActor
) -> tuple[Contract, ContractRevision]:
    revision = await session.get(ContractRevision, revision_id)
    contract = None if revision is None else await session.get(Contract, revision.contract_id)
    if revision is None or contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    party = await session.scalar(
        select(ContractParty.id).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.organization_id == actor.organization_id,
        )
    )
    if party is None:
        raise HTTPException(status_code=403, detail="Contract is outside this organization")
    return contract, revision


async def _payload(
    session: AsyncSession,
    contract: Contract,
    revision: ContractRevision,
    *,
    include_security: bool = True,
) -> dict[str, Any]:
    application = await session.get(Application, contract.application_id)
    parties = list(
        (
            await session.execute(
                select(ContractParty, Organization)
                .join(Organization, Organization.id == ContractParty.organization_id)
                .where(ContractParty.contract_revision_id == revision.id)
                .order_by(ContractParty.signing_order)
            )
        ).all()
    )
    signatures = {
        item.contract_party_id: item
        for item in (
            await session.scalars(
                select(ContractSignature).where(
                    ContractSignature.contract_revision_id == revision.id
                )
            )
        ).all()
    }
    data_object = await session.scalar(
        select(ContractObject).where(ContractObject.contract_revision_id == revision.id)
    )
    model_object = await session.scalar(
        select(ContractModelObject).where(
            ContractModelObject.contract_revision_id == revision.id
        )
    )
    policies = list(
        (
            await session.scalars(
                select(Policy)
                .where(Policy.contract_revision_id == revision.id)
                .order_by(Policy.priority.desc(), Policy.policy_code)
            )
        ).all()
    )
    for policy in policies:
        await session.refresh(policy, attribute_names=["constraints"])
    security_validation = (
        await validate_contract_security(session, revision, stage="display")
        if include_security
        else None
    )
    return {
        "contract_id": str(contract.id),
        "contract_number": contract.contract_number,
        "application_id": str(contract.application_id),
        "application_number": application.application_number if application else "",
        "application_status": application.status if application else None,
        "revision_id": str(revision.id),
        "revision_no": revision.revision_no,
        "status": revision.status,
        "name": revision.name,
        "summary": revision.summary,
        "content_digest": revision.content_digest,
        "digest_short": revision.content_digest[:19] if revision.content_digest else None,
        "created_at": _iso(revision.created_at),
        "proposed_at": _iso(revision.proposed_at),
        "activated_at": _iso(revision.activated_at),
        "effective_from": _iso(revision.effective_from),
        "effective_until": _iso(revision.effective_until),
        "terms": revision.terms_document,
        "policy_convergence": revision.terms_document.get("policy_convergence", {}),
        "data_object": None
        if data_object is None
        else {
            "version_id": str(data_object.data_product_version_id),
            "name": data_object.product_name_snapshot,
            "snapshot_digest": data_object.product_snapshot_digest,
            "scope": data_object.authorized_scope,
        },
        "model_object": None
        if model_object is None
        else {
            "version_id": str(model_object.model_version_id),
            "name": model_object.model_name_snapshot,
            "snapshot_digest": model_object.model_snapshot_digest,
            "scope": model_object.authorized_scope,
        },
        "parties": [
            {
                "party_id": str(party.id),
                "organization_id": str(party.organization_id),
                "organization_name": organization.display_name,
                "role": party.party_role,
                "signing_order": party.signing_order,
                "required": party.is_required,
                "confirmed": party.id in signatures,
                "confirmed_at": _iso(signatures[party.id].signed_at)
                if party.id in signatures
                else None,
                "confirmed_digest": signatures[party.id].signed_content_digest
                if party.id in signatures
                else None,
            }
            for party, organization in parties
        ],
        "confirmation_progress": {
            "completed": len(signatures),
            "required": sum(party.is_required for party, _ in parties),
        },
        "policies": [
            {
                "code": policy.policy_code,
                "type": policy.policy_type,
                "effect": policy.effect,
                "action": policy.action_code,
                "digest": policy.policy_digest,
                "constraints": [
                    {
                        "name": item.constraint_name,
                        "operator": item.operator,
                        "value": item.value,
                        "unit": item.unit,
                    }
                    for item in policy.constraints
                ],
            }
            for policy in policies
        ],
        "security_validation": security_validation,
        "next_step": "waiting_for_data_and_model_readiness"
        if revision.status == "active"
        else "confirm_current_version",
        "capability": {
            "hard_isolation": False,
            "ca_backed_signature": False,
            "compute_job_creation": False,
            "readiness_implemented": True,
        },
    }


@router.post("/applications/{application_id}/contract")
async def create_contract(
    application_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            application = await session.get(Application, application_id)
            if application is None:
                raise HTTPException(status_code=404, detail="Application not found")
            revision = await generate_contract(
                session, application, actor=actor, raw_key=_key(idempotency_key)
            )
            contract = await session.get(Contract, revision.contract_id)
        return await _payload(session, contract, revision)
    except HTTPException:
        raise
    except ContractLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/digital-contracts")
async def contracts(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    rows = (
        await session.execute(
            select(Contract, ContractRevision)
            .join(ContractRevision, ContractRevision.contract_id == Contract.id)
            .join(ContractParty, ContractParty.contract_revision_id == ContractRevision.id)
            .where(ContractParty.organization_id == actor.organization_id)
            .order_by(Contract.created_at.desc(), ContractRevision.revision_no.desc())
        )
    ).all()
    items = [
        await _payload(session, contract, revision, include_security=False)
        for contract, revision in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/digital-contracts/{contract_id}")
async def contract_detail(
    contract_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    contract = await session.get(Contract, contract_id)
    revision = None if contract is None else await session.scalar(
        select(ContractRevision)
        .where(ContractRevision.contract_id == contract.id)
        .order_by(ContractRevision.revision_no.desc())
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    await _revision_for_access(session, revision.id, actor)
    return await _payload(session, contract, revision)


@router.post("/digital-contracts/{contract_id}/confirm")
async def confirm(
    contract_id: UUID,
    payload: ConfirmationRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity)
            contract, revision = await _revision_for_access(
                session, payload.contract_revision_id, actor
            )
            if contract.id != contract_id:
                raise HTTPException(status_code=409, detail="Contract version mismatch")
            await confirm_contract(
                session,
                revision,
                actor=actor,
                raw_key=_key(idempotency_key),
                acknowledged_digest=payload.content_digest,
                declaration_accepted=payload.declaration_accepted,
            )
        return await _payload(session, contract, revision)
    except HTTPException:
        raise
    except ContractLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/digital-contracts/{contract_id}/activate")
async def activate(
    contract_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            contract = await session.get(Contract, contract_id)
            revision = None if contract is None else await session.scalar(
                select(ContractRevision)
                .where(ContractRevision.contract_id == contract.id)
                .order_by(ContractRevision.revision_no.desc())
            )
            if revision is None:
                raise HTTPException(status_code=404, detail="Contract not found")
            await activate_productized_contract(
                session, revision, actor=actor, raw_key=_key(idempotency_key)
            )
        return await _payload(session, contract, revision)
    except HTTPException:
        raise
    except ContractLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/digital-contracts/{contract_id}/audit-events")
async def contract_audit(
    contract_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    contract = await session.get(Contract, contract_id)
    revision = None if contract is None else await session.scalar(
        select(ContractRevision)
        .where(ContractRevision.contract_id == contract.id)
        .order_by(ContractRevision.revision_no.desc())
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    await _revision_for_access(session, revision.id, actor)
    rows = (
        await session.execute(
            select(AuditEvent, OutboxMessage)
            .outerjoin(OutboxMessage, OutboxMessage.audit_event_id == AuditEvent.event_id)
            .where(
                AuditEvent.subject_type == "contract_revision",
                AuditEvent.subject_id == revision.id,
            )
            .order_by(AuditEvent.stream_sequence)
        )
    ).all()
    return {
        "items": [
            {
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "occurred_at": _iso(event.occurred_at),
                "actor_organization_id": str(event.actor_organization_id)
                if event.actor_organization_id
                else None,
                "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "result": event.result,
                "evidence": event.evidence_snapshot,
                "previous_hash": event.previous_event_digest,
                "current_hash": event.event_digest,
                "outbox_status": outbox.status if outbox else None,
            }
            for event, outbox in rows
        ],
        "total": len(rows),
    }
