from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.connector_control import _ingress_connector
from app.api.routes.data_products import _actor
from app.db.session import get_db_session
from app.modules.applications.models import Application
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.connector_control.models import (
    ConnectorAssetMirror, ConnectorAssetMirrorVersion, HospitalConnector,
    HospitalExecutorMirror,
)
from app.modules.contracts.models import Contract, ContractRevision
from app.modules.marketplace.models import ModelProduct, ModelVersion
from app.modules.policy_control.models import (
    ConnectorOrderDecision, ConnectorOrderReceipt, ControlReadinessSnapshot,
    ExecutionOrder, ExecutionOrderConsumptionReceipt, PolicyBundle,
    PolicyBundleVersion, PolicyRevocation, PolicySigningKey,
)
from app.modules.policy_control.services import (
    PolicyControlError, accept_decision, accept_execution_consumption,
    accept_receipt, compile_policy,
    ensure_active_signing_key, issue_order, revoke_policy, sign_activate_policy,
)

router = APIRouter(prefix="/policy-control", tags=["signed-policy-control-alpha"])


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: UUID
    executor_mirror_id: UUID | None = None
    application_id: UUID
    contract_id: UUID
    asset_version_id: UUID
    model_version_id: UUID
    purpose_code: str = Field(min_length=3, max_length=80)
    execution_mode: Literal[
        "CONTROL_POLICY_VALIDATION", "FIXED_REFERENCE_EXECUTION"
    ] = "CONTROL_POLICY_VALIDATION"


class OrderRequest(BaseModel):
    policy_bundle_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=100)


class RevokeRequest(BaseModel):
    reason_code: str = Field(min_length=3, max_length=64)
    reason_text: str = Field(min_length=3, max_length=500)


class SignedConnectorMessage(BaseModel):
    payload: dict[str, Any]
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signature: str = Field(min_length=40, max_length=4096)


async def _context(session: AsyncSession, identity: str):
    return await _actor(session, identity)


def _error(exc: PolicyControlError) -> HTTPException:
    message = str(exc)
    if "operator" in message:
        return HTTPException(403, message)
    if "NOT_FOUND" in message or "not found" in message:
        return HTTPException(404, message)
    if "SIGNATURE" in message or "DIGEST" in message or "INVALID" in message:
        return HTTPException(400, message)
    return HTTPException(409, message)


async def _verified_policy_connector(
    session: AsyncSession,
    connector_id: UUID,
    client_certificate: str,
    ingress_verified: str,
    request: Request,
) -> HospitalConnector:
    connector, certificate = await _ingress_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    if connector.status != "active":
        raise HTTPException(403, "active Connector required")
    if (
        certificate.status != "active"
        or certificate.valid_to <= datetime.now().astimezone()
    ):
        raise HTTPException(403, "active Connector certificate required")
    return connector


def _bundle_payload(row: PolicyBundle, version: PolicyBundleVersion | None) -> dict[str, Any]:
    return {
        "id": str(row.id), "policy_key": row.policy_key, "connector_id": str(row.connector_id),
        "application_id": str(row.application_id), "contract_id": str(row.contract_id),
        "control_readiness_id": str(row.control_readiness_id), "status": row.status,
        "created_at": row.created_at, "activated_at": row.activated_at,
        "expires_at": row.expires_at, "revoked_at": row.revoked_at,
        "version": {
            "id": str(version.id), "schema_version": version.schema_version,
            "version": version.version, "payload_digest": version.payload_digest,
            "signing_key_id": version.signing_key_id, "signature": version.signature,
            "canonical_payload": version.canonical_payload,
            "execution_authorized": version.execution_authorized,
            "requested_action": version.requested_action,
            "execution_scope": version.execution_scope,
            "task_type": version.task_type,
            "max_execution_count": version.max_execution_count,
        } if version else None,
    }


