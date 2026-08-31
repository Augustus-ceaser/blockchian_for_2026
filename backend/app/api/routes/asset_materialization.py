from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.data_products import _actor
from app.db.session import get_db_session
from app.modules.asset_materialization.models import AssetMaterializationPlan
from app.modules.asset_materialization.services import (
    MaterializationPlanError,
    cancel_plan,
    create_plan,
    decide_plan,
    submit_plan,
)
from app.modules.dataset_model_evidence.models import DatasetModelRelation

router = APIRouter(tags=["asset-materialization-plans"])


class MaterializationPlanRequest(BaseModel):
    data_plan: dict[str, Any]
    model_plan: dict[str, Any]
    transformation_plan: dict[str, Any]
    execution_goal: dict[str, Any]
    data_estimated_bytes: int = Field(ge=0)
    model_estimated_bytes: int = Field(ge=0)
    derived_estimated_bytes: int = Field(ge=0)
    hardware_requirements: dict[str, Any]
    network_allowlist: list[str] = Field(max_length=20)
    asset_file_allowlist: list[dict[str, Any]] = Field(max_length=200)
    license_snapshot: dict[str, Any]
    access_snapshot: dict[str, Any]
    security_preflight: dict[str, Any]
    blocking_reasons: list[str] = Field(default_factory=list, max_length=40)
    supersedes_plan_id: UUID | None = None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reasons: list[str] = Field(default_factory=list, max_length=40)


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(400, "Idempotency-Key is required.")
    return value.strip()


def _payload(plan: AssetMaterializationPlan) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "relation_id": str(plan.relation_id),
        "data_product_version_id": str(plan.data_product_version_id),
        "model_product_version_id": str(plan.model_product_version_id),
        "relation_evidence_id": str(plan.relation_evidence_id),
        "plan_status": plan.plan_status,
        "data_plan": plan.data_plan,
        "model_plan": plan.model_plan,
        "transformation_plan": plan.transformation_plan,
        "execution_goal": plan.execution_goal,
        "data_estimated_bytes": plan.data_estimated_bytes,
        "model_estimated_bytes": plan.model_estimated_bytes,
        "derived_estimated_bytes": plan.derived_estimated_bytes,
        "total_estimated_bytes": plan.total_estimated_bytes,
        "hardware_requirements": plan.hardware_requirements,
        "network_allowlist": plan.network_allowlist,
        "asset_file_allowlist": plan.asset_file_allowlist,
        "license_snapshot": plan.license_snapshot,
        "access_snapshot": plan.access_snapshot,
        "security_preflight": plan.security_preflight,
        "blocking_reasons": plan.blocking_reasons,
        "version_locks": {
            "data_version_digest": plan.data_version_digest,
            "model_version_digest": plan.model_version_digest,
            "data_source_digest": plan.data_source_digest,
            "model_source_digest": plan.model_source_digest,
            "data_governance_digest": plan.data_governance_digest,
            "model_governance_digest": plan.model_governance_digest,
            "relation_evidence_digest": plan.relation_evidence_digest,
        },
        "plan_digest": plan.plan_digest,
        "created_by": str(plan.created_by),
        "submitted_by": str(plan.submitted_by) if plan.submitted_by else None,
        "approved_by": str(plan.approved_by) if plan.approved_by else None,
        "rejection_reasons": plan.rejection_reasons,
        "supersedes_plan_id": (
            str(plan.supersedes_plan_id) if plan.supersedes_plan_id else None
        ),
        "created_at": plan.created_at.isoformat(),
        "submitted_at": plan.submitted_at.isoformat() if plan.submitted_at else None,
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "decided_at": plan.decided_at.isoformat() if plan.decided_at else None,
        "asset_downloaded": False,
        "data_materialized": False,
        "model_materialized": False,
        "executor_registered": False,
        "execution_ready": False,
    }


async def _read_context(session: AsyncSession, identity: str):
    context, _ = await _actor(session, identity)
    return context


@router.get("/materialization-plans")
async def list_materialization_plans(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context = await _read_context(session, identity)
    rows = list(
        (
            await session.scalars(
                select(AssetMaterializationPlan)
                .where(AssetMaterializationPlan.space_id == context.space_id)
                .order_by(AssetMaterializationPlan.created_at.desc())
            )
        ).all()
    )
    return {"items": [_payload(row) for row in rows], "total": len(rows)}


@router.get("/materialization-plans/{plan_id}")
async def materialization_plan_detail(
    plan_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context = await _read_context(session, identity)
    plan = await session.get(AssetMaterializationPlan, plan_id)
    if plan is None or plan.space_id != context.space_id:
        raise HTTPException(404, "Materialization plan not found.")
    return _payload(plan)


@router.get("/dataset-model-relations/{relation_id}/materialization-plans")
async def relation_materialization_plans(
    relation_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context = await _read_context(session, identity)
    relation = await session.get(DatasetModelRelation, relation_id)
    if relation is None or relation.space_id != context.space_id:
        raise HTTPException(404, "Relation not found.")
    rows = list(
        (
            await session.scalars(
                select(AssetMaterializationPlan)
                .where(AssetMaterializationPlan.relation_id == relation_id)
                .order_by(AssetMaterializationPlan.created_at.desc())
            )
        ).all()
    )
    return {"items": [_payload(row) for row in rows], "total": len(rows)}


@router.post("/dataset-model-relations/{relation_id}/materialization-plans")
async def create_materialization_plan(
    relation_id: UUID,
    payload: MaterializationPlanRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, expected="catalog_curator")
            plan, created = await create_plan(
                session,
                actor=actor,
                relation_id=relation_id,
                payload=payload.model_dump(mode="json"),
                raw_key=_key(idempotency_key),
            )
        return {"created": created, "plan": _payload(plan)}
    except MaterializationPlanError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/materialization-plans/{plan_id}/submit")
async def submit_materialization_plan(
    plan_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, expected="catalog_curator")
            plan, changed = await submit_plan(
                session, actor=actor, plan_id=plan_id, raw_key=_key(idempotency_key)
            )
        return {"changed": changed, "plan": _payload(plan)}
    except MaterializationPlanError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/materialization-plans/{plan_id}/approve")
async def approve_materialization_plan(
    plan_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, expected="space_operator")
            plan, changed = await decide_plan(
                session,
                actor=actor,
                plan_id=plan_id,
                approve=True,
                reasons=[],
                raw_key=_key(idempotency_key),
            )
        return {"changed": changed, "plan": _payload(plan)}
    except MaterializationPlanError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/materialization-plans/{plan_id}/reject")
async def reject_materialization_plan(
    plan_id: UUID,
    payload: DecisionRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    if payload.decision != "reject":
        raise HTTPException(422, "Decision must be reject.")
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, expected="space_operator")
            plan, changed = await decide_plan(
                session,
                actor=actor,
                plan_id=plan_id,
                approve=False,
                reasons=payload.reasons,
                raw_key=_key(idempotency_key),
            )
        return {"changed": changed, "plan": _payload(plan)}
    except MaterializationPlanError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/materialization-plans/{plan_id}/cancel")
async def cancel_materialization_plan(
    plan_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, expected="space_operator")
            plan, changed = await cancel_plan(
                session, actor=actor, plan_id=plan_id, raw_key=_key(idempotency_key)
            )
        return {"changed": changed, "plan": _payload(plan)}
    except MaterializationPlanError as exc:
        raise HTTPException(409, str(exc)) from exc
