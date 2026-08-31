from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo import DEMO_CONTRACT_NUMBER, PATHMNIST_MODEL_DIGEST
from app.modules.applications.models import Application, ApplicationItem
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    OutboxMessage,
    digest_idempotency_key,
)
from app.modules.callback_inbox.models import ExecutionCallbackInboxEntry
from app.modules.catalog.models import (
    DataProduct,
    DataProductVersion,
    DataResource,
)
from app.modules.compute.models import Artifact, ArtifactReview, ComputeJob, ComputeRun
from app.modules.compute.services import (
    ComputeInvariantError,
    create_compute_job,
    prepare_compute_run,
    reserve_compute_run,
    validate_compute_job,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.contracts.models import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    Policy,
)
from app.modules.identity.models import Organization
from app.modules.inbox.models import ConsumerInboxEntry

router = APIRouter(tags=["demo-business"])


class CapabilityBoundary(BaseModel):
    demo: bool = True
    simulated: bool = False
    hard_isolation: bool = False
    clinical_use: bool = False
    artifact_download_enabled: bool = False


class CollectionResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    capability: CapabilityBoundary = Field(default_factory=CapabilityBoundary)


class OverviewResponse(BaseModel):
    space_id: UUID
    counts: dict[str, int]
    latest_run: dict[str, Any] | None
    latest_artifact: dict[str, Any] | None
    verified_baseline_metrics: dict[str, Any]
    outbox: dict[str, int]
    inbox: dict[str, int]
    audit_chain_valid: bool
    capability: CapabilityBoundary = Field(default_factory=CapabilityBoundary)


class DemoRunRequest(BaseModel):
    scenario: Literal["pathmnist_resnet18_20"] = "pathmnist_resnet18_20"


class DemoRunResponse(BaseModel):
    job_id: UUID
    run_id: UUID
    job_status: str
    run_status: str
    replayed: bool
    run_count: dict[str, int]
    status_url: str
    capability: CapabilityBoundary = Field(default_factory=CapabilityBoundary)


