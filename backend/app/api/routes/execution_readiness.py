from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.applications.models import Application
from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.compute.models import (
    Artifact,
    ComputeJob,
    ComputeRun,
    ExecutionEligibilityInvalidation,
    ExecutionEligibilitySnapshot,
)
from app.modules.compute.readiness import (
    ExecutionReadinessError,
    confirm_productized_readiness,
    create_pre_dispatch_job,
    current_readiness,
    request_controlled_dispatch,
    revoke_productized_readiness,
    run_eligibility_check,
)
from app.modules.callback_inbox.models import ExecutionCallbackInboxEntry
from app.modules.contracts.models import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    Policy,
    PolicyConstraint,
)
from app.modules.identity.models import Organization
from app.modules.marketplace.models import (
    ContractModelObject,
    ContractReadinessConfirmation,
    ContractReadinessRevocation,
    ApprovedResultPackage,
    ModelProduct,
    ModelVersion,
    ResultDownloadGrant,
)
from app.modules.catalog.models import DataProduct, DataProductVersion
from app.modules.marketplace.services import MarketplaceServiceError, require_actor


router = APIRouter(tags=["execution-readiness"])
WORKSPACE = Path(__file__).resolve().parents[4]
ROLES = {"space_operator", "data_provider", "model_provider", "data_requester"}


class JobCreateRequest(BaseModel):
    eligibility_snapshot_id: UUID


class ProviderReadinessRequest(BaseModel):
    declaration_accepted: bool
    confirmation_note: str = Field(min_length=3, max_length=240)


class RevokeReadinessRequest(BaseModel):
    reason_code: str = Field(min_length=3, max_length=64)


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(
            status_code=403, detail="Execution readiness command API is disabled"
        )


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    return value.strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _actor(
    session: AsyncSession,
    identity: str,
    required: str | None = None,
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


async def _contract_for_access(
    session: AsyncSession,
    contract_id: UUID,
    actor: DemoActor,
) -> tuple[Contract, ContractRevision]:
    contract = await session.get(Contract, contract_id)
    revision = (
        None
        if contract is None
        else await session.scalar(
            select(ContractRevision)
            .where(ContractRevision.contract_id == contract.id)
            .order_by(ContractRevision.revision_no.desc())
        )
    )
    if contract is None or revision is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    party = await session.scalar(
        select(ContractParty.id).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.organization_id == actor.organization_id,
        )
    )
    if party is None:
        raise HTTPException(
            status_code=403, detail="Contract is outside this organization"
        )
    return contract, revision


