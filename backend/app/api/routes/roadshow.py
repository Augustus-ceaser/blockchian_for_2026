from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import (
    PHASE4_APPLICATION_NUMBER,
    PHASE4_CONTRACT_NUMBER,
    Phase4DemoError,
    activate_phase4_contract_command,
    approve_data_listing_command,
    approve_model_listing_command,
    build_phase4_contract_command,
    command_for,
    confirm_phase4_readiness_command,
    create_phase4_compute_run_command,
    create_phase4_download_grant_command,
    create_phase4_result_package_command,
    decide_compute_demand_review_command,
    decide_phase4_artifact_review_command,
    ensure_phase4_artifact_review_plan,
    ensure_phase4_demo_initial,
    get_phase4_context,
    latest_phase4_artifact,
    phase4_is_ready,
    sign_phase4_contract_command,
    submit_compute_demand_command,
    submit_data_listing_command,
    submit_model_listing_command,
)
from app.modules.applications.models import Application
from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.callback_inbox.models import ExecutionCallbackInboxEntry
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.contracts.models import Contract, ContractParty, ContractRevision, ContractSignature
from app.modules.inbox.models import ConsumerInboxEntry
from app.modules.marketplace.models import (
    ApprovedResultPackage,
    ArtifactReviewDecision,
    ArtifactReviewTask,
    ContractReadinessConfirmation,
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.services import (
    MarketplaceServiceError,
    consume_download_grant,
    require_actor,
)
from app.modules.marketplace.storage import MinioReleaseObjectStore
from app.modules.reviews.models import ReviewDecision, ReviewTask
from app.modules.catalog.models import DataProduct, DataProductPublication, DataProductVersion


router = APIRouter(prefix="/roadshow", tags=["roadshow"])
WORKSPACE = Path(__file__).resolve().parents[4]
DEMO_ROLES = {
    "space_operator",
    "data_provider",
    "model_provider",
    "data_requester",
    "catalog_curator",
}


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Roadshow command API is disabled")


def _key(value: str | None, action: str) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail=f"{action} requires Idempotency-Key")
    return value.strip()


async def _actor(session: AsyncSession, identity: str, expected: str | None = None):
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


def _store(request: Request) -> MinioReleaseObjectStore:
    settings = request.app.state.settings
    return MinioReleaseObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        bucket_name=settings.minio_release_bucket,
    )


def _iso(value):
    return None if value is None else value.isoformat()


async def _phase4_revision(session: AsyncSession) -> ContractRevision | None:
    return await session.scalar(
        select(ContractRevision)
        .join(Contract, Contract.id == ContractRevision.contract_id)
        .where(Contract.contract_number == PHASE4_CONTRACT_NUMBER)
        .order_by(ContractRevision.revision_no.desc())
        .limit(1)
    )


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap(
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    if identity != "space_operator":
        raise HTTPException(status_code=403, detail="Space operator identity required")
    try:
        async with session.begin():
            context = await ensure_phase4_demo_initial(session, workspace=WORKSPACE)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ready": True, "space_id": str(context.space_id), "hard_isolation": False}


@router.get("/context")
async def context_view(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    return {
        "identity": actor.role,
        "organization": actor.organization_name,
        "user": actor.user_name,
        "space_id": str(context.space_id),
        "notice": "所有机构、病例、数据、模型和计算结果均为模拟或公开数据工程演示。",
        "assurance": {
            "hard_isolation": False,
            "clinical_validation": False,
            "production_privacy_compute": False,
            "national_certification": False,
        },
    }


@router.get("/overview")
async def overview(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    data_version = await session.get(DataProductVersion, context.data_version_id)
    model_version = await session.get(ModelVersion, context.model_version_id)
    data_publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == context.data_version_id,
            DataProductPublication.status == "active",
        )
    )
    model_publication = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == context.model_version_id,
            ModelPublication.status == "active",
        )
    )
    application = await session.scalar(
        select(Application).where(Application.application_number == PHASE4_APPLICATION_NUMBER)
    )
    revision = await _phase4_revision(session)
    run = await session.scalar(
        select(ComputeRun).order_by(ComputeRun.prepared_at.desc()).limit(1)
    )
    artifact = await latest_phase4_artifact(session)
    package = None if artifact is None else await session.scalar(
        select(ApprovedResultPackage).where(ApprovedResultPackage.artifact_id == artifact.id)
    )
    open_reviews = await session.scalar(
        select(func.count()).select_from(ReviewTask).where(
            ReviewTask.assignee_organization_id == actor.organization_id,
            ReviewTask.task_status.in_(("pending", "claimed")),
        )
    )
    artifact_reviews = await session.scalar(
        select(func.count()).select_from(ArtifactReviewTask).where(
            ArtifactReviewTask.responsible_organization_id == actor.organization_id,
            ArtifactReviewTask.status.in_(("pending", "claimed")),
        )
    )
    return {
        "role": identity,
        "data_listing": "published" if data_publication is not None else (None if data_version is None else data_version.status),
        "model_listing": "published" if model_publication is not None else (None if model_version is None else model_version.status),
        "application": None if application is None else application.status,
        "contract": None if revision is None else revision.status,
        "execution_ready": await phase4_is_ready(session),
        "run": None if run is None else run.status,
        "artifact": None if artifact is None else artifact.release_status,
        "result_package": None if package is None else package.status,
        "my_pending_reviews": int(open_reviews or 0) + int(artifact_reviews or 0),
    }