def _order_payload(
    row: ExecutionOrder, receipt=None, decision=None, consumption=None
) -> dict[str, Any]:
    return {
        "id": str(row.id), "order_key": row.order_key, "order_mode": row.order_mode,
        "requested_action": row.requested_action, "policy_bundle_id": str(row.policy_bundle_id),
        "policy_bundle_version_id": str(row.policy_bundle_version_id),
        "connector_id": str(row.connector_id), "connector_sequence": row.connector_sequence,
        "payload_digest": row.payload_digest, "signing_key_id": row.signing_key_id,
        "status": row.status, "issued_at": row.issued_at, "expires_at": row.expires_at,
        "execution_authorized": row.execution_authorized,
        "execution_scope": row.execution_scope,
        "task_type": row.task_type,
        "max_execution_count": row.max_execution_count,
        "consumed_count": row.consumed_count,
        "execution_started": row.consumed_count > 0,
        "execution_completed": False,
        "display_status": (
            (
                (
                    "Fixed reference authorization consumed - local execution"
                    if row.consumed_count
                    else "Accepted fixed reference authorization - not executed"
                )
                if row.execution_authorized
                else "Accepted for control validation only - not executed"
            )
            if row.status == "accepted"
            else row.status
        ),
        "receipt": {
            "payload_digest": receipt.payload_digest, "payload": receipt.receipt_payload,
            "received_at": receipt.received_at,
        } if receipt else None,
        "decision": {
            "decision": decision.decision, "reason_code": decision.reason_code,
            "reason_text": decision.reason_text, "payload_digest": decision.payload_digest,
            "received_at": decision.received_at,
        } if decision else None,
        "consumption": {
            "id": str(consumption.id),
            "payload_digest": consumption.payload_digest,
            "authorization_snapshot_id":
                consumption.authorization_snapshot_id,
            "task_manifest_id": consumption.task_manifest_id,
            "runtime_session_id": consumption.runtime_session_id,
            "reference_execution_id": consumption.reference_execution_id,
            "received_at": consumption.received_at,
        } if consumption else None,
    }