async def _payload(
    session: AsyncSession,
    contract: Contract,
    revision: ContractRevision,
) -> dict[str, Any]:
    application = await session.get(Application, contract.application_id)
    data_object = await session.scalar(
        select(ContractObject).where(
            ContractObject.contract_revision_id == revision.id
        )
    )
    model_object = await session.scalar(
        select(ContractModelObject).where(
            ContractModelObject.contract_revision_id == revision.id
        )
    )
    data_version = (
        None
        if data_object is None
        else await session.get(
            DataProductVersion, data_object.data_product_version_id
        )
    )
    data_product = (
        None
        if data_version is None
        else await session.get(DataProduct, data_version.data_product_id)
    )
    model_version = (
        None
        if model_object is None
        else await session.get(ModelVersion, model_object.model_version_id)
    )
    model_product = (
        None
        if model_version is None
        else await session.get(ModelProduct, model_version.model_product_id)
    )
    parties = list(
        (
            await session.execute(
                select(ContractParty, Organization)
                .join(
                    Organization,
                    Organization.id == ContractParty.organization_id,
                )
                .where(ContractParty.contract_revision_id == revision.id)
            )
        ).all()
    )
    readiness: dict[str, Any] = {}
    for readiness_type in ("data_ready", "model_ready", "platform_ready"):
        row = await current_readiness(session, revision.id, readiness_type)
        readiness[readiness_type] = (
            None
            if row is None
            else {
                "id": str(row.id),
                "type": row.readiness_type,
                "target_digest": row.target_digest,
                "evidence_digest": row.evidence_digest,
                "confirmed_at": _iso(row.confirmed_at),
                "responsible_organization_id": str(
                    row.responsible_organization_id
                ),
                "target": row.target_snapshot,
                "evidence": row.evidence_snapshot,
            }
        )
    snapshot = await session.scalar(
        select(ExecutionEligibilitySnapshot)
        .outerjoin(
            ExecutionEligibilityInvalidation,
            ExecutionEligibilityInvalidation.execution_eligibility_snapshot_id
            == ExecutionEligibilitySnapshot.id,
        )
        .where(
            ExecutionEligibilitySnapshot.contract_revision_id == revision.id,
            ExecutionEligibilityInvalidation.id.is_(None),
        )
        .order_by(ExecutionEligibilitySnapshot.created_at.desc())
        .limit(1)
    )
    jobs = list(
        (
            await session.scalars(
                select(ComputeJob)
                .where(ComputeJob.contract_revision_id == revision.id)
                .order_by(ComputeJob.created_at.desc())
            )
        ).all()
    )
    job_rows = []
    for job in jobs:
        run = await session.scalar(
            select(ComputeRun)
            .where(ComputeRun.compute_job_id == job.id)
            .order_by(ComputeRun.attempt_no.desc())
            .limit(1)
        )
        artifact = (
            None
            if run is None
            else await session.scalar(
                select(Artifact).where(Artifact.compute_run_id == run.id)
            )
        )
        callbacks = (
            []
            if run is None
            else list(
                (
                    await session.scalars(
                        select(ExecutionCallbackInboxEntry)
                        .where(
                            ExecutionCallbackInboxEntry.compute_run_id == run.id
                        )
                        .order_by(ExecutionCallbackInboxEntry.occurred_at)
                    )
                ).all()
            )
        )
        package_count = (
            0
            if artifact is None
            else int(
                await session.scalar(
                    select(func.count(ApprovedResultPackage.id)).where(
                        ApprovedResultPackage.artifact_id == artifact.id
                    )
                )
                or 0
            )
        )
        download_grant_count = (
            0
            if artifact is None
            else int(
                await session.scalar(
                    select(func.count(ResultDownloadGrant.id))
                    .join(
                        ApprovedResultPackage,
                        ApprovedResultPackage.id
                        == ResultDownloadGrant.result_package_id,
                    )
                    .where(ApprovedResultPackage.artifact_id == artifact.id)
                )
                or 0
            )
        )
        job_rows.append(
            {
                "id": str(job.id),
                "status": job.status,
                "created_at": _iso(job.created_at),
                "eligibility_snapshot_id": str(
                    job.execution_eligibility_snapshot_id
                )
                if job.execution_eligibility_snapshot_id
                else None,
                "eligibility_snapshot_digest": job.eligibility_snapshot_digest,
                "slot_ordinal": job.pre_dispatch_slot_ordinal,
                "slot_digest": job.pre_dispatch_slot_digest,
                "run_limit": job.run_limit_snapshot,
                "run": None
                if run is None
                else {
                    "id": str(run.id),
                    "status": run.status,
                    "attempt_no": run.attempt_no,
                    "reservation_ordinal": run.reservation_ordinal,
                    "prepared_at": _iso(run.prepared_at),
                    "reserved_at": _iso(run.reserved_at),
                    "dispatched_at": _iso(run.dispatched_at),
                    "started_at": _iso(run.started_at),
                    "finished_at": _iso(run.finished_at),
                    "callbacks": [
                        {
                            "id": str(item.id),
                            "type": item.callback_type,
                            "status": item.status,
                            "occurred_at": _iso(item.occurred_at),
                            "outcome": item.outcome_code,
                            "payload": item.payload_snapshot,
                        }
                        for item in callbacks
                    ],
                },
                "artifact": None
                if artifact is None
                else {
                    "id": str(artifact.id),
                    "status": artifact.release_status,
                    "type": artifact.artifact_type,
                    "content_digest": artifact.content_digest,
                    "size_bytes": artifact.size_bytes,
                    "created_at": _iso(artifact.created_at),
                    "release_package_count": package_count,
                    "download_grant_count": download_grant_count,
                },
                "compute_run_created": run is not None,
                "artifact_created": artifact is not None,
            }
        )
    run_count = await session.scalar(
        select(PolicyConstraint).join(
            Policy, Policy.id == PolicyConstraint.policy_id
        ).where(
            Policy.contract_revision_id == revision.id,
            Policy.action_code == "execute_controlled_compute",
            Policy.effect == "permit",
            PolicyConstraint.constraint_name == "run_count",
        )
    )
    if jobs:
        readiness_state = "job_created"
        next_responsible = "Phase 5.6 dispatcher"
    elif snapshot is not None:
        readiness_state = "eligible"
        next_responsible = "data_requester"
    elif readiness["data_ready"] is None:
        readiness_state = "waiting_for_data_ready"
        next_responsible = "data_provider"
    elif readiness["model_ready"] is None:
        readiness_state = "waiting_for_model_ready"
        next_responsible = "model_provider"
    else:
        readiness_state = "waiting_for_platform_check"
        next_responsible = "space_operator"
    return {
        "contract_id": str(contract.id),
        "contract_number": contract.contract_number,
        "contract_revision_id": str(revision.id),
        "contract_revision_no": revision.revision_no,
        "contract_status": revision.status,
        "contract_digest": revision.content_digest,
        "application_id": str(contract.application_id),
        "application_number": (
            application.application_number if application else None
        ),
        "requester_organization_id": (
            str(application.applicant_organization_id)
            if application
            else None
        ),
        "effective_from": _iso(revision.effective_from),
        "effective_until": _iso(revision.effective_until),
        "run_count": run_count.value if run_count else None,
        "data": {
            "product": data_product.name if data_product else None,
            "version": data_version.version_label if data_version else None,
            "version_id": str(data_version.id) if data_version else None,
            "snapshot_digest": data_object.product_snapshot_digest
            if data_object
            else None,
            "scope": data_object.authorized_scope if data_object else None,
        },
        "model": {
            "product": model_product.name if model_product else None,
            "version": model_version.version_label if model_version else None,
            "version_id": str(model_version.id) if model_version else None,
            "snapshot_digest": model_object.model_snapshot_digest
            if model_object
            else None,
            "model_digest": model_version.model_digest
            if model_version
            else None,
            "entrypoint_id": model_version.entrypoint_id
            if model_version
            else None,
            "runtime": model_version.runtime if model_version else None,
        },
        "parties": [
            {
                "role": party.party_role,
                "organization_id": str(party.organization_id),
                "organization_name": organization.display_name,
            }
            for party, organization in parties
        ],
        "readiness": readiness,
        "readiness_state": readiness_state,
        "next_responsible": next_responsible,
        "blocker_count": 0
        if snapshot is not None
        else sum(
            item is None
            for item in (
                readiness["data_ready"],
                readiness["model_ready"],
                readiness["platform_ready"],
            )
        ),
        "eligibility": (
            None
            if snapshot is None
            else {
                "id": str(snapshot.id),
                "digest": snapshot.eligibility_snapshot_digest,
                "created_at": _iso(snapshot.created_at),
                "valid_until": _iso(snapshot.valid_until),
                "checks": snapshot.check_matrix,
                "environment": snapshot.execution_environment_snapshot,
                "snapshot": snapshot.eligibility_snapshot,
            }
        ),
        "jobs": job_rows,
        "hard_isolation": False,
    }


