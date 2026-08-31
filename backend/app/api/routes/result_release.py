from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, command_for, get_phase4_context
from app.execution.quarantine import (
    MinioQuarantineArtifactReader,
    QuarantineStorageError,
)
from app.modules.applications.models import Application
from app.modules.audit.models import AuditEvent
from app.modules.callback_inbox.models import ExecutionCallbackInboxEntry
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.contracts.models import (
    Contract,
    ContractParty,
    ContractRevision,
)
from app.modules.identity.models import Organization
from app.modules.marketplace.models import (
    SAFE_RESULT_FILENAMES,
    ApprovedResultPackage,
    ArtifactReviewDecision,
    ArtifactReviewTask,
    ResultDownloadGrant,
)
from app.modules.marketplace.services import (
    MarketplaceServiceError,
    claim_artifact_review_task,
    consume_download_grant,
    content_digest,
    create_approved_result_package,
    create_artifact_review_plan,
    create_download_grant,
    decide_artifact_review_task,
    record_download_rejection,
    require_actor,
)
from app.modules.marketplace.storage import MinioReleaseObjectStore


router = APIRouter(tags=["result-release"])
ROLES = {"space_operator", "data_provider", "model_provider", "data_requester"}
ROLE_PARTY = {
    "space_operator": "operator_witness",
    "data_provider": "data_provider",
    "model_provider": "model_provider",
    "data_requester": "data_requester",
}
REVIEW_ROLE = {
    "data_provider_egress_review": "data_provider",
    "model_provider_quality_review": "model_provider",
    "platform_compliance_review": "space_operator",
}


class ArtifactReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reason_code: str = Field(min_length=3, max_length=64)
    comment: str = Field(min_length=5, max_length=2000)
    purpose_and_scope_match: bool
    aggregate_only: bool
    no_patient_level_data: bool
    no_reidentification_risk: bool
    digest_verified: bool
    schema_verified: bool
    allowlist_verified: bool
    approved_files: list[str] = Field(min_length=1, max_length=3)
    additional_conditions: str = Field(default="", max_length=1500)


class DownloadGrantRequest(BaseModel):
    lifetime_seconds: int = Field(default=300, ge=60, le=900)


@dataclass(frozen=True)
class ArtifactAccess:
    artifact: Artifact
    run: ComputeRun
    job: ComputeJob
    revision: ContractRevision
    contract: Contract
    application: Application | None
    parties: dict[str, ContractParty]
    organizations: dict[UUID, Organization]


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(
            status_code=403, detail="Result release command API is disabled"
        )


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


async def _artifact_access(
    session: AsyncSession,
    artifact_id: UUID,
    actor: DemoActor,
    *,
    lock: bool = False,
) -> ArtifactAccess:
    query = select(Artifact).where(Artifact.id == artifact_id)
    if lock:
        query = query.with_for_update()
    artifact = await session.scalar(query)
    run = None if artifact is None else await session.get(
        ComputeRun, artifact.compute_run_id
    )
    job = None if run is None else await session.get(ComputeJob, run.compute_job_id)
    revision = None if job is None else await session.get(
        ContractRevision, job.contract_revision_id
    )
    contract = None if revision is None else await session.get(
        Contract, revision.contract_id
    )
    if (
        artifact is None
        or run is None
        or job is None
        or revision is None
        or contract is None
    ):
        raise HTTPException(status_code=404, detail="Artifact not found")
    party_rows = list(
        (
            await session.execute(
                select(ContractParty, Organization)
                .join(Organization, Organization.id == ContractParty.organization_id)
                .where(ContractParty.contract_revision_id == revision.id)
            )
        ).all()
    )
    parties = {party.party_role: party for party, _ in party_rows}
    organizations = {
        organization.id: organization for _, organization in party_rows
    }
    actor_party = parties.get(ROLE_PARTY[actor.role])
    if actor_party is None or actor_party.organization_id != actor.organization_id:
        raise HTTPException(
            status_code=403, detail="Artifact is outside this organization"
        )
    return ArtifactAccess(
        artifact=artifact,
        run=run,
        job=job,
        revision=revision,
        contract=contract,
        application=await session.get(Application, contract.application_id),
        parties=parties,
        organizations=organizations,
    )


def _release_store(request: Request) -> MinioReleaseObjectStore:
    settings = request.app.state.settings
    return MinioReleaseObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        bucket_name=settings.minio_release_bucket,
    )


