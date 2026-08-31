from __future__ import annotations

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import (
    PHASE4_DATA_PRODUCT_CODE,
    PHASE4_MODEL_PRODUCT_CODE,
    DemoActor,
)
from app.modules.applications.models import Application, ApplicationItem
from app.modules.audit import (
    AuditCommandContext,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.audit.models import AuditEvent
from app.modules.catalog.models import DataProduct, DataProductPublication, DataProductVersion
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.contracts.models import Contract, ContractObject, ContractRevision
from app.modules.lifecycle.models import ProductLifecycleRequest
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ApprovedResultPackage,
    ContractModelObject,
    ModelProduct,
    ModelPublication,
    ModelVersion,
    ResultDownloadGrant,
)


class LifecycleGovernanceError(ValueError):
    pass


def _command(actor: DemoActor, request_id: UUID, action: str, raw_key: str) -> AuditCommandContext:
    return AuditCommandContext(
        command_id=uuid5(NAMESPACE_URL, f"medtrust:phase59:{request_id}:{action}:{raw_key}"),
        idempotency_key=digest_idempotency_key(f"phase59:{action}:{raw_key}"),
        correlation_id=request_id,
        actor_type="user",
        actor_organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )


def _event_prefix(target_type: str) -> str:
    return "data_product" if target_type == "data_product" else "model_product"


def _event_action(action: str) -> str:
    return "deletion" if action == "archive" else action


async def _target(
    session: AsyncSession, *, target_type: str, product_id: UUID, lock: bool = False
):
    model = DataProduct if target_type == "data_product" else ModelProduct
    statement = select(model).where(model.id == product_id)
    if lock:
        statement = statement.with_for_update()
    product = await session.scalar(statement)
    if product is None:
        raise LifecycleGovernanceError("产品不存在")
    return product


async def _active_publication(session: AsyncSession, target_type: str, product_id: UUID):
    model = DataProductPublication if target_type == "data_product" else ModelPublication
    product_field = (
        DataProductPublication.data_product_id
        if target_type == "data_product"
        else ModelPublication.model_product_id
    )
    return await session.scalar(
        select(model).where(product_field == product_id, model.status == "active")
    )