async def _demo_contract(
    session: AsyncSession,
) -> tuple[Contract, ContractRevision]:
    row = (
        await session.execute(
            select(Contract, ContractRevision)
            .join(ContractRevision, ContractRevision.contract_id == Contract.id)
            .where(
                Contract.contract_number == DEMO_CONTRACT_NUMBER,
                ContractRevision.status == "active",
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PathMNIST demo baseline is not prepared",
        )
    return row


async def _space_id(session: AsyncSession) -> UUID:
    contract, _ = await _demo_contract(session)
    return contract.space_id


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@router.get("/overview", response_model=OverviewResponse)
async def overview(session: AsyncSession = Depends(get_db_session)) -> OverviewResponse:
    space_id = await _space_id(session)
    counts = {
        "data_products": int(
            await session.scalar(
                select(func.count(DataProduct.id)).where(DataProduct.space_id == space_id)
            )
            or 0
        ),
        "applications": int(
            await session.scalar(
                select(func.count(Application.id)).where(Application.space_id == space_id)
            )
            or 0
        ),
        "contracts": int(
            await session.scalar(
                select(func.count(Contract.id)).where(Contract.space_id == space_id)
            )
            or 0
        ),
        "compute_jobs": int(
            await session.scalar(
                select(func.count(ComputeJob.id)).where(ComputeJob.space_id == space_id)
            )
            or 0
        ),
        "artifacts": int(
            await session.scalar(
                select(func.count(Artifact.id)).where(Artifact.space_id == space_id)
            )
            or 0
        ),
        "audit_events": int(
            await session.scalar(
                select(func.count(AuditEvent.event_id)).where(AuditEvent.space_id == space_id)
            )
            or 0
        ),
    }
    latest_run = await session.scalar(
        select(ComputeRun)
        .where(ComputeRun.space_id == space_id)
        .order_by(ComputeRun.prepared_at.desc())
        .limit(1)
    )
    latest_artifact = await session.scalar(
        select(Artifact)
        .where(Artifact.space_id == space_id)
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    outbox_total = int(
        await session.scalar(
            select(func.count(OutboxMessage.message_id)).where(
                OutboxMessage.space_id == space_id
            )
        )
        or 0
    )
    outbox_published = int(
        await session.scalar(
            select(func.count(OutboxMessage.message_id)).where(
                OutboxMessage.space_id == space_id,
                OutboxMessage.status == "published",
            )
        )
        or 0
    )
    consumer_inbox_total = int(
        await session.scalar(
            select(func.count(ConsumerInboxEntry.id)).where(
                ConsumerInboxEntry.space_id == space_id
            )
        )
        or 0
    )
    consumer_inbox_completed = int(
        await session.scalar(
            select(func.count(ConsumerInboxEntry.id)).where(
                ConsumerInboxEntry.space_id == space_id,
                ConsumerInboxEntry.status == "completed",
            )
        )
        or 0
    )
    callback_inbox_total = int(
        await session.scalar(
            select(func.count(ExecutionCallbackInboxEntry.id)).where(
                ExecutionCallbackInboxEntry.space_id == space_id
            )
        )
        or 0
    )
    callback_inbox_completed = int(
        await session.scalar(
            select(func.count(ExecutionCallbackInboxEntry.id)).where(
                ExecutionCallbackInboxEntry.space_id == space_id,
                ExecutionCallbackInboxEntry.status == "completed",
            )
        )
        or 0
    )
    chain_check = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": space_id},
        )
    ).one()
    return OverviewResponse(
        space_id=space_id,
        counts=counts,
        latest_run=(
            {
                "id": str(latest_run.id),
                "status": latest_run.status,
                "attempt_no": latest_run.attempt_no,
                "prepared_at": _iso(latest_run.prepared_at),
            }
            if latest_run
            else None
        ),
        latest_artifact=(
            {
                "id": str(latest_artifact.id),
                "status": latest_artifact.release_status,
                "artifact_type": latest_artifact.artifact_type,
                "created_at": _iso(latest_artifact.created_at),
            }
            if latest_artifact
            else None
        ),
        verified_baseline_metrics={
            "source": "verified_release_baseline",
            "sample_count": 20,
            "accuracy": "0.95",
            "mean_confidence": "0.960102856159",
            "artifact_status": "quarantined",
        },
        outbox={"total": outbox_total, "published": outbox_published},
        inbox={
            "consumer_total": consumer_inbox_total,
            "consumer_completed": consumer_inbox_completed,
            "callback_total": callback_inbox_total,
            "callback_completed": callback_inbox_completed,
        },
        audit_chain_valid=bool(chain_check.is_valid),
    )


@router.get("/data-products", response_model=CollectionResponse)
async def data_products(session: AsyncSession = Depends(get_db_session)) -> CollectionResponse:
    space_id = await _space_id(session)
    rows = (
        await session.execute(
            select(DataProduct, Organization, DataProductVersion)
            .join(Organization, Organization.id == DataProduct.provider_organization_id)
            .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
            .where(DataProduct.space_id == space_id, DataProduct.is_demo.is_(True))
            .order_by(DataProduct.created_at.desc(), DataProductVersion.version_no.desc())
        )
    ).all()
    seen: set[UUID] = set()
    items: list[dict[str, Any]] = []
    for product, provider, version in rows:
        if product.id in seen:
            continue
        seen.add(product.id)
        items.append(
            {
                "id": str(product.id),
                "name": product.name,
                "provider": provider.display_name,
                "description": product.description,
                "domain": product.domain,
                "status": product.lifecycle_status,
                "version_id": str(version.id),
                "version": version.version_label,
                "version_status": version.status,
                "use_mode": version.default_use_mode,
                "classification": version.classification_level,
                "is_demo": product.is_demo,
            }
        )
    return CollectionResponse(items=items, total=len(items))


@router.get("/data-products/{product_id}")
async def data_product_detail(
    product_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    space_id = await _space_id(session)
    product = await session.get(DataProduct, product_id)
    if product is None or product.space_id != space_id or not product.is_demo:
        raise HTTPException(status_code=404, detail="Data product not found")
    provider = await session.get(Organization, product.provider_organization_id)
    versions = list(
        (
            await session.scalars(
                select(DataProductVersion)
                .where(DataProductVersion.data_product_id == product.id)
                .order_by(DataProductVersion.version_no.desc())
            )
        ).all()
    )
    resources = []
    if versions:
        resources = list(
            (
                await session.scalars(
                    select(DataResource)
                    .where(DataResource.data_product_version_id == versions[0].id)
                    .order_by(DataResource.position_no)
                )
            ).all()
        )
    return {
        "id": str(product.id),
        "name": product.name,
        "provider": provider.display_name if provider else "演示机构",
        "description": product.description,
        "domain": product.domain,
        "status": product.lifecycle_status,
        "versions": [
            {
                "id": str(item.id),
                "label": item.version_label,
                "status": item.status,
                "summary": item.content_summary,
                "quality_report": item.quality_report,
                "policy": item.default_policy_template,
                "snapshot_digest": item.snapshot_digest,
            }
            for item in versions
        ],
        "resources": [
            {
                "id": str(item.id),
                "name": item.name,
                "type": item.resource_type,
                "modality": item.modality,
                "format": item.format,
                "scope": item.scope_metadata,
                "quality": item.quality_report,
            }
            for item in resources
        ],
        "capability": CapabilityBoundary().model_dump(),
    }


@router.get("/applications", response_model=CollectionResponse)
async def applications(session: AsyncSession = Depends(get_db_session)) -> CollectionResponse:
    space_id = await _space_id(session)
    rows = list(
        (
            await session.scalars(
                select(Application)
                .where(Application.space_id == space_id, Application.is_demo.is_(True))
                .order_by(Application.created_at.desc())
            )
        ).all()
    )
    items = []
    for item in rows:
        product_names = list(
            (
                await session.scalars(
                    select(DataProduct.name)
                    .join(ApplicationItem, ApplicationItem.data_product_id == DataProduct.id)
                    .where(ApplicationItem.application_id == item.id)
                )
            ).all()
        )
        items.append(
            {
                "id": str(item.id),
                "number": item.application_number,
                "purpose": item.purpose,
                "status": item.status,
                "algorithm": f"{item.algorithm_name} {item.algorithm_version}",
                "products": product_names,
                "requested_run_limit": item.requested_run_limit,
                "submitted_at": _iso(item.submitted_at),
            }
        )
    return CollectionResponse(items=items, total=len(items))


async def _contract_items(session: AsyncSession, space_id: UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Contract, ContractRevision)
            .join(ContractRevision, ContractRevision.contract_id == Contract.id)
            .where(Contract.space_id == space_id, Contract.is_demo.is_(True))
            .order_by(Contract.created_at.desc(), ContractRevision.revision_no.desc())
        )
    ).all()
    return [
        {
            "id": str(contract.id),
            "number": contract.contract_number,
            "revision_id": str(revision.id),
            "revision_no": revision.revision_no,
            "name": revision.name,
            "status": revision.status,
            "summary": revision.summary,
            "activated_at": _iso(revision.activated_at),
            "eligibility_digest": contract.eligibility_digest,
        }
        for contract, revision in rows
    ]