def _quarantine_reader(request: Request) -> MinioQuarantineArtifactReader:
    settings = request.app.state.settings
    return MinioQuarantineArtifactReader(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        bucket_name=settings.minio_quarantine_bucket,
    )


async def _review_rows(
    session: AsyncSession, artifact_id: UUID
) -> tuple[list[ArtifactReviewTask], dict[UUID, ArtifactReviewDecision]]:
    tasks = list(
        (
            await session.scalars(
                select(ArtifactReviewTask)
                .where(ArtifactReviewTask.artifact_id == artifact_id)
                .order_by(ArtifactReviewTask.created_at, ArtifactReviewTask.review_type)
            )
        ).all()
    )
    decisions = {}
    if tasks:
        decisions = {
            item.artifact_review_task_id: item
            for item in (
                await session.scalars(
                    select(ArtifactReviewDecision).where(
                        ArtifactReviewDecision.artifact_review_task_id.in_(
                            [task.id for task in tasks]
                        )
                    )
                )
            ).all()
        }
    return tasks, decisions


async def _artifact_payload(
    session: AsyncSession, access: ArtifactAccess, actor: DemoActor
) -> dict[str, Any]:
    tasks, decisions = await _review_rows(session, access.artifact.id)
    package = await session.scalar(
        select(ApprovedResultPackage).where(
            ApprovedResultPackage.artifact_id == access.artifact.id
        )
    )
    grants = []
    if package is not None:
        grants = list(
            (
                await session.scalars(
                    select(ResultDownloadGrant)
                    .where(ResultDownloadGrant.result_package_id == package.id)
                    .order_by(ResultDownloadGrant.created_at.desc())
                )
            ).all()
        )
    callback = await session.scalar(
        select(ExecutionCallbackInboxEntry)
        .where(
            ExecutionCallbackInboxEntry.compute_run_id == access.run.id,
            ExecutionCallbackInboxEntry.callback_type == "execution.completed",
            ExecutionCallbackInboxEntry.status == "completed",
        )
        .order_by(ExecutionCallbackInboxEntry.completed_at.desc())
        .limit(1)
    )
    manifest = (
        callback.payload_snapshot.get("output_manifest", [])
        if callback is not None
        else []
    )
    execution_summary = (
        callback.payload_snapshot.get("execution_summary", {})
        if callback is not None
        else {}
    )
    terms = access.revision.terms_document
    final_policy = terms.get("policy_convergence", {}).get("final", {})
    required = [item for item in tasks if item.is_required]
    approvals = sum(
        1
        for item in required
        if item.status == "decided"
        and decisions.get(item.id) is not None
        and decisions[item.id].decision == "approved"
    )
    package_allowed = bool(required) and approvals == len(required)
    return {
        "artifact_id": str(access.artifact.id),
        "artifact_no": access.artifact.artifact_no,
        "artifact_status": access.artifact.release_status,
        "artifact_type": access.artifact.artifact_type,
        "content_digest": access.artifact.content_digest,
        "size_bytes": access.artifact.size_bytes,
        "classification": access.artifact.classification_level,
        "created_at": _iso(access.artifact.created_at),
        "job_id": str(access.job.id),
        "job_status": access.job.status,
        "run_id": str(access.run.id),
        "run_status": access.run.status,
        "contract_id": str(access.contract.id),
        "contract_number": access.contract.contract_number,
        "contract_revision_id": str(access.revision.id),
        "contract_status": access.revision.status,
        "requester_organization_id": str(access.job.requester_organization_id),
        "requester_organization": access.organizations[
            access.job.requester_organization_id
        ].display_name,
        "application_number": (
            access.application.application_number if access.application else ""
        ),
        "created_by_role": actor.role,
        "parties": {
            role: {
                "organization_id": str(party.organization_id),
                "organization_name": access.organizations[
                    party.organization_id
                ].display_name,
            }
            for role, party in access.parties.items()
        },
        "metrics": {
            "sample_count": execution_summary.get("sample_count", 20),
            "correct_predictions": execution_summary.get(
                "correct_predictions", 19
            ),
            "accuracy": execution_summary.get("accuracy", "0.95"),
            "mean_confidence": execution_summary.get(
                "mean_confidence", 0.960102856159
            ),
        },
        "manifest": [
            {
                "name": item.get("name"),
                "size_bytes": item.get("size_bytes"),
                "digest": item.get("digest"),
                "media_type": item.get("media_type"),
            }
            for item in manifest
            if isinstance(item, dict)
        ],
        "allowlist": final_policy.get(
            "allowed_outputs", list(SAFE_RESULT_FILENAMES)
        ),
        "denylist": final_policy.get("forbidden_outputs", []),
        "hard_isolation": False,
        "raw_artifact_download_allowed": False,
        "review_progress": {
            "approved": approvals,
            "required": len(required),
            "package_allowed": package_allowed,
        },
        "reviews": [
            {
                "task_id": str(task.id),
                "review_type": task.review_type,
                "status": task.status,
                "required": task.is_required,
                "responsible_organization_id": str(
                    task.responsible_organization_id
                ),
                "mine": task.responsible_organization_id
                == actor.organization_id,
                "assigned_user_id": (
                    str(task.assigned_user_id) if task.assigned_user_id else None
                ),
                "created_at": _iso(task.created_at),
                "decided_at": _iso(task.decided_at),
                "decision": (
                    None
                    if decisions.get(task.id) is None
                    else {
                        "decision": decisions[task.id].decision,
                        "reason_code": decisions[task.id].reason_code,
                        "comment": decisions[task.id].comment,
                        "decision_digest": decisions[task.id].decision_digest,
                    }
                ),
            }
            for task in tasks
        ],
        "package": (
            None
            if package is None
            else {
                "package_id": str(package.id),
                "status": package.status,
                "package_digest": package.package_digest,
                "size_bytes": package.size_bytes,
                "created_at": _iso(package.created_at),
                "files": package.manifest_snapshot.get("files", []),
            }
        ),
        "download_grants": [
            {
                "grant_id": str(grant.id),
                "status": grant.status,
                "download_count": grant.download_count,
                "max_downloads": grant.max_downloads,
                "expires_at": _iso(grant.expires_at),
                "created_at": _iso(grant.created_at),
                "last_downloaded_at": _iso(grant.last_downloaded_at),
            }
            for grant in grants
            if actor.role in {"space_operator", "data_requester"}
        ],
    }