async def build_impact_snapshot(
    session: AsyncSession,
    *,
    space_id: UUID,
    target_type: str,
    product_id: UUID,
    version_id: UUID | None,
) -> dict:
    if target_type == "data_product":
        application_ids = select(ApplicationItem.application_id).where(
            ApplicationItem.data_product_id == product_id
        )
        version_ids = select(DataProductVersion.id).where(
            DataProductVersion.data_product_id == product_id
        )
        revision_ids = select(ContractObject.contract_revision_id).where(
            ContractObject.data_product_version_id.in_(version_ids)
        )
    else:
        application_ids = select(ApplicationModelSelection.application_id).where(
            ApplicationModelSelection.model_product_id == product_id
        )
        version_ids = select(ModelVersion.id).where(
            ModelVersion.model_product_id == product_id
        )
        revision_ids = select(ContractModelObject.contract_revision_id).where(
            ContractModelObject.model_version_id.in_(version_ids)
        )
    contract_ids = select(ContractRevision.contract_id).where(ContractRevision.id.in_(revision_ids))
    job_ids = select(ComputeJob.id).where(ComputeJob.contract_revision_id.in_(revision_ids))
    run_ids = select(ComputeRun.id).where(ComputeRun.compute_job_id.in_(job_ids))
    artifact_ids = select(Artifact.id).where(Artifact.compute_run_id.in_(run_ids))

    async def count(model, *criteria) -> int:
        return int(await session.scalar(select(func.count()).select_from(model).where(*criteria)) or 0)

    chain = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": space_id},
        )
    ).mappings().one()
    snapshot = {
        "schema_version": "phase5.9/product-impact/v1",
        "target_type": target_type,
        "product_id": str(product_id),
        "version_id": str(version_id) if version_id else None,
        "applications": {
            "draft": await count(Application, Application.id.in_(application_ids), Application.status == "draft"),
            "submitted_or_reviewing": await count(
                Application,
                Application.id.in_(application_ids),
                Application.status.in_(("submitted", "prechecking", "provider_review")),
            ),
            "approved": await count(Application, Application.id.in_(application_ids), Application.status == "approved"),
        },
        "contracts": {
            "draft": await count(ContractRevision, ContractRevision.id.in_(revision_ids), ContractRevision.status.in_(("draft", "proposed", "signed"))),
            "active": await count(ContractRevision, ContractRevision.id.in_(revision_ids), ContractRevision.status == "active"),
            "total": await count(Contract, Contract.id.in_(contract_ids)),
        },
        "compute_jobs": {
            "waiting_or_ready": await count(ComputeJob, ComputeJob.id.in_(job_ids), ComputeJob.status.in_(("created", "validated", "queued", "dispatched"))),
            "total": await count(ComputeJob, ComputeJob.id.in_(job_ids)),
        },
        "running_compute_runs": await count(ComputeRun, ComputeRun.id.in_(run_ids), ComputeRun.status == "running"),
        "quarantined_artifacts": await count(Artifact, Artifact.id.in_(artifact_ids), Artifact.release_status == "quarantined"),
        "available_release_packages": await count(ApprovedResultPackage, ApprovedResultPackage.artifact_id.in_(artifact_ids), ApprovedResultPackage.status == "available"),
        "active_download_grants": await count(
            ResultDownloadGrant,
            ResultDownloadGrant.result_package_id.in_(
                select(ApprovedResultPackage.id).where(ApprovedResultPackage.artifact_id.in_(artifact_ids))
            ),
            ResultDownloadGrant.status == "active",
        ),
        "audit_chain_valid": bool(chain["is_valid"]),
        "invalid_audit_sequence": chain["invalid_sequence"],
    }
    snapshot["blockers"] = [
        name
        for name, blocked in (
            ("running_compute_run", snapshot["running_compute_runs"] > 0),
            ("quarantined_artifact", snapshot["quarantined_artifacts"] > 0),
            ("invalid_audit_chain", not snapshot["audit_chain_valid"]),
        )
        if blocked
    ]
    return snapshot


async def create_lifecycle_request(
    session: AsyncSession,
    *,
    space_id: UUID,
    target_type: str,
    product_id: UUID,
    action: str,
    actor: DemoActor,
    reason: str,
    details: dict,
    raw_key: str,
) -> ProductLifecycleRequest:
    product = await _target(session, target_type=target_type, product_id=product_id, lock=True)
    if product.space_id != space_id or product.provider_organization_id != actor.organization_id:
        raise LifecycleGovernanceError("只有产品所属机构可以提交生命周期申请")
    if action == "unpublish" and product.lifecycle_status != "active":
        raise LifecycleGovernanceError("只有已上架产品可以申请下架")
    if action == "relist" and product.lifecycle_status != "unpublished":
        raise LifecycleGovernanceError("只有已下架产品可以申请重新上架")
    if action == "archive" and product.lifecycle_status == "archived":
        raise LifecycleGovernanceError("产品已经归档")
    if action == "archive" and product.lifecycle_status != "unpublished":
        raise LifecycleGovernanceError("产品必须先完成下架，才能申请归档")
    protected_code = (
        PHASE4_DATA_PRODUCT_CODE
        if target_type == "data_product"
        else PHASE4_MODEL_PRODUCT_CODE
    )
    if action == "archive" and product.product_code == protected_code:
        raise LifecycleGovernanceError("主演示产品受保护，不能归档")
    if action == "relist" and details.get("content_changed"):
        raise LifecycleGovernanceError("产品内容已变化，请先创建并审核新版本")
    pending = await session.scalar(
        select(ProductLifecycleRequest).where(
            ProductLifecycleRequest.space_id == space_id,
            ProductLifecycleRequest.target_type == target_type,
            ProductLifecycleRequest.target_product_id == product_id,
            ProductLifecycleRequest.status == "pending",
        )
    )
    key_digest = digest_idempotency_key(raw_key)
    if pending is not None:
        if pending.idempotency_digest == key_digest and pending.action == action:
            return pending
        raise LifecycleGovernanceError("该产品已有待审核的生命周期申请")
    publication = await _active_publication(session, target_type, product_id)
    version_id = None
    if publication is not None:
        version_id = (
            publication.data_product_version_id
            if target_type == "data_product"
            else publication.model_version_id
        )
    elif action in {"relist", "archive"}:
        version_model = DataProductVersion if target_type == "data_product" else ModelVersion
        product_field = (
            DataProductVersion.data_product_id
            if target_type == "data_product"
            else ModelVersion.model_product_id
        )
        version_id = await session.scalar(
            select(version_model.id)
            .where(product_field == product_id, version_model.status == "approved")
            .order_by(version_model.version_no.desc())
            .limit(1)
        )
    impact = await build_impact_snapshot(
        session,
        space_id=space_id,
        target_type=target_type,
        product_id=product_id,
        version_id=version_id,
    )
    request_row = ProductLifecycleRequest(
        space_id=space_id,
        target_type=target_type,
        target_product_id=product_id,
        target_version_id=version_id,
        action=action,
        requested_by_user_id=actor.user_id,
        requested_by_organization_id=actor.organization_id,
        reason=reason.strip(),
        details=details,
        impact_snapshot=impact,
        impact_digest=canonical_json_digest_v1(impact),
        idempotency_digest=key_digest,
    )
    session.add(request_row)
    await session.flush()
    command = _command(actor, request_row.id, "request", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type=f"{_event_prefix(target_type)}.{_event_action(action)}.requested",
        subject_type="product_lifecycle_request",
        subject_id=request_row.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.9/lifecycle-request/v1",
            "action": action,
            "product_id": str(product_id),
            "impact_digest": request_row.impact_digest,
            "state_before": product.lifecycle_status,
            "state_after": product.lifecycle_status,
        },
        **command.append_kwargs(),
    )
    return request_row