@router.get("/catalog/data")
async def data_catalog(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    product = await session.get(DataProduct, context.data_product_id)
    version = await session.get(DataProductVersion, context.data_version_id)
    publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == context.data_version_id,
            DataProductPublication.status == "active",
        )
    )
    if product is None or version is None:
        return {"items": []}
    return {"items": [{
        "id": str(product.id), "version_id": str(version.id), "name": product.name,
        "description": product.description, "domain": product.domain,
        "version": version.version_label, "status": version.status,
        "published": publication is not None, "use_mode": version.default_use_mode,
        "scope": version.scope_metadata, "quality": version.quality_report,
        "provenance": version.provenance_summary,
        "restrictions": ["禁止原始图像导出", "仅受控计算", "仅演示用途"],
    }]}


@router.get("/catalog/models")
async def model_catalog(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    product = await session.get(ModelProduct, context.model_product_id)
    version = await session.get(ModelVersion, context.model_version_id)
    publication = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == context.model_version_id,
            ModelPublication.status == "active",
        )
    )
    if product is None or version is None:
        return {"items": []}
    return {"items": [{
        "id": str(product.id), "version_id": str(version.id), "name": product.name,
        "description": product.description, "domain": product.domain,
        "version": version.version_label, "status": version.status,
        "published": publication is not None, "entrypoint": version.entrypoint_id,
        "runtime": version.runtime, "compatibility": version.compatibility_metadata,
        "restrictions": ["固定白名单入口", "仅CPU推理", "禁止权重导出", "非临床用途"],
    }]}