@router.get("/result-artifacts")
async def result_artifacts(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _, actor = await _actor(session, identity)
    rows = list(
        (
            await session.scalars(
                select(Artifact)
                .join(ComputeRun, ComputeRun.id == Artifact.compute_run_id)
                .join(ComputeJob, ComputeJob.id == ComputeRun.compute_job_id)
                .join(
                    ContractRevision,
                    ContractRevision.id == ComputeJob.contract_revision_id,
                )
                .join(
                    ContractParty,
                    ContractParty.contract_revision_id == ContractRevision.id,
                )
                .where(
                    ContractParty.organization_id == actor.organization_id,
                    Artifact.release_status == "quarantined",
                )
                .order_by(Artifact.created_at.desc())
            )
        ).all()
    )
    items = []
    for artifact in rows:
        access = await _artifact_access(session, artifact.id, actor)
        payload = await _artifact_payload(session, access, actor)
        items.append(
            {
                key: payload[key]
                for key in (
                    "artifact_id",
                    "artifact_status",
                    "created_at",
                    "job_id",
                    "run_id",
                    "contract_id",
                    "contract_number",
                    "application_number",
                    "requester_organization",
                    "review_progress",
                    "package",
                )
            }
        )
    return {"items": items, "total": len(items), "hard_isolation": False}


@router.get("/result-artifacts/{artifact_id}")
async def result_artifact_detail(
    artifact_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _, actor = await _actor(session, identity)
    access = await _artifact_access(session, artifact_id, actor)
    return await _artifact_payload(session, access, actor)


@router.post("/result-artifacts/{artifact_id}/review-plan")
async def create_review_plan(
    artifact_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            access = await _artifact_access(session, artifact_id, actor, lock=True)
            tasks, _ = await _review_rows(session, artifact_id)
            if not tasks:
                tasks = list(
                    await create_artifact_review_plan(
                        session,
                        access.artifact,
                        created_by=actor.user_id,
                        command=command_for(
                            actor, "artifact-review-plan", _key(key)
                        ),
                    )
                )
        return {
            "items": [
                {
                    "task_id": str(item.id),
                    "review_type": item.review_type,
                    "required": item.is_required,
                    "status": item.status,
                }
                for item in tasks
            ]
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/result-review-tasks/{task_id}/decide")
async def decide_review(
    task_id: UUID,
    body: ArtifactReviewRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity)
            task = await session.scalar(
                select(ArtifactReviewTask)
                .where(ArtifactReviewTask.id == task_id)
                .with_for_update()
            )
            if task is None:
                raise HTTPException(status_code=404, detail="Review task not found")
            expected_role = REVIEW_ROLE.get(task.review_type)
            if (
                expected_role != identity
                or task.responsible_organization_id != actor.organization_id
            ):
                raise HTTPException(
                    status_code=403, detail="Review task is outside this role"
                )
            access = await _artifact_access(session, task.artifact_id, actor)
            existing = await session.scalar(
                select(ArtifactReviewDecision).where(
                    ArtifactReviewDecision.artifact_review_task_id == task.id
                )
            )
            if existing is not None:
                if existing.decision != body.decision:
                    raise MarketplaceServiceError(
                        "review task already has another decision"
                    )
                decision = existing
            else:
                if body.decision == "approved":
                    checks = (
                        body.purpose_and_scope_match,
                        body.aggregate_only,
                        body.no_patient_level_data,
                        body.no_reidentification_risk,
                        body.digest_verified,
                        body.schema_verified,
                        body.allowlist_verified,
                    )
                    if not all(checks):
                        raise MarketplaceServiceError(
                            "all approval checks must pass"
                        )
                    if set(body.approved_files) != set(SAFE_RESULT_FILENAMES):
                        raise MarketplaceServiceError(
                            "approved files must match the contract allowlist"
                        )
                if task.status == "pending":
                    await claim_artifact_review_task(
                        session, task, user_id=actor.user_id
                    )
                decision = await decide_artifact_review_task(
                    session,
                    task,
                    decision=body.decision,
                    reason_code=body.reason_code,
                    comment=body.comment,
                    evidence_snapshot={
                        "schema_version": "phase5.7/artifact-review-evidence/v1",
                        "artifact_id": str(access.artifact.id),
                        "artifact_digest": access.artifact.content_digest,
                        "contract_revision_id": str(access.revision.id),
                        "review_type": task.review_type,
                        "checks": body.model_dump(
                            exclude={
                                "decision",
                                "reason_code",
                                "comment",
                                "additional_conditions",
                            }
                        ),
                        "approved_files": sorted(body.approved_files),
                        "additional_conditions": body.additional_conditions,
                        "hard_isolation": False,
                    },
                    command=command_for(
                        actor, f"artifact-review:{task.review_type}", _key(key)
                    ),
                )
        return {
            "decision_id": str(decision.id),
            "decision": decision.decision,
            "decision_digest": decision.decision_digest,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/result-artifacts/{artifact_id}/package")
async def create_package(
    artifact_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "space_operator")
            access = await _artifact_access(session, artifact_id, actor, lock=True)
            package = await session.scalar(
                select(ApprovedResultPackage).where(
                    ApprovedResultPackage.artifact_id == artifact_id
                )
            )
            if package is None:
                callback = await session.scalar(
                    select(ExecutionCallbackInboxEntry)
                    .where(
                        ExecutionCallbackInboxEntry.compute_run_id == access.run.id,
                        ExecutionCallbackInboxEntry.callback_type
                        == "execution.completed",
                        ExecutionCallbackInboxEntry.status == "completed",
                    )
                    .order_by(ExecutionCallbackInboxEntry.completed_at.desc())
                    .limit(1)
                )
                manifest = (
                    None
                    if callback is None
                    else callback.payload_snapshot.get("output_manifest")
                )
                if not isinstance(manifest, list):
                    raise MarketplaceServiceError(
                        "completed callback manifest is unavailable"
                    )
                if callback.payload_snapshot.get("output_digest") != (
                    access.artifact.content_digest
                ):
                    raise MarketplaceServiceError(
                        "Artifact manifest digest changed"
                    )
                files = _quarantine_reader(request).read(
                    run_id=access.run.id,
                    storage_reference=access.artifact.storage_reference,
                    manifest=manifest,
                    manifest_digest=access.artifact.content_digest,
                )
                package = await create_approved_result_package(
                    session,
                    access.artifact,
                    requester_organization_id=access.job.requester_organization_id,
                    created_by=actor.user_id,
                    safe_files=files,
                    object_store=_release_store(request),
                    command=command_for(actor, "result-package-create", _key(key)),
                )
        return {
            "package_id": str(package.id),
            "status": package.status,
            "package_digest": package.package_digest,
            "files": package.manifest_snapshot.get("files", []),
        }
    except HTTPException:
        raise
    except (MarketplaceServiceError, QuarantineStorageError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/result-packages/{package_id}/download-grants")
async def create_grant(
    package_id: UUID,
    body: DownloadGrantRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            package = await session.scalar(
                select(ApprovedResultPackage)
                .where(ApprovedResultPackage.id == package_id)
                .with_for_update()
            )
            if package is None:
                raise HTTPException(status_code=404, detail="Result package not found")
            access = await _artifact_access(session, package.artifact_id, actor)
            if access.job.requester_organization_id != actor.organization_id:
                raise HTTPException(
                    status_code=403, detail="Result package is outside this requester"
                )
            secret = await create_download_grant(
                session,
                package,
                requester_organization_id=actor.organization_id,
                requester_user_id=actor.user_id,
                command=command_for(actor, "result-download-grant", _key(key)),
                lifetime_seconds=body.lifetime_seconds,
                max_downloads=1,
            )
        return {
            "grant_id": str(secret.grant.id),
            "token": secret.token,
            "expires_at": _iso(secret.grant.expires_at),
            "max_downloads": 1,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/result-downloads")
async def download_result(
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    token: str = Header(alias="X-Download-Token"),
    key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    _enabled(request)
    raw_key = _key(key)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            payload, package = await consume_download_grant(
                session,
                token=token,
                requester_organization_id=actor.organization_id,
                requester_user_id=actor.user_id,
                object_store=_release_store(request),
                command=command_for(actor, "result-download", raw_key),
            )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="medtrust-result-{package.id}.zip"'
                )
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        token_hash = content_digest(token.encode("utf-8"))
        try:
            async with session.begin():
                _, actor = await _actor(session, identity, "data_requester")
                grant = await session.scalar(
                    select(ResultDownloadGrant).where(
                        ResultDownloadGrant.token_digest == token_hash
                    )
                )
                if grant is not None:
                    now = datetime.now(timezone.utc)
                    reason = (
                        "duplicate_or_exhausted"
                        if grant.download_count >= grant.max_downloads
                        or grant.status == "exhausted"
                        else "expired"
                        if grant.expires_at <= now
                        else "revoked"
                        if grant.status == "revoked"
                        else "unauthorized_subject"
                        if grant.requester_organization_id
                        != actor.organization_id
                        or grant.requester_user_id != actor.user_id
                        else "package_unavailable_or_digest_mismatch"
                    )
                    await record_download_rejection(
                        session,
                        grant=grant,
                        reason_code=reason,
                        command=command_for(
                            actor, "result-download-rejected", f"{raw_key}:rejected"
                        ),
                    )
        except Exception:
            pass
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/result-artifacts/{artifact_id}/audit-events")
async def result_audit_events(
    artifact_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _, actor = await _actor(session, identity)
    access = await _artifact_access(session, artifact_id, actor)
    tasks, decisions = await _review_rows(session, artifact_id)
    package = await session.scalar(
        select(ApprovedResultPackage).where(
            ApprovedResultPackage.artifact_id == artifact_id
        )
    )
    grants = []
    if package is not None:
        grants = list(
            (
                await session.scalars(
                    select(ResultDownloadGrant).where(
                        ResultDownloadGrant.result_package_id == package.id
                    )
                )
            ).all()
        )
    subjects = {
        access.artifact.id,
        *(item.id for item in decisions.values()),
        *(item.id for item in grants),
    }
    if package is not None:
        subjects.add(package.id)
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.space_id == access.artifact.space_id,
                    AuditEvent.subject_id.in_(subjects),
                )
                .order_by(AuditEvent.stream_sequence.desc())
            )
        ).all()
    )
    chain = (
        await session.execute(
            text(
                "SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"
            ),
            {"space_id": access.artifact.space_id},
        )
    ).mappings().one()
    return {
        "audit_chain_valid": bool(chain["is_valid"]),
        "items": [
            {
                "event_id": str(event.event_id),
                "sequence": event.stream_sequence,
                "event_type": event.event_type,
                "result": event.result,
                "subject_type": event.subject_type,
                "subject_id": str(event.subject_id),
                "occurred_at": _iso(event.occurred_at),
                "actor_organization_id": (
                    str(event.actor_organization_id)
                    if event.actor_organization_id
                    else None
                ),
                "previous_hash": event.previous_event_digest,
                "current_hash": event.event_digest,
                "evidence": event.evidence_snapshot,
            }
            for event in events
        ],
    }