async def decide_lifecycle_request(
    session: AsyncSession,
    *,
    request_id: UUID,
    actor: DemoActor,
    decision: str,
    comment: str,
    raw_key: str,
) -> ProductLifecycleRequest:
    row = await session.scalar(
        select(ProductLifecycleRequest)
        .where(ProductLifecycleRequest.id == request_id)
        .with_for_update()
    )
    if row is None:
        raise LifecycleGovernanceError("生命周期申请不存在")
    if row.status != "pending":
        command = _command(actor, row.id, f"decision:{decision}", raw_key)
        exact_replay = await session.scalar(
            select(AuditEvent.event_id).where(
                AuditEvent.space_id == row.space_id,
                AuditEvent.subject_type == "product_lifecycle_request",
                AuditEvent.subject_id == row.id,
                AuditEvent.command_id == command.command_id,
                AuditEvent.idempotency_key == command.idempotency_key,
                AuditEvent.event_type
                == f"{_event_prefix(row.target_type)}.{_event_action(row.action)}.{decision}",
            )
        )
        if row.decision == decision and exact_replay is not None:
            return row
        raise LifecycleGovernanceError("生命周期申请已经处理")
    product = await _target(
        session, target_type=row.target_type, product_id=row.target_product_id, lock=True
    )
    impact = await build_impact_snapshot(
        session,
        space_id=row.space_id,
        target_type=row.target_type,
        product_id=row.target_product_id,
        version_id=row.target_version_id,
    )
    row.impact_snapshot = impact
    row.impact_digest = canonical_json_digest_v1(impact)
    now = datetime.now(timezone.utc)
    if decision == "approved":
        if impact["blockers"]:
            raise LifecycleGovernanceError("存在阻断项，不能批准该生命周期申请")
        if row.action == "unpublish":
            publication = await _active_publication(session, row.target_type, product.id)
            if publication is None:
                raise LifecycleGovernanceError("产品当前没有有效上架记录")
            publication.status = "withdrawn"
            if row.target_type == "data_product":
                publication.withdrawn_at = now
                publication.withdrawn_by = actor.user_id
                publication.withdrawal_reason = row.reason
            else:
                publication.ended_at = now
            product.lifecycle_status = "unpublished"
            product.unpublished_at = now
            effect = "unpublished"
        elif row.action == "relist":
            if product.lifecycle_status != "unpublished" or row.target_version_id is None:
                raise LifecycleGovernanceError("产品当前不能重新上架")
            if row.target_type == "data_product":
                publication = DataProductPublication(
                    space_id=row.space_id,
                    data_product_id=product.id,
                    data_product_version_id=row.target_version_id,
                    status="active",
                    visibility="space",
                    published_by=actor.user_id,
                )
            else:
                publication = ModelPublication(
                    space_id=row.space_id,
                    model_product_id=product.id,
                    model_version_id=row.target_version_id,
                    status="active",
                    visibility="space",
                    published_by=actor.user_id,
                )
            session.add(publication)
            product.lifecycle_status = "active"
            effect = "republished"
        else:
            protected_code = (
                PHASE4_DATA_PRODUCT_CODE
                if row.target_type == "data_product"
                else PHASE4_MODEL_PRODUCT_CODE
            )
            if product.product_code == protected_code:
                raise LifecycleGovernanceError("主演示产品受保护，不能归档")
            active = await _active_publication(session, row.target_type, product.id)
            if active is not None:
                raise LifecycleGovernanceError("已上架产品必须先完成下架")
            product.lifecycle_status = "archived"
            product.deleted_at = now
            effect = "archived"
        row.status = "approved"
    elif decision in {"rejected", "returned"}:
        row.status = decision
        effect = decision
    else:
        raise LifecycleGovernanceError("不支持的审核决定")
    row.decision = decision
    row.review_comment = comment.strip()
    row.reviewed_by_user_id = actor.user_id
    row.reviewed_at = now
    row.updated_at = now
    row.row_version += 1
    product.row_version += 1
    await session.flush()
    command = _command(actor, row.id, f"decision:{decision}", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=row.space_id,
        event_type=f"{_event_prefix(row.target_type)}.{_event_action(row.action)}.{decision}",
        subject_type="product_lifecycle_request",
        subject_id=row.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.9/lifecycle-decision/v1",
            "decision": decision,
            "action": row.action,
            "impact_digest": row.impact_digest,
            "blockers": impact["blockers"],
        },
        **command.append_kwargs(),
    )
    if decision == "approved":
        await append_audit_event_with_outbox(
            session,
            space_id=row.space_id,
            event_type=f"{_event_prefix(row.target_type)}.{effect}",
            subject_type="product_lifecycle_request",
            subject_id=row.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.9/lifecycle-effect/v1",
                "action": row.action,
                "product_id": str(product.id),
                "state_after": product.lifecycle_status,
            },
            **command.append_kwargs(),
        )
    return row