@router.get("/workflow")
async def workflow(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    application = await session.scalar(select(Application).where(Application.application_number == PHASE4_APPLICATION_NUMBER))
    tasks = [] if application is None else list((await session.scalars(select(ReviewTask).where(ReviewTask.application_id == application.id).order_by(ReviewTask.sequence_no, ReviewTask.review_type))).all())
    revision = await _phase4_revision(session)
    readiness = [] if revision is None else list((await session.scalars(select(ContractReadinessConfirmation).where(ContractReadinessConfirmation.contract_revision_id == revision.id))).all())
    run = await session.scalar(select(ComputeRun).order_by(ComputeRun.prepared_at.desc()).limit(1))
    artifact = await latest_phase4_artifact(session)
    artifact_tasks = [] if artifact is None else list((await session.scalars(select(ArtifactReviewTask).where(ArtifactReviewTask.artifact_id == artifact.id))).all())
    signatures = [] if revision is None else list((await session.execute(
        select(ContractSignature, ContractParty)
        .join(ContractParty, ContractParty.id == ContractSignature.contract_party_id)
        .where(ContractSignature.contract_revision_id == revision.id)
    )).all())
    package = None if artifact is None else await session.scalar(
        select(ApprovedResultPackage).where(ApprovedResultPackage.artifact_id == artifact.id)
    )
    events = list((await session.scalars(select(AuditEvent).where(AuditEvent.space_id == context.space_id).order_by(AuditEvent.stream_sequence.desc()).limit(25))).all())
    return {
        "application": None if application is None else {"id": str(application.id), "number": application.application_number, "status": application.status, "purpose": application.purpose},
        "reviews": [{"id": str(item.id), "type": item.review_type, "status": item.task_status, "mine": item.assignee_organization_id == actor.organization_id} for item in tasks],
        "contract": None if revision is None else {"id": str(revision.id), "number": PHASE4_CONTRACT_NUMBER, "status": revision.status, "content_digest": revision.content_digest},
        "signatures": [{"party_role": party.party_role, "signed_at": _iso(signature.signed_at)} for signature, party in signatures],
        "readiness": [{"type": item.readiness_type, "confirmed_at": _iso(item.confirmed_at)} for item in readiness],
        "run": None if run is None else {"id": str(run.id), "status": run.status, "ordinal": run.reservation_ordinal},
        "artifact": None if artifact is None else {"id": str(artifact.id), "status": artifact.release_status, "digest": artifact.content_digest},
        "artifact_reviews": [{"id": str(item.id), "type": item.review_type, "status": item.status, "required": item.is_required, "mine": item.responsible_organization_id == actor.organization_id} for item in artifact_tasks],
        "result_package": None if package is None else {"id": str(package.id), "status": package.status, "files": package.manifest_snapshot.get("files", [])},
        "audit": [{"sequence": item.stream_sequence, "type": item.event_type, "result": item.result, "occurred_at": _iso(item.occurred_at)} for item in events],
    }


async def _execute(request, session, identity, expected, key, callback):
    _enabled(request)
    try:
        async with session.begin():
            context, _ = await _actor(session, identity, expected)
            result = await callback(context, _key(key, expected))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/data-listing/submit")
async def data_submit(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "data_provider", key, lambda c, k: _data_submit(session, c, k))

async def _data_submit(session, context, key):
    row = await submit_data_listing_command(session, context, raw_key=key)
    return {"version_id": str(row.id), "status": row.status}

@router.post("/data-listing/approve")
async def data_approve(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "space_operator", key, lambda c, k: _data_approve(session, c, k))

async def _data_approve(session, context, key):
    row = await approve_data_listing_command(session, context, raw_key=key)
    return {"publication_id": str(row.id), "status": row.status}

@router.post("/model-listing/submit")
async def model_submit(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "model_provider", key, lambda c, k: _model_submit(session, c, k))

async def _model_submit(session, context, key):
    row = await submit_model_listing_command(session, context, workspace=WORKSPACE, raw_key=key)
    return {"version_id": str(row.id), "status": row.status}

@router.post("/model-listing/approve")
async def model_approve(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "space_operator", key, lambda c, k: _model_approve(session, c, k))

async def _model_approve(session, context, key):
    row = await approve_model_listing_command(session, context, workspace=WORKSPACE, raw_key=key)
    return {"publication_id": str(row.id), "status": row.status}

@router.post("/demands/submit")
async def demand_submit(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "data_requester", key, lambda c, k: _demand_submit(session, c, k))

async def _demand_submit(session, context, key):
    row = await submit_compute_demand_command(session, context, raw_key=key)
    return {"snapshot_id": str(row.id), "snapshot_digest": row.snapshot_digest}


def _review_endpoint(path: str, review_type: str, role: str):
    async def endpoint(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
        return await _execute(request, session, identity, role, key, lambda c, k: _review(session, c, k, review_type))
    router.add_api_route(path, endpoint, methods=["POST"])

async def _review(session, context, key, review_type):
    row = await decide_compute_demand_review_command(session, context, review_type=review_type, raw_key=key)
    return {"decision_id": str(row.id), "decision": row.decision}

_review_endpoint("/reviews/platform-precheck/approve", "application_precheck", "space_operator")
_review_endpoint("/reviews/data-provider/approve", "data_provider_review", "data_provider")
_review_endpoint("/reviews/model-provider/approve", "model_provider_review", "model_provider")


@router.post("/contracts/create")
async def contract_create(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "space_operator", key, lambda c, k: _contract_create(session, c, k))

async def _contract_create(session, context, key):
    row = await build_phase4_contract_command(session, context, raw_key=key)
    return {"revision_id": str(row.id), "status": row.status, "content_digest": row.content_digest}

@router.post("/contracts/sign")
async def contract_sign(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    _enabled(request)
    try:
        async with session.begin():
            context, _ = await _actor(session, identity)
            row = await sign_phase4_contract_command(session, context, actor_role=identity, raw_key=_key(key, "contract-sign"))
        return {"revision_id": str(row.id), "status": row.status}
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/contracts/activate")
async def contract_activate(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "space_operator", key, lambda c, k: _contract_activate(session, c, k))

async def _contract_activate(session, context, key):
    row = await activate_phase4_contract_command(session, context, raw_key=key)
    return {"revision_id": str(row.id), "status": row.status}


def _readiness_endpoint(path: str, readiness: str, role: str):
    async def endpoint(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
        return await _execute(request, session, identity, role, key, lambda c, k: _ready(session, c, k, readiness))
    router.add_api_route(path, endpoint, methods=["POST"])

async def _ready(session, context, key, readiness):
    row = await confirm_phase4_readiness_command(session, context, readiness_type=readiness, workspace=WORKSPACE, raw_key=key)
    return {"confirmation_id": str(row.id), "type": row.readiness_type}

_readiness_endpoint("/readiness/data", "data_ready", "data_provider")
_readiness_endpoint("/readiness/model", "model_ready", "model_provider")
_readiness_endpoint("/readiness/platform", "platform_ready", "space_operator")


@router.post("/compute-runs", status_code=status.HTTP_202_ACCEPTED)
async def compute_run(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "data_requester", key, lambda c, k: _compute_run(session, c, k))

async def _compute_run(session, context, key):
    job, run, replayed = await create_phase4_compute_run_command(session, context, raw_key=key)
    return {"job_id": str(job.id), "run_id": str(run.id), "status": run.status, "reservation_ordinal": run.reservation_ordinal, "replayed": replayed}


@router.post("/artifacts/review-plan")
async def artifact_plan(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "space_operator", key, lambda c, k: _artifact_plan(session, c))

async def _artifact_plan(session, context):
    rows = await ensure_phase4_artifact_review_plan(session, context)
    return {"items": [{"id": str(row.id), "type": row.review_type, "required": row.is_required, "status": row.status} for row in rows]}


def _artifact_review_endpoint(path: str, review_type: str, role: str):
    async def endpoint(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
        return await _execute(request, session, identity, role, key, lambda c, k: _artifact_review(session, c, k, review_type))
    router.add_api_route(path, endpoint, methods=["POST"])

async def _artifact_review(session, context, key, review_type):
    row = await decide_phase4_artifact_review_command(session, context, review_type=review_type, raw_key=key)
    return {"decision_id": str(row.id), "decision": row.decision}

_artifact_review_endpoint("/artifact-reviews/data-provider/approve", "data_provider_egress_review", "data_provider")
_artifact_review_endpoint("/artifact-reviews/platform/approve", "platform_compliance_review", "space_operator")
_artifact_review_endpoint("/artifact-reviews/model-provider/approve", "model_provider_quality_review", "model_provider")


@router.post("/result-packages")
async def result_package(request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    _enabled(request)
    try:
        async with session.begin():
            context, _ = await _actor(session, identity, "space_operator")
            row = await create_phase4_result_package_command(session, context, workspace=WORKSPACE, object_store=_store(request), raw_key=_key(key, "result-package"))
        return {"package_id": str(row.id), "status": row.status, "files": row.manifest_snapshot["files"]}
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/result-packages/{package_id}/download-grants")
async def download_grant(package_id: UUID, request: Request, identity: str = Header(alias="X-Demo-Identity"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    return await _execute(request, session, identity, "data_requester", key, lambda c, k: _grant(session, c, package_id, k))

async def _grant(session, context, package_id, key):
    grant_id, token, expires_at = await create_phase4_download_grant_command(session, context, package_id=package_id, raw_key=key)
    return {"grant_id": str(grant_id), "token": token, "expires_at": _iso(expires_at), "max_downloads": 1}


@router.post("/result-downloads")
async def result_download(request: Request, identity: str = Header(alias="X-Demo-Identity"), token: str = Header(alias="X-Download-Token"), key: str | None = Header(default=None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_db_session)):
    _enabled(request)
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, "data_requester")
            payload, package = await consume_download_grant(
                session,
                token=token,
                requester_organization_id=actor.organization_id,
                requester_user_id=actor.user_id,
                object_store=_store(request),
                command=command_for(
                    actor, "result-download", _key(key, "result-download")
                ),
            )
        return Response(content=payload, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="medtrust-approved-result-{package.id}.zip"'})
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/infrastructure")
async def infrastructure(identity: str = Header(alias="X-Demo-Identity"), session: AsyncSession = Depends(get_db_session)):
    await _actor(session, identity, "space_operator")
    counts = {}
    for name, model in (("audit_events", AuditEvent), ("outbox", OutboxMessage), ("consumer_inbox", ConsumerInboxEntry), ("callback_inbox", ExecutionCallbackInboxEntry)):
        counts[name] = int(await session.scalar(select(func.count()).select_from(model)) or 0)
    return {"counts": counts, "delivery": "at-least-once with idempotent consumers", "hard_isolation": False}