@router.get("/contracts", response_model=CollectionResponse)
async def contracts(session: AsyncSession = Depends(get_db_session)) -> CollectionResponse:
    items = await _contract_items(session, await _space_id(session))
    return CollectionResponse(items=items, total=len(items))


@router.get("/contracts/{contract_id}")
async def contract_detail(
    contract_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    space_id = await _space_id(session)
    contract = await session.get(Contract, contract_id)
    if contract is None or contract.space_id != space_id or not contract.is_demo:
        raise HTTPException(status_code=404, detail="Contract not found")
    revision = await session.scalar(
        select(ContractRevision)
        .where(ContractRevision.contract_id == contract.id)
        .order_by(ContractRevision.revision_no.desc())
        .limit(1)
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Contract revision not found")
    parties = list(
        (
            await session.execute(
                select(ContractParty, Organization)
                .join(Organization, Organization.id == ContractParty.organization_id)
                .where(ContractParty.contract_revision_id == revision.id)
            )
        ).all()
    )
    objects = list(
        (
            await session.scalars(
                select(ContractObject).where(
                    ContractObject.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    policies = list(
        (
            await session.scalars(
                select(Policy)
                .where(Policy.contract_revision_id == revision.id)
                .order_by(Policy.priority)
            )
        ).all()
    )
    return {
        "id": str(contract.id),
        "number": contract.contract_number,
        "status": revision.status,
        "revision": revision.revision_no,
        "name": revision.name,
        "summary": revision.summary,
        "content_digest": revision.content_digest,
        "activated_at": _iso(revision.activated_at),
        "parties": [
            {"role": party.party_role, "organization": organization.display_name}
            for party, organization in parties
        ],
        "objects": [
            {
                "name": item.product_name_snapshot,
                "version_id": str(item.data_product_version_id),
                "scope": item.authorized_scope,
            }
            for item in objects
        ],
        "policies": [
            {
                "code": item.policy_code,
                "effect": item.effect,
                "action": item.action_code,
                "digest": item.policy_digest,
            }
            for item in policies
        ],
        "capability": CapabilityBoundary().model_dump(),
    }


@router.get("/compute-jobs", response_model=CollectionResponse)
async def compute_jobs(session: AsyncSession = Depends(get_db_session)) -> CollectionResponse:
    space_id = await _space_id(session)
    jobs = list(
        (
            await session.scalars(
                select(ComputeJob)
                .where(ComputeJob.space_id == space_id)
                .order_by(ComputeJob.created_at.desc())
            )
        ).all()
    )
    items = []
    for job in jobs:
        run = await session.scalar(
            select(ComputeRun)
            .where(ComputeRun.compute_job_id == job.id)
            .order_by(ComputeRun.attempt_no.desc())
            .limit(1)
        )
        items.append(
            {
                "id": str(job.id),
                "status": job.status,
                "purpose": job.purpose_code,
                "algorithm": job.algorithm_spec_snapshot.get("algorithm_name"),
                "entrypoint": job.algorithm_spec_snapshot.get("entrypoint_id"),
                "created_at": _iso(job.created_at),
                "run": (
                    {
                        "id": str(run.id),
                        "status": run.status,
                        "attempt_no": run.attempt_no,
                        "reservation_ordinal": run.reservation_ordinal,
                        "run_limit": run.run_limit_snapshot,
                    }
                    if run
                    else None
                ),
            }
        )
    return CollectionResponse(items=items, total=len(items))


@router.get("/compute-runs/{run_id}")
async def compute_run_detail(
    run_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    space_id = await _space_id(session)
    run = await session.get(ComputeRun, run_id)
    if run is None or run.space_id != space_id:
        raise HTTPException(status_code=404, detail="Compute run not found")
    callbacks = list(
        (
            await session.scalars(
                select(ExecutionCallbackInboxEntry)
                .where(ExecutionCallbackInboxEntry.compute_run_id == run.id)
                .order_by(ExecutionCallbackInboxEntry.occurred_at)
            )
        ).all()
    )
    artifact = await session.scalar(
        select(Artifact).where(Artifact.compute_run_id == run.id)
    )
    return {
        "id": str(run.id),
        "job_id": str(run.compute_job_id),
        "status": run.status,
        "attempt_no": run.attempt_no,
        "reservation_ordinal": run.reservation_ordinal,
        "run_limit": run.run_limit_snapshot,
        "prepared_at": _iso(run.prepared_at),
        "reserved_at": _iso(run.reserved_at),
        "dispatched_at": _iso(run.dispatched_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "callbacks": [
            {
                "type": item.callback_type,
                "status": item.status,
                "occurred_at": _iso(item.occurred_at),
                "outcome": item.outcome_code,
            }
            for item in callbacks
        ],
        "artifact_id": str(artifact.id) if artifact else None,
        "artifact_status": artifact.release_status if artifact else None,
        "capability": CapabilityBoundary().model_dump(),
    }


@router.get("/artifacts/{artifact_id}")
async def artifact_detail(
    artifact_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    space_id = await _space_id(session)
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.space_id != space_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    review = await session.scalar(
        select(ArtifactReview).where(ArtifactReview.artifact_id == artifact.id)
    )
    return {
        "id": str(artifact.id),
        "run_id": str(artifact.compute_run_id),
        "type": artifact.artifact_type,
        "content_digest": artifact.content_digest,
        "size_bytes": artifact.size_bytes,
        "classification": artifact.classification_level,
        "status": artifact.release_status,
        "created_at": _iso(artifact.created_at),
        "review": (
            {
                "status": review.status,
                "decision": review.decision,
                "decided_at": _iso(review.decided_at),
            }
            if review
            else None
        ),
        "download_url": None,
        "capability": CapabilityBoundary().model_dump(),
    }


@router.get("/audit-events", response_model=CollectionResponse)
async def audit_events(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> CollectionResponse:
    space_id = await _space_id(session)
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.space_id == space_id)
                .order_by(AuditEvent.stream_sequence.desc())
                .limit(limit)
            )
        ).all()
    )
    items = [
        {
            "id": str(item.event_id),
            "sequence": item.stream_sequence,
            "event_type": item.event_type,
            "actor_type": item.actor_type,
            "actor": item.actor_service_code or "space participant",
            "subject_type": item.subject_type,
            "subject_id": str(item.subject_id),
            "result": item.result,
            "occurred_at": _iso(item.occurred_at),
            "event_digest": item.event_digest,
            "previous_event_digest": item.previous_event_digest,
        }
        for item in events
    ]
    return CollectionResponse(items=items, total=len(items))


@router.get("/connectors", response_model=CollectionResponse)
async def connectors(session: AsyncSession = Depends(get_db_session)) -> CollectionResponse:
    space_id = await _space_id(session)
    rows = (
        await session.execute(
            select(Connector, Organization)
            .join(Organization, Organization.id == Connector.owner_organization_id)
            .where(Connector.space_id == space_id)
            .order_by(Connector.created_at)
        )
    ).all()
    items = []
    for connector, organization in rows:
        capabilities = list(
            (
                await session.scalars(
                    select(ConnectorCapability).where(
                        ConnectorCapability.connector_id == connector.id
                    )
                )
            ).all()
        )
        items.append(
            {
                "id": str(connector.id),
                "name": connector.name,
                "organization": organization.display_name,
                "verification_status": connector.verification_status,
                "runtime_status": connector.runtime_status,
                "last_heartbeat_at": _iso(connector.last_heartbeat_at),
                "capabilities": [
                    {
                        "code": item.capability_code,
                        "version": item.capability_version,
                        "status": item.status,
                    }
                    for item in capabilities
                ],
            }
        )
    return CollectionResponse(items=items, total=len(items))


def _command_context(idempotency_key: str, event: str) -> AuditCommandContext:
    command_id = uuid5(
        NAMESPACE_URL, f"medtrust:api:pathmnist:{event}:{idempotency_key}"
    )
    return AuditCommandContext(
        command_id=command_id,
        idempotency_key=digest_idempotency_key(
            f"api:pathmnist:{event}:{idempotency_key}"
        ),
        correlation_id=command_id,
        actor_type="system",
        actor_service_code="medtrust.compute",
    )


@router.post(
    "/demo/pathmnist/runs",
    response_model=DemoRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_pathmnist_run(
    payload: DemoRunRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    demo_role: str = Header(alias="X-Demo-Role"),
    session: AsyncSession = Depends(get_db_session),
) -> DemoRunResponse:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Demo command API is disabled")
    if demo_role != "ai_company":
        raise HTTPException(status_code=403, detail="AI enterprise demo role required")
    del payload
    try:
        async with session.begin():
            contract, revision = await _demo_contract(session)
            contract_object = await session.scalar(
                select(ContractObject).where(
                    ContractObject.contract_revision_id == revision.id
                )
            )
            consumer = await session.scalar(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id,
                    ContractParty.party_role == "consumer",
                )
            )
            if contract_object is None or consumer is None:
                raise ComputeInvariantError("demo Contract graph is incomplete")
            algorithm_spec = {
                "schema_version": "algorithm-spec/v1",
                "algorithm_name": "PathMNIST official ResNet-18 28px",
                "algorithm_version": "1",
                "algorithm_digest": PATHMNIST_MODEL_DIGEST,
                "registration_digest": (
                    "sha256:cde1049d2777ce5d05fc6dfbe3cd03ecaea4890bb055abe7f5f46b80c4b29736"
                ),
                "entrypoint_id": "pathmnist_resnet18_v1",
                "execution_profile": "local_builtin_cpu_inference",
                "declared_output_types": ["model_artifact"],
                # A successful ComputeJob is terminal. A new demo invocation
                # therefore creates a new fixed intent without exposing the
                # caller's raw idempotency key or changing the allowlisted
                # algorithm digest.
                "demo_invocation_digest": digest_idempotency_key(
                    f"api:pathmnist:invocation:{idempotency_key}"
                ),
            }
            job = await create_compute_job(
                session,
                revision_id=revision.id,
                party_id=consumer.id,
                contract_object_id=contract_object.id,
                requester_organization_id=consumer.organization_id,
                requester_user_id=revision.created_by,
                purpose_code="model_validation",
                requested_output_types=["model_artifact"],
                algorithm_spec_snapshot=algorithm_spec,
                audit_command=_command_context(idempotency_key, "job"),
            )

            run_command = _command_context(idempotency_key, "run")
            reservation_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.space_id == contract.space_id,
                    AuditEvent.event_type == "compute.run.reserved",
                    AuditEvent.command_id == run_command.command_id,
                )
            )
            replayed = reservation_event is not None
            if reservation_event is None:
                await validate_compute_job(session, job)
                run = await prepare_compute_run(session, job, created_by=revision.created_by)
                await reserve_compute_run(
                    session,
                    run,
                    audit_command=run_command,
                )
            else:
                run = await session.get(ComputeRun, reservation_event.subject_id)
                if run is None or run.compute_job_id != job.id:
                    raise ComputeInvariantError(
                        "idempotent Run reservation subject is unavailable"
                    )
    except ComputeInvariantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DemoRunResponse(
        job_id=job.id,
        run_id=run.id,
        job_status=job.status,
        run_status=run.status,
        replayed=replayed,
        run_count={
            "ordinal": int(run.reservation_ordinal or 0),
            "limit": int(run.run_limit_snapshot or 0),
        },
        status_url=f"/api/v1/compute-runs/{run.id}",
    )