async def cancel_lifecycle_request(
    session: AsyncSession, *, request_id: UUID, actor: DemoActor, raw_key: str
) -> ProductLifecycleRequest:
    row = await session.scalar(
        select(ProductLifecycleRequest).where(ProductLifecycleRequest.id == request_id).with_for_update()
    )
    if row is None or row.requested_by_organization_id != actor.organization_id:
        raise LifecycleGovernanceError("生命周期申请不存在")
    if row.status != "pending":
        command = _command(actor, row.id, "cancel", raw_key)
        exact_replay = await session.scalar(
            select(AuditEvent.event_id).where(
                AuditEvent.space_id == row.space_id,
                AuditEvent.subject_type == "product_lifecycle_request",
                AuditEvent.subject_id == row.id,
                AuditEvent.command_id == command.command_id,
                AuditEvent.idempotency_key == command.idempotency_key,
                AuditEvent.event_type == "product.lifecycle.cancelled",
            )
        )
        if row.status == "cancelled" and exact_replay is not None:
            return row
        raise LifecycleGovernanceError("只有待审核申请可以撤回")
    row.status = "cancelled"
    row.decision = "cancelled"
    row.updated_at = datetime.now(timezone.utc)
    row.row_version += 1
    await session.flush()
    await append_audit_event_with_outbox(
        session,
        space_id=row.space_id,
        event_type="product.lifecycle.cancelled",
        subject_type="product_lifecycle_request",
        subject_id=row.id,
        result="success",
        evidence_snapshot={"schema_version": "phase5.9/lifecycle-cancel/v1", "action": row.action},
        **_command(actor, row.id, "cancel", raw_key).append_kwargs(),
    )
    return row