@router.get("/sources")
async def sources(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role != "space_operator":
        raise HTTPException(403, "only the platform operator may compile policy")
    connectors = (await session.scalars(select(HospitalConnector).where(
        HospitalConnector.space_id == context.space_id, HospitalConnector.status == "active"
    ))).all()
    applications = (await session.scalars(select(Application).where(
        Application.space_id == context.space_id, Application.status == "approved"
    ))).all()
    contracts = (await session.scalars(
        select(Contract).join(ContractRevision, ContractRevision.contract_id == Contract.id).where(
            Contract.space_id == context.space_id, ContractRevision.status == "active"
        )
    )).all()
    assets = (await session.scalars(select(ConnectorAssetMirror).where(
        ConnectorAssetMirror.space_id == context.space_id, ConnectorAssetMirror.status == "synced"
    ))).all()
    current_asset_version_ids = [
        row.current_version_id for row in assets if row.current_version_id is not None
    ]
    asset_versions = (
        (
            await session.scalars(
                select(ConnectorAssetMirrorVersion).where(
                    ConnectorAssetMirrorVersion.id.in_(current_asset_version_ids)
                )
            )
        ).all()
        if current_asset_version_ids
        else []
    )
    models = (await session.execute(
        select(ModelVersion, ModelProduct.name).join(ModelProduct, ModelProduct.id == ModelVersion.model_product_id)
        .where(ModelVersion.space_id == context.space_id, ModelVersion.status.in_(("approved", "published")))
    )).all()
    executors = (await session.scalars(select(HospitalExecutorMirror).where(
        HospitalExecutorMirror.space_id == context.space_id,
        HospitalExecutorMirror.status == "active",
        HospitalExecutorMirror.fixed_reference_readiness_status == "ready",
        HospitalExecutorMirror.latest_status_event_id
        == HospitalExecutorMirror.latest_verified_readiness_event_id,
    ))).all()
    return {
        "connectors": [{"id": str(row.id), "label": row.display_name} for row in connectors],
        "executors": [{
            "id": str(row.id), "connector_id": str(row.connector_id),
            "label": row.executor_instance_id,
            "valid_until": row.readiness_valid_until,
            "source_event_id": str(row.latest_verified_readiness_event_id),
        } for row in executors],
        "applications": [{"id": str(row.id), "label": row.application_number, "provider_organization_id": str(row.provider_organization_id)} for row in applications],
        "contracts": [{"id": str(row.id), "application_id": str(row.application_id), "label": row.contract_number} for row in contracts],
        "asset_versions": [{
            "id": str(row.id), "connector_id": str(row.connector_id),
            "label": f"{next((asset.display_name for asset in assets if asset.id == row.mirror_id), 'Asset')} / {row.version_label}",
            "metadata_digest": row.metadata_digest, "quality_digest": row.quality_digest,
        } for row in asset_versions],
        "model_versions": [{
            "id": str(row.ModelVersion.id), "label": f"{row.name} / {row.ModelVersion.version_label}",
            "reference_digest": row.ModelVersion.snapshot_digest or row.ModelVersion.model_digest,
            "materialization_status": "NOT_EVALUATED_IN_PHASE_5_13D",
        } for row in models],
        "boundary": {
            "fixed_reference_execution_available": True,
            "formal_execution_started": False,
            "hard_isolation": False,
        },
    }


@router.get("/readiness")
async def readiness_snapshots(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role not in {"space_operator", "data_provider"}:
        raise HTTPException(403, "execution readiness is not available")
    query = select(ControlReadinessSnapshot).where(
        ControlReadinessSnapshot.space_id == context.space_id
    )
    if actor.role == "data_provider":
        query = query.join(
            HospitalConnector,
            HospitalConnector.id == ControlReadinessSnapshot.connector_id,
        ).where(
            HospitalConnector.organization_id == actor.organization_id
        )
    rows = (
        await session.scalars(
            query.order_by(ControlReadinessSnapshot.created_at.desc())
        )
    ).all()
    return {
        "items": [{
            "id": str(row.id),
            "readiness_mode": row.readiness_mode,
            "requested_action": row.requested_action,
            "task_type": row.task_type,
            "status": row.status,
            "readiness_digest": row.readiness_digest,
            "source_executor_status_event_id": (
                str(row.source_executor_status_event_id)
                if row.source_executor_status_event_id else None
            ),
            "source_executor_status_event_digest":
                row.source_executor_status_event_digest,
            "expires_at": row.expires_at,
            "execution_authorized": row.execution_authorized,
            "hard_isolation": False,
            "checks": row.checks,
        } for row in rows],
        "total": len(rows),
    }


@router.get("/readiness/{readiness_id}")
async def readiness_detail(
    readiness_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role not in {"space_operator", "data_provider"}:
        raise HTTPException(403, "execution readiness is not available")
    row = await session.get(ControlReadinessSnapshot, readiness_id)
    if row is None or row.space_id != context.space_id:
        raise HTTPException(404, "execution readiness not found")
    if actor.role == "data_provider":
        connector = await session.get(HospitalConnector, row.connector_id)
        if (
            connector is None
            or connector.organization_id != actor.organization_id
        ):
            raise HTTPException(404, "execution readiness not found")
    return {
        "id": str(row.id),
        "readiness_mode": row.readiness_mode,
        "requested_action": row.requested_action,
        "task_type": row.task_type,
        "status": row.status,
        "readiness_digest": row.readiness_digest,
        "source_executor_status_event_id": (
            str(row.source_executor_status_event_id)
            if row.source_executor_status_event_id else None
        ),
        "source_executor_status_event_digest":
            row.source_executor_status_event_digest,
        "source_attestation_expires_at": row.source_attestation_expires_at,
        "source_asset_version_id": (
            str(row.source_asset_version_id)
            if row.source_asset_version_id else None
        ),
        "source_asset_metadata_digest": row.source_asset_metadata_digest,
        "source_quality_digest": row.source_quality_digest,
        "source_model_reference_digest": row.source_model_reference_digest,
        "source_contract_digest": row.source_contract_digest,
        "source_application_digest": row.source_application_digest,
        "computed_at": row.computed_at,
        "expires_at": row.expires_at,
        "execution_authorized": row.execution_authorized,
        "hard_isolation": False,
        "checks": row.checks,
    }


@router.post("/signing-keys/ensure")
async def ensure_key(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    try:
        row = await ensure_active_signing_key(session, actor=actor, space_id=context.space_id)
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"key_id": row.key_id, "algorithm": row.algorithm, "fingerprint": row.public_key_fingerprint, "status": row.status}


@router.post("/policies/compile")
async def compile_bundle(
    request: CompileRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    try:
        bundle, version, readiness = await compile_policy(
            session, actor=actor, space_id=context.space_id, **request.model_dump()
        )
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {**_bundle_payload(bundle, version), "readiness": {"id": str(readiness.id), "status": readiness.status, "digest": readiness.readiness_digest, "checks": readiness.checks}}


@router.post("/policies/{bundle_id}/sign-activate")
async def sign_bundle(
    bundle_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    try:
        bundle, version = await sign_activate_policy(session, actor=actor, space_id=context.space_id, bundle_id=bundle_id)
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return _bundle_payload(bundle, version)


@router.get("/policies")
async def policies(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role not in {"space_operator", "data_provider"}:
        raise HTTPException(403, "policy details are not available to this role")
    query = select(PolicyBundle).where(PolicyBundle.space_id == context.space_id)
    if actor.role == "data_provider":
        query = query.where(PolicyBundle.organization_id == actor.organization_id)
    rows = (await session.scalars(query.order_by(PolicyBundle.created_at.desc()))).all()
    result = []
    for row in rows:
        result.append(_bundle_payload(row, await session.get(PolicyBundleVersion, row.current_version_id)))
    return {"items": result, "total": len(result)}


@router.get("/policies/{bundle_id}")
async def policy_detail(
    bundle_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    row = await session.get(PolicyBundle, bundle_id)
    if row is None or row.space_id != context.space_id:
        raise HTTPException(404, "policy not found")
    if actor.role == "data_provider" and row.organization_id != actor.organization_id:
        raise HTTPException(404, "policy not found")
    if actor.role not in {"space_operator", "data_provider"}:
        raise HTTPException(403, "policy details are not available to this role")
    return _bundle_payload(row, await session.get(PolicyBundleVersion, row.current_version_id))


@router.post("/orders")
async def create_order(
    request: OrderRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    try:
        row = await issue_order(session, actor=actor, space_id=context.space_id, bundle_id=request.policy_bundle_id, idempotency_key=request.idempotency_key)
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return _order_payload(row)


@router.get("/orders")
async def orders(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    if actor.role not in {"space_operator", "data_provider"}:
        raise HTTPException(403, "control orders are not available to this role")
    query = select(ExecutionOrder).where(ExecutionOrder.space_id == context.space_id)
    if actor.role == "data_provider":
        query = query.join(HospitalConnector, HospitalConnector.id == ExecutionOrder.connector_id).where(
            HospitalConnector.organization_id == actor.organization_id
        )
    rows = (await session.scalars(query.order_by(ExecutionOrder.created_at.desc()))).all()
    return {"items": [_order_payload(row) for row in rows], "total": len(rows)}


@router.get("/orders/{order_id}")
async def order_detail(
    order_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    row = await session.get(ExecutionOrder, order_id)
    if row is None or row.space_id != context.space_id:
        raise HTTPException(404, "order not found")
    if actor.role == "data_provider":
        connector = await session.get(HospitalConnector, row.connector_id)
        if connector.organization_id != actor.organization_id:
            raise HTTPException(404, "order not found")
    elif actor.role != "space_operator":
        raise HTTPException(403, "control orders are not available to this role")
    receipt = await session.scalar(select(ConnectorOrderReceipt).where(ConnectorOrderReceipt.execution_order_id == row.id))
    decision = await session.scalar(select(ConnectorOrderDecision).where(ConnectorOrderDecision.execution_order_id == row.id))
    consumption = await session.scalar(
        select(ExecutionOrderConsumptionReceipt).where(
            ExecutionOrderConsumptionReceipt.execution_order_id == row.id
        )
    )
    return _order_payload(row, receipt, decision, consumption)


@router.post("/policies/{bundle_id}/revoke")
async def revoke_bundle(
    bundle_id: UUID, request: RevokeRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _context(session, identity)
    try:
        row = await revoke_policy(session, actor=actor, space_id=context.space_id, bundle_id=bundle_id, **request.model_dump())
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"id": str(row.id), "revocation_id": row.revocation_id, "payload_digest": row.payload_digest, "effective_at": row.effective_at}


@router.get("/side-effect-counts")
async def side_effect_counts(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _context(session, identity)
    if actor.role != "space_operator":
        raise HTTPException(403, "only the platform operator may view control acceptance counts")
    return {
        "compute_jobs": await session.scalar(select(func.count()).select_from(ComputeJob)),
        "compute_runs": await session.scalar(select(func.count()).select_from(ComputeRun)),
        "artifacts": await session.scalar(select(func.count()).select_from(Artifact)),
        "execution_count": 0,
    }


@router.get("/ingress/connectors/{connector_id}/orders/available")
async def pull_orders(
    connector_id: UUID, request: Request, after_sequence: int = 0,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    connector = await _verified_policy_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    rows = (await session.scalars(select(ExecutionOrder).where(
        ExecutionOrder.connector_id == connector.id,
        ExecutionOrder.connector_sequence > after_sequence,
        ExecutionOrder.status.in_(("available_for_connector", "delivered", "revoked")),
    ).order_by(ExecutionOrder.connector_sequence).limit(20))).all()
    items = []
    for row in rows:
        version = await session.get(PolicyBundleVersion, row.policy_bundle_version_id)
        key = await session.scalar(select(PolicySigningKey).where(PolicySigningKey.key_id == row.signing_key_id))
        items.append({
            "execution_order_id": str(row.id),
            "order_key": row.order_key,
            "order": row.canonical_payload, "order_digest": row.payload_digest,
            "order_signature": row.signature,
            "policy": version.canonical_payload, "policy_digest": version.payload_digest,
            "policy_signature": version.signature,
            "signing_key": {
                "key_id": key.key_id, "algorithm": key.algorithm,
                "public_key_material": key.public_key_material,
                "fingerprint": key.public_key_fingerprint, "status": key.status,
            },
            "central_status": row.status,
        })
        if row.status == "available_for_connector":
            row.status = "delivered"
            row.delivered_at = datetime.now().astimezone()
    await session.commit()
    return {"items": items, "total": len(items)}


@router.post("/ingress/connectors/{connector_id}/orders/{order_id}/receipt")
async def post_receipt(
    connector_id: UUID, order_id: UUID, message: SignedConnectorMessage, request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    connector = await _verified_policy_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    if message.payload.get("execution_order_id") != str(order_id):
        raise HTTPException(400, "receipt order mismatch")
    try:
        row = await accept_receipt(session, connector=connector, payload=message.payload, digest=message.payload_digest, signature=message.signature)
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"id": str(row.id), "accepted": True}


@router.post("/ingress/connectors/{connector_id}/orders/{order_id}/decision")
async def post_decision(
    connector_id: UUID, order_id: UUID, message: SignedConnectorMessage, request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    connector = await _verified_policy_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    if message.payload.get("execution_order_id") != str(order_id):
        raise HTTPException(400, "decision order mismatch")
    try:
        row = await accept_decision(session, connector=connector, payload=message.payload, digest=message.payload_digest, signature=message.signature)
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"id": str(row.id), "accepted": True}


@router.post(
    "/ingress/connectors/{connector_id}/orders/{order_id}/consumption"
)
async def post_execution_consumption(
    connector_id: UUID, order_id: UUID, message: SignedConnectorMessage,
    request: Request,
    client_certificate: str = Header(alias="X-Client-Certificate"),
    ingress_verified: str = Header(alias="X-Connector-Ingress-Verified"),
    session: AsyncSession = Depends(get_db_session),
):
    connector = await _verified_policy_connector(
        session, connector_id, client_certificate, ingress_verified, request
    )
    if message.payload.get("execution_order_id") != str(order_id):
        raise HTTPException(400, "consumption order mismatch")
    try:
        row = await accept_execution_consumption(
            session,
            connector=connector,
            payload=message.payload,
            digest=message.payload_digest,
            signature=message.signature,
        )
        await session.commit()
    except PolicyControlError as exc:
        await session.rollback()
        raise _error(exc) from exc
    return {"id": str(row.id), "accepted": True}