@router.get("/execution-readiness")
async def readiness_list(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    rows = (
        await session.execute(
            select(Contract, ContractRevision)
            .join(
                ContractRevision,
                ContractRevision.contract_id == Contract.id,
            )
            .join(
                ContractParty,
                ContractParty.contract_revision_id == ContractRevision.id,
            )
            .where(
                ContractParty.organization_id == actor.organization_id,
                ContractRevision.status == "active",
            )
            .order_by(Contract.created_at.desc())
        )
    ).all()
    items = [await _payload(session, contract, revision) for contract, revision in rows]
    return {"items": items, "total": len(items)}


@router.get("/execution-readiness/{contract_id}")
async def readiness_detail(
    contract_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    contract, revision = await _contract_for_access(
        session, contract_id, actor
    )
    return await _payload(session, contract, revision)


async def _confirm_provider(
    contract_id: UUID,
    payload: ProviderReadinessRequest,
    request: Request,
    identity: str,
    idempotency_key: str | None,
    session: AsyncSession,
    readiness_type: str,
    role: str,
):
    _enabled(request)
    if not payload.declaration_accepted:
        raise HTTPException(
            status_code=422, detail="Readiness declaration must be accepted"
        )
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, role)
            contract, revision = await _contract_for_access(
                session, contract_id, actor
            )
            await confirm_productized_readiness(
                session,
                revision,
                readiness_type=readiness_type,
                actor=actor,
                workspace=WORKSPACE,
                raw_key=_key(idempotency_key),
                confirmation_note=payload.confirmation_note,
            )
        return await _payload(session, contract, revision)
    except HTTPException:
        raise
    except ExecutionReadinessError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/execution-readiness/{contract_id}/data-readiness")
async def confirm_data_readiness(
    contract_id: UUID,
    payload: ProviderReadinessRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    session: AsyncSession = Depends(get_db_session),
):
    return await _confirm_provider(
        contract_id,
        payload,
        request,
        identity,
        idempotency_key,
        session,
        "data_ready",
        "data_provider",
    )


@router.post("/execution-readiness/{contract_id}/model-readiness")
async def confirm_model_readiness(
    contract_id: UUID,
    payload: ProviderReadinessRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    session: AsyncSession = Depends(get_db_session),
):
    return await _confirm_provider(
        contract_id,
        payload,
        request,
        identity,
        idempotency_key,
        session,
        "model_ready",
        "model_provider",
    )


@router.post("/execution-readiness/{contract_id}/eligibility-check")
async def eligibility_check(
    contract_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            contract, revision = await _contract_for_access(
                session, contract_id, actor
            )
            snapshot, report = await run_eligibility_check(
                session,
                revision,
                operator=actor,
                raw_key=_key(idempotency_key),
            )
        return {
            "snapshot_id": str(snapshot.id) if snapshot else None,
            "report": report,
            "detail": await _payload(session, contract, revision),
        }
    except HTTPException:
        raise
    except ExecutionReadinessError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/execution-readiness/{contract_id}/jobs",
    status_code=status.HTTP_201_CREATED,
)
async def create_job(
    contract_id: UUID,
    payload: JobCreateRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            contract, revision = await _contract_for_access(
                session, contract_id, actor
            )
            snapshot = await session.get(
                ExecutionEligibilitySnapshot,
                payload.eligibility_snapshot_id,
            )
            if (
                snapshot is None
                or snapshot.contract_revision_id != revision.id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Eligibility snapshot is outside this contract",
                )
            job = await create_pre_dispatch_job(
                session,
                snapshot,
                requester=actor,
                raw_key=_key(idempotency_key),
            )
        return {
            "job_id": str(job.id),
            "status": job.status,
            "slot_ordinal": job.pre_dispatch_slot_ordinal,
            "compute_run_count": 0,
            "artifact_count": 0,
            "detail": await _payload(session, contract, revision),
        }
    except HTTPException:
        raise
    except ExecutionReadinessError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/execution-readiness/jobs/{job_id}/dispatch")
async def dispatch_job(
    job_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            job = await session.get(ComputeJob, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="ComputeJob not found")
            contract, revision = await _contract_for_access(
                session, job.contract_id, actor
            )
            if revision.id != job.contract_revision_id:
                raise HTTPException(
                    status_code=409, detail="ComputeJob contract revision mismatch"
                )
            run, replayed = await request_controlled_dispatch(
                session,
                job,
                operator=actor,
                raw_key=_key(idempotency_key),
            )
        return {
            "job_id": str(job.id),
            "run_id": str(run.id),
            "job_status": job.status,
            "run_status": run.status,
            "replayed": replayed,
            "detail": await _payload(session, contract, revision),
        }
    except HTTPException:
        raise
    except ExecutionReadinessError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/execution-readiness/readiness/{confirmation_id}/revoke")
async def revoke_readiness(
    confirmation_id: UUID,
    payload: RevokeReadinessRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity)
            confirmation = await session.get(
                ContractReadinessConfirmation, confirmation_id
            )
            if confirmation is None:
                raise HTTPException(
                    status_code=404, detail="Readiness confirmation not found"
                )
            contract = await session.scalar(
                select(Contract)
                .join(
                    ContractRevision,
                    ContractRevision.contract_id == Contract.id,
                )
                .where(
                    ContractRevision.id
                    == confirmation.contract_revision_id
                )
            )
            if contract is None:
                raise HTTPException(status_code=404, detail="Contract not found")
            _, revision = await _contract_for_access(
                session, contract.id, actor
            )
            await revoke_productized_readiness(
                session,
                confirmation,
                actor=actor,
                reason_code=payload.reason_code,
                raw_key=_key(idempotency_key),
            )
        return await _payload(session, contract, revision)
    except HTTPException:
        raise
    except ExecutionReadinessError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/execution-readiness/{contract_id}/audit-events")
async def readiness_audit(
    contract_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    contract, revision = await _contract_for_access(
        session, contract_id, actor
    )
    snapshot_ids = select(ExecutionEligibilitySnapshot.id).where(
        ExecutionEligibilitySnapshot.contract_revision_id == revision.id
    )
    job_ids = select(ComputeJob.id).where(
        ComputeJob.contract_revision_id == revision.id
    )
    run_ids = select(ComputeRun.id).join(
        ComputeJob, ComputeJob.id == ComputeRun.compute_job_id
    ).where(ComputeJob.contract_revision_id == revision.id)
    artifact_ids = (
        select(Artifact.id)
        .join(ComputeRun, ComputeRun.id == Artifact.compute_run_id)
        .join(ComputeJob, ComputeJob.id == ComputeRun.compute_job_id)
        .where(ComputeJob.contract_revision_id == revision.id)
    )
    readiness_ids = select(ContractReadinessConfirmation.id).where(
        ContractReadinessConfirmation.contract_revision_id == revision.id
    )
    revocation_ids = (
        select(ContractReadinessRevocation.id)
        .join(
            ContractReadinessConfirmation,
            ContractReadinessConfirmation.id
            == ContractReadinessRevocation.readiness_confirmation_id,
        )
        .where(
            ContractReadinessConfirmation.contract_revision_id == revision.id
        )
    )
    invalidation_ids = (
        select(ExecutionEligibilityInvalidation.id)
        .join(
            ExecutionEligibilitySnapshot,
            ExecutionEligibilitySnapshot.id
            == ExecutionEligibilityInvalidation.execution_eligibility_snapshot_id,
        )
        .where(
            ExecutionEligibilitySnapshot.contract_revision_id == revision.id
        )
    )
    rows = (
        await session.execute(
            select(AuditEvent, OutboxMessage)
            .outerjoin(
                OutboxMessage,
                OutboxMessage.audit_event_id == AuditEvent.event_id,
            )
            .where(
                AuditEvent.space_id == contract.space_id,
                or_(
                    (
                        (AuditEvent.subject_type == "contract_revision")
                        & (AuditEvent.subject_id == revision.id)
                    ),
                    (
                        (AuditEvent.subject_type == "contract_readiness")
                        & AuditEvent.subject_id.in_(readiness_ids)
                    ),
                    (
                        (
                            AuditEvent.subject_type
                            == "contract_readiness_revocation"
                        )
                        & AuditEvent.subject_id.in_(revocation_ids)
                    ),
                    (
                        (AuditEvent.subject_type == "execution_eligibility")
                        & AuditEvent.subject_id.in_(snapshot_ids)
                    ),
                    (
                        (
                            AuditEvent.subject_type
                            == "execution_eligibility_invalidation"
                        )
                        & AuditEvent.subject_id.in_(invalidation_ids)
                    ),
                    (
                        (AuditEvent.subject_type == "compute_job")
                        & AuditEvent.subject_id.in_(job_ids)
                    ),
                    (
                        (AuditEvent.subject_type == "compute_run")
                        & AuditEvent.subject_id.in_(run_ids)
                    ),
                    (
                        (AuditEvent.subject_type == "artifact")
                        & AuditEvent.subject_id.in_(artifact_ids)
                    ),
                ),
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
                "actor_user_id": str(event.actor_user_id)
                if event.actor_user_id
                else None,
                "subject_type": event.subject_type,
                "subject_id": str(event.subject_id),
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
