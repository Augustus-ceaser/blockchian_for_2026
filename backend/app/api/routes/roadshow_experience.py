from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import (
    PHASE4_APPLICATION_NUMBER,
    DemoActor,
    get_phase4_context,
)
from app.modules.applications.models import Application, ApplicationItem
from app.modules.audit.models import AuditEvent
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
)
from app.modules.compute.models import (
    Artifact,
    ComputeJob,
    ComputeRun,
    ExecutionEligibilitySnapshot,
)
from app.modules.contracts.models import (
    Contract,
    ContractParty,
    ContractRevision,
    ContractSignature,
)
from app.modules.identity.models import Organization
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ApprovedResultPackage,
    ArtifactReviewDecision,
    ArtifactReviewTask,
    ContractReadinessConfirmation,
    ModelProduct,
    ModelPublication,
    ModelVersion,
    ResultDownloadGrant,
)
from app.modules.marketplace.services import MarketplaceServiceError, require_actor
from app.modules.reviews.models import ReviewDecision, ReviewTask


router = APIRouter(prefix="/roadshow-experience", tags=["roadshow-experience"])
ROLES = {"space_operator", "data_provider", "model_provider", "data_requester"}
WORKSPACE = Path(__file__).resolve().parents[4]
RUNTIME_FILE = WORKSPACE / ".runtime" / "phase4-demo-processes.json"

APPLICATION_REVIEW_ROLE = {
    "application_precheck": "space_operator",
    "data_provider_review": "data_provider",
    "model_provider_review": "model_provider",
}
ARTIFACT_REVIEW_ROLE = {
    "data_provider_egress_review": "data_provider",
    "model_provider_quality_review": "model_provider",
    "platform_compliance_review": "space_operator",
}
PARTY_ROLE = {
    "operator_witness": "space_operator",
    "data_provider": "data_provider",
    "model_provider": "model_provider",
    "data_requester": "data_requester",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _short_digest(value: str | None) -> str | None:
    if not value:
        return None
    return value if len(value) <= 24 else f"{value[:18]}..."


def _process_running(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def _actor(session: AsyncSession, identity: str) -> tuple[Any, DemoActor]:
    if identity not in ROLES:
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


async def _application_for_access(
    session: AsyncSession,
    application_id: UUID,
    actor: DemoActor,
) -> Application:
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if actor.role == "space_operator":
        return application
    if actor.role == "data_requester":
        allowed = application.applicant_organization_id == actor.organization_id
    elif actor.role == "data_provider":
        allowed = (
            application.status != "draft"
            and application.provider_organization_id == actor.organization_id
        )
    else:
        allowed = application.status != "draft" and bool(
            await session.scalar(
                select(ApplicationModelSelection.id).where(
                    ApplicationModelSelection.application_id == application.id,
                    ApplicationModelSelection.model_provider_organization_id
                    == actor.organization_id,
                )
            )
        )
    if not allowed:
        raise HTTPException(
            status_code=403, detail="Application is outside this organization"
        )
    return application


async def _chain_rows(
    session: AsyncSession,
    context: Any,
    actor: DemoActor,
) -> list[Application]:
    query = (
        select(Application)
        .where(
            Application.space_id == context.space_id,
            Application.application_number != PHASE4_APPLICATION_NUMBER,
        )
        .order_by(Application.updated_at.desc(), Application.created_at.desc())
    )
    if actor.role == "data_requester":
        query = query.where(
            Application.applicant_organization_id == actor.organization_id
        )
    elif actor.role == "data_provider":
        query = query.where(
            Application.provider_organization_id == actor.organization_id,
            Application.status != "draft",
        )
    elif actor.role == "model_provider":
        query = query.join(
            ApplicationModelSelection,
            ApplicationModelSelection.application_id == Application.id,
        ).where(
            ApplicationModelSelection.model_provider_organization_id
            == actor.organization_id,
            Application.status != "draft",
        )
    return list((await session.scalars(query)).unique().all())


def _node(
    key: str,
    label: str,
    *,
    object_id: UUID | None,
    number: str | None,
    status: str,
    complete: bool,
    responsible_role: str | None,
    href: str | None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "object_id": str(object_id) if object_id else None,
        "number": number,
        "status": status,
        "complete": complete,
        "responsible_role": responsible_role,
        "href": href,
    }


async def _project_chain(
    session: AsyncSession,
    application: Application,
) -> tuple[dict[str, Any], set[UUID]]:
    subject_ids: set[UUID] = {application.id}
    item = await session.scalar(
        select(ApplicationItem)
        .where(ApplicationItem.application_id == application.id)
        .order_by(ApplicationItem.position_no)
    )
    data_version = (
        None
        if item is None
        else await session.get(DataProductVersion, item.data_product_version_id)
    )
    data_product = (
        None
        if data_version is None
        else await session.get(DataProduct, data_version.data_product_id)
    )
    selection = await session.scalar(
        select(ApplicationModelSelection).where(
            ApplicationModelSelection.application_id == application.id
        )
    )
    model_version = (
        None
        if selection is None
        else await session.get(ModelVersion, selection.model_version_id)
    )
    model_product = (
        None
        if model_version is None
        else await session.get(ModelProduct, model_version.model_product_id)
    )
    data_published = bool(
        data_version
        and await session.scalar(
            select(DataProductPublication.id).where(
                DataProductPublication.data_product_version_id == data_version.id,
                DataProductPublication.status == "active",
            )
        )
    )
    model_published = bool(
        model_version
        and await session.scalar(
            select(ModelPublication.id).where(
                ModelPublication.model_version_id == model_version.id,
                ModelPublication.status == "active",
            )
        )
    )
    for value in (data_product, data_version, model_product, model_version):
        if value is not None:
            subject_ids.add(value.id)

    review_tasks = list(
        (
            await session.scalars(
                select(ReviewTask)
                .where(ReviewTask.application_id == application.id)
                .order_by(ReviewTask.sequence_no)
            )
        ).all()
    )
    review_decisions = list(
        (
            await session.scalars(
                select(ReviewDecision).where(
                    ReviewDecision.review_task_id.in_(
                        [task.id for task in review_tasks] or [UUID(int=0)]
                    )
                )
            )
        ).all()
    )
    subject_ids.update(task.id for task in review_tasks)
    subject_ids.update(decision.id for decision in review_decisions)

    contract = await session.scalar(
        select(Contract).where(Contract.application_id == application.id)
    )
    revision = (
        None
        if contract is None
        else await session.scalar(
            select(ContractRevision)
            .where(ContractRevision.contract_id == contract.id)
            .order_by(ContractRevision.revision_no.desc())
        )
    )
    parties: list[ContractParty] = []
    signatures: list[ContractSignature] = []
    readiness: list[ContractReadinessConfirmation] = []
    eligibility = None
    if contract is not None:
        subject_ids.add(contract.id)
    if revision is not None:
        subject_ids.add(revision.id)
        parties = list(
            (
                await session.scalars(
                    select(ContractParty)
                    .where(ContractParty.contract_revision_id == revision.id)
                    .order_by(ContractParty.signing_order)
                )
            ).all()
        )
        signatures = list(
            (
                await session.scalars(
                    select(ContractSignature).where(
                        ContractSignature.contract_revision_id == revision.id
                    )
                )
            ).all()
        )
        readiness = list(
            (
                await session.scalars(
                    select(ContractReadinessConfirmation)
                    .where(
                        ContractReadinessConfirmation.contract_revision_id
                        == revision.id
                    )
                    .order_by(ContractReadinessConfirmation.confirmed_at.desc())
                )
            ).all()
        )
        eligibility = await session.scalar(
            select(ExecutionEligibilitySnapshot)
            .where(ExecutionEligibilitySnapshot.contract_revision_id == revision.id)
            .order_by(ExecutionEligibilitySnapshot.created_at.desc())
        )
    subject_ids.update(item.id for item in parties)
    subject_ids.update(item.id for item in signatures)
    subject_ids.update(item.id for item in readiness)
    if eligibility is not None:
        subject_ids.add(eligibility.id)

    jobs = (
        []
        if revision is None
        else list(
            (
                await session.scalars(
                    select(ComputeJob)
                    .where(ComputeJob.contract_revision_id == revision.id)
                    .order_by(ComputeJob.created_at.desc())
                )
            ).all()
        )
    )
    job = jobs[0] if jobs else None
    runs = (
        []
        if job is None
        else list(
            (
                await session.scalars(
                    select(ComputeRun)
                    .where(ComputeRun.compute_job_id == job.id)
                    .order_by(ComputeRun.attempt_no.desc())
                )
            ).all()
        )
    )
    run = runs[0] if runs else None
    artifact = (
        None
        if run is None
        else await session.scalar(
            select(Artifact)
            .where(Artifact.compute_run_id == run.id)
            .order_by(Artifact.artifact_no.desc())
        )
    )
    subject_ids.update(item.id for item in jobs)
    subject_ids.update(item.id for item in runs)
    if artifact is not None:
        subject_ids.add(artifact.id)

    artifact_tasks = (
        []
        if artifact is None
        else list(
            (
                await session.scalars(
                    select(ArtifactReviewTask)
                    .where(ArtifactReviewTask.artifact_id == artifact.id)
                    .order_by(ArtifactReviewTask.created_at)
                )
            ).all()
        )
    )
    artifact_decisions = list(
        (
            await session.scalars(
                select(ArtifactReviewDecision).where(
                    ArtifactReviewDecision.artifact_review_task_id.in_(
                        [task.id for task in artifact_tasks] or [UUID(int=0)]
                    )
                )
            )
        ).all()
    )
    package = (
        None
        if artifact is None
        else await session.scalar(
            select(ApprovedResultPackage).where(
                ApprovedResultPackage.artifact_id == artifact.id
            )
        )
    )
    grants = (
        []
        if package is None
        else list(
            (
                await session.scalars(
                    select(ResultDownloadGrant)
                    .where(ResultDownloadGrant.result_package_id == package.id)
                    .order_by(ResultDownloadGrant.created_at.desc())
                )
            ).all()
        )
    )
    grant = grants[0] if grants else None
    subject_ids.update(task.id for task in artifact_tasks)
    subject_ids.update(decision.id for decision in artifact_decisions)
    if package is not None:
        subject_ids.add(package.id)
    subject_ids.update(item.id for item in grants)

    readiness_types = {item.readiness_type for item in readiness}
    signature_party_ids = {item.contract_party_id for item in signatures}
    open_application_review = next(
        (task for task in review_tasks if task.task_status in {"pending", "claimed"}),
        None,
    )
    open_artifact_review = next(
        (
            task
            for task in artifact_tasks
            if task.status in {"pending", "claimed"}
        ),
        None,
    )
    unsigned_party = next(
        (party for party in parties if party.id not in signature_party_ids),
        None,
    )

    if application.status == "draft":
        next_role, next_action = "data_requester", "完善并提交计算需求"
    elif open_application_review is not None:
        next_role = APPLICATION_REVIEW_ROLE.get(open_application_review.review_type)
        next_action = "完成当前申请审核"
    elif application.status != "approved":
        next_role, next_action = None, "申请已结束"
    elif revision is None:
        next_role, next_action = "space_operator", "生成数字合约"
    elif unsigned_party is not None and revision.status != "active":
        next_role = PARTY_ROLE.get(unsigned_party.party_role)
        next_action = "确认当前合约版本"
    elif revision.status != "active":
        next_role, next_action = "space_operator", "激活已完成确认的合约"
    elif "data_ready" not in readiness_types:
        next_role, next_action = "data_provider", "确认数据资产就绪"
    elif "model_ready" not in readiness_types:
        next_role, next_action = "model_provider", "确认模型资产就绪"
    elif "platform_ready" not in readiness_types or eligibility is None:
        next_role, next_action = "space_operator", "完成平台资格检查"
    elif job is None:
        next_role, next_action = "data_requester", "创建受控计算任务"
    elif run is None:
        next_role, next_action = "space_operator", "派发受控计算任务"
    elif run.status not in {"succeeded", "failed", "interrupted", "cancelled"}:
        next_role, next_action = "space_operator", "等待受控执行完成"
    elif run.status != "succeeded":
        next_role, next_action = "space_operator", "检查失败运行证据"
    elif artifact is None:
        next_role, next_action = "space_operator", "等待 Callback 生成隔离结果"
    elif open_artifact_review is not None:
        next_role = ARTIFACT_REVIEW_ROLE.get(open_artifact_review.review_type)
        next_action = "完成结果出域审核"
    elif package is None:
        next_role, next_action = "space_operator", "生成白名单安全结果包"
    elif grant is None:
        next_role, next_action = "data_requester", "申请一次性下载授权"
    elif grant.status == "active":
        next_role, next_action = "data_requester", "下载安全结果包"
    else:
        next_role, next_action = None, "全链路已完成"

    artifact_approved = bool(artifact_tasks) and all(
        task.status == "decided" for task in artifact_tasks
    )
    nodes = [
        _node(
            "data_product",
            "数据产品",
            object_id=data_version.id if data_version else None,
            number=data_product.product_code if data_product else None,
            status="published" if data_published else data_version.status if data_version else "missing",
            complete=data_published,
            responsible_role="data_provider",
            href=f"/data-products/{data_version.id}" if data_version else None,
        ),
        _node(
            "model_product",
            "模型产品",
            object_id=model_version.id if model_version else None,
            number=model_product.product_code if model_product else None,
            status="published" if model_published else model_version.status if model_version else "missing",
            complete=model_published,
            responsible_role="model_provider",
            href=f"/model-products/{model_version.id}" if model_version else None,
        ),
        _node(
            "application",
            "计算申请",
            object_id=application.id,
            number=application.application_number,
            status=application.status,
            complete=application.status == "approved",
            responsible_role=next_role if open_application_review else "data_requester",
            href=f"/applications/{application.id}",
        ),
        _node(
            "contract",
            "数字合约",
            object_id=contract.id if contract else None,
            number=contract.contract_number if contract else None,
            status=revision.status if revision else "not_created",
            complete=bool(revision and revision.status == "active"),
            responsible_role=next_role if revision and revision.status != "active" else "space_operator",
            href=f"/contracts/{contract.id}" if contract else None,
        ),
        _node(
            "readiness",
            "执行准备",
            object_id=eligibility.id if eligibility else None,
            number=f"{len(readiness_types)}/3 ready",
            status="eligible" if eligibility else "checking",
            complete=eligibility is not None,
            responsible_role=next_role if eligibility is None else "space_operator",
            href=f"/execution/{contract.id}" if contract else None,
        ),
        _node(
            "job",
            "计算任务",
            object_id=job.id if job else None,
            number=str(job.id)[:8] if job else None,
            status=job.status if job else "not_created",
            complete=job is not None,
            responsible_role="data_requester",
            href=f"/execution/{contract.id}" if contract else None,
        ),
        _node(
            "run",
            "受控运行",
            object_id=run.id if run else None,
            number=f"attempt {run.attempt_no}" if run else None,
            status=run.status if run else "not_started",
            complete=bool(run and run.status == "succeeded"),
            responsible_role="space_operator",
            href=f"/execution/{contract.id}" if contract else None,
        ),
        _node(
            "artifact",
            "隔离结果",
            object_id=artifact.id if artifact else None,
            number=str(artifact.id)[:8] if artifact else None,
            status=artifact.release_status if artifact else "not_created",
            complete=artifact is not None,
            responsible_role="space_operator",
            href=f"/results/{artifact.id}" if artifact else None,
        ),
        _node(
            "result_review",
            "多方审核",
            object_id=artifact.id if artifact else None,
            number=f"{sum(task.status == 'decided' for task in artifact_tasks)}/{len(artifact_tasks)}",
            status="approved" if artifact_approved else "pending",
            complete=artifact_approved,
            responsible_role=next_role if open_artifact_review else "space_operator",
            href=f"/results/{artifact.id}" if artifact else None,
        ),
        _node(
            "package",
            "安全结果包",
            object_id=package.id if package else None,
            number=str(package.id)[:8] if package else None,
            status=package.status if package else "not_created",
            complete=bool(package and package.status == "available"),
            responsible_role="space_operator",
            href=f"/results/{artifact.id}" if artifact else None,
        ),
        _node(
            "download",
            "一次性下载",
            object_id=grant.id if grant else None,
            number=f"{grant.download_count}/{grant.max_downloads}" if grant else None,
            status=grant.status if grant else "not_granted",
            complete=bool(grant and grant.status == "exhausted"),
            responsible_role="data_requester",
            href=f"/results/{artifact.id}" if artifact else None,
        ),
        _node(
            "audit",
            "审计完成",
            object_id=application.id,
            number="hash chain",
            status="verified",
            complete=bool(grant and grant.status == "exhausted"),
            responsible_role=None,
            href="/audit",
        ),
    ]
    completed = sum(node["complete"] for node in nodes)
    files = (
        []
        if package is None
        else [
            item if isinstance(item, str) else item.get("name")
            for item in package.manifest_snapshot.get("files", [])
            if isinstance(item, str) or (isinstance(item, dict) and item.get("name"))
        ]
    )
    payload = {
        "application_id": str(application.id),
        "application_number": application.application_number,
        "scenario_name": application.purpose,
        "status": "completed" if completed == len(nodes) else "active",
        "completed_nodes": completed,
        "total_nodes": len(nodes),
        "next_role": next_role,
        "next_action": next_action,
        "nodes": nodes,
        "facts": {
            "data_product": {
                "name": data_product.name if data_product else None,
                "version": data_version.version_label if data_version else None,
                "digest": _short_digest(
                    data_version.snapshot_digest if data_version else None
                ),
            },
            "model_product": {
                "name": model_product.name if model_product else None,
                "version": model_version.version_label if model_version else None,
                "digest": _short_digest(
                    model_version.model_digest if model_version else None
                ),
            },
            "contract": {
                "number": contract.contract_number if contract else None,
                "status": revision.status if revision else None,
                "digest": _short_digest(
                    revision.content_digest if revision else None
                ),
                "signatures": len(signatures),
                "required_signatures": len(parties),
            },
            "execution": {
                "readiness": sorted(readiness_types),
                "eligibility": eligibility is not None,
                "job_status": job.status if job else None,
                "run_status": run.status if run else None,
                "sample_count": (
                    run.execution_environment_snapshot.get("sample_count")
                    if run and isinstance(run.execution_environment_snapshot, dict)
                    else None
                ),
            },
            "result": {
                "artifact_status": artifact.release_status if artifact else None,
                "artifact_digest": _short_digest(
                    artifact.content_digest if artifact else None
                ),
                "approved_reviews": sum(
                    decision.decision == "approved"
                    for decision in artifact_decisions
                ),
                "required_reviews": len(artifact_tasks),
                "package_status": package.status if package else None,
                "package_files": files,
                "grant_status": grant.status if grant else None,
                "download_count": grant.download_count if grant else 0,
                "max_downloads": grant.max_downloads if grant else 0,
            },
        },
        "hard_isolation": False,
    }
    return payload, subject_ids


@router.get("/chains")
async def chains(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    context, actor = await _actor(session, identity)
    applications = await _chain_rows(session, context, actor)
    items = []
    for application in applications:
        payload, _ = await _project_chain(session, application)
        items.append(
            {
                key: payload[key]
                for key in (
                    "application_id",
                    "application_number",
                    "scenario_name",
                    "status",
                    "completed_nodes",
                    "total_nodes",
                    "next_role",
                    "next_action",
                )
            }
        )
    return {"items": items, "total": len(items), "hard_isolation": False}


@router.get("/chains/{application_id}")
async def chain_detail(
    application_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _, actor = await _actor(session, identity)
    application = await _application_for_access(session, application_id, actor)
    payload, _ = await _project_chain(session, application)
    return payload


@router.get("/chains/{application_id}/events")
async def chain_events(
    application_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    view: str = Query(default="critical", pattern="^(critical|all)$"),
    limit: int = Query(default=120, ge=1, le=300),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    context, actor = await _actor(session, identity)
    application = await _application_for_access(session, application_id, actor)
    _, subject_ids = await _project_chain(session, application)
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.space_id == context.space_id,
                    AuditEvent.subject_id.in_(subject_ids),
                )
                .order_by(AuditEvent.stream_sequence.desc())
                .limit(limit)
            )
        ).all()
    )
    if view == "critical":
        events = [
            event
            for event in events
            if event.result != "success"
            or not any(
                token in event.event_type
                for token in ("outbox", "heartbeat", "inbox.received")
            )
        ]
    organization_ids = {
        event.actor_organization_id
        for event in events
        if event.actor_organization_id is not None
    }
    organizations = {
        item.id: item.display_name
        for item in (
            await session.scalars(
                select(Organization).where(Organization.id.in_(organization_ids))
            )
        ).all()
    }
    chain = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": context.space_id},
        )
    ).mappings().one()
    return {
        "view": view,
        "audit_chain_valid": bool(chain["is_valid"]),
        "invalid_sequence": chain["invalid_sequence"],
        "items": [
            {
                "event_id": str(event.event_id),
                "sequence": event.stream_sequence,
                "event_type": event.event_type,
                "result": event.result,
                "occurred_at": _iso(event.occurred_at),
                "actor": (
                    organizations.get(event.actor_organization_id)
                    or event.actor_service_code
                    or event.actor_type
                ),
                "subject_type": event.subject_type,
                "subject_id": str(event.subject_id),
                "state_before": event.evidence_snapshot.get("state_before"),
                "state_after": event.evidence_snapshot.get("state_after"),
                "evidence_digest": _short_digest(event.evidence_digest),
                "event_digest": _short_digest(event.event_digest),
            }
            for event in events
        ],
        "total": len(events),
    }


@router.get("/health")
async def health(
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    context, _ = await _actor(session, identity)
    await session.execute(text("SELECT 1"))
    chain = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": context.space_id},
        )
    ).mappings().one()
    processes: dict[str, bool] = {}
    if RUNTIME_FILE.exists():
        try:
            payload = json.loads(RUNTIME_FILE.read_text(encoding="utf-8-sig"))
            processes = {
                str(item.get("name")): _process_running(item.get("pid"))
                for item in payload
                if isinstance(item, dict) and item.get("name")
            }
        except (OSError, ValueError):
            processes = {}

    minio_status = "unknown"
    try:
        from minio import Minio

        settings = request.app.state.settings
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        minio_status = (
            "ok"
            if client.bucket_exists(settings.minio_quarantine_bucket)
            and client.bucket_exists(settings.minio_release_bucket)
            else "not_ready"
        )
    except Exception:
        minio_status = "not_ready"

    services = [
        {"key": "frontend", "label": "Frontend", "status": "ok" if processes.get("frontend") else "unknown"},
        {"key": "backend", "label": "Backend", "status": "ok"},
        {"key": "database", "label": "PostgreSQL", "status": "ok"},
        {"key": "minio", "label": "MinIO", "status": minio_status},
        {"key": "dispatcher", "label": "Dispatcher", "status": "ok" if processes.get("outbox-dispatcher") else "unknown"},
        {"key": "coordinator", "label": "Coordinator", "status": "ok" if processes.get("execution-coordinator") else "unknown"},
        {"key": "executor", "label": "Executor", "status": "unknown"},
        {"key": "callback", "label": "Callback", "status": "ok" if processes.get("callback-worker") else "unknown"},
        {"key": "audit", "label": "Audit chain", "status": "ok" if chain["is_valid"] else "not_ready"},
    ]
    return {
        "status": (
            "not_ready"
            if any(item["status"] == "not_ready" for item in services)
            else "ok"
        ),
        "services": services,
        "audit_chain_valid": bool(chain["is_valid"]),
        "invalid_sequence": chain["invalid_sequence"],
        "hard_isolation": False,
    }
