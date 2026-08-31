from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.data_products import _actor
from app.db.session import get_db_session
from app.modules.catalog.models import DataProduct, DataProductPublication, DataProductVersion
from app.modules.dataset_model_evidence.models import DatasetModelEvidence, DatasetModelRelation
from app.modules.dataset_model_evidence.services import (
    DatasetModelEvidenceError,
    append_operator_evidence,
)
from app.modules.marketplace.models import ModelProduct, ModelPublication, ModelVersion

router = APIRouter(tags=["dataset-model-evidence"])


class StaticReviewRequest(BaseModel):
    data_product_version_id: UUID
    model_product_version_id: UUID
    evidence_type: Literal[
        "static_schema_compatible",
        "static_schema_compatible_with_transformation",
        "static_schema_incompatible",
        "insufficient_metadata",
    ]
    outcome: Literal["supports", "contradicts", "inconclusive"] = "supports"
    evidence_scope: Literal[
        "input_schema", "preprocessing", "task", "modality", "format",
        "resolution", "label_schema",
    ] = "input_schema"
    evidence_note: str = Field(min_length=20, max_length=2000)
    structured_assessment: dict[str, Any]
    transformation_requirements: list[dict[str, Any]] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    warning_reasons: list[str] = Field(default_factory=list)
    supersedes_evidence_id: UUID | None = None


class EvidenceRequest(StaticReviewRequest):
    evidence_type: Literal[
        "author_declared_training",
        "author_declared_evaluation",
        "author_declared_benchmark",
        "external_related_reference",
        "static_schema_compatible",
        "static_schema_compatible_with_transformation",
        "static_schema_incompatible",
        "insufficient_metadata",
        "executed",
        "execution_failed",
        "verified",
    ]
    evidence_reference: dict[str, Any] = Field(default_factory=dict)


async def _currently_public(
    session: AsyncSession, relation: DatasetModelRelation
) -> bool:
    if not relation.public_visible or not relation.active:
        return False
    data_product = await session.get(DataProduct, relation.data_product_id)
    data_version = await session.get(DataProductVersion, relation.data_product_version_id)
    model_product = await session.get(ModelProduct, relation.model_product_id)
    model_version = await session.get(ModelVersion, relation.model_product_version_id)
    if not all((data_product, data_version, model_product, model_version)):
        return False
    data_publication = await session.scalar(select(DataProductPublication.id).where(
        DataProductPublication.data_product_version_id == relation.data_product_version_id,
        DataProductPublication.status == "active",
    ))
    model_publication = await session.scalar(select(ModelPublication.id).where(
        ModelPublication.model_version_id == relation.model_product_version_id,
        ModelPublication.status == "active",
    ))
    return (
        data_product.lifecycle_status == "active"
        and data_version.status == "approved"
        and model_product.lifecycle_status == "active"
        and model_version.status == "approved"
        and data_publication is not None
        and model_publication is not None
    )


async def _payload(session: AsyncSession, relation: DatasetModelRelation) -> dict[str, Any]:
    dp = await session.get(DataProduct, relation.data_product_id)
    dv = await session.get(DataProductVersion, relation.data_product_version_id)
    mp = await session.get(ModelProduct, relation.model_product_id)
    mv = await session.get(ModelVersion, relation.model_product_version_id)
    evidence = (
        await session.scalar(select(DatasetModelEvidence).where(
            DatasetModelEvidence.id == relation.current_evidence_id
        ))
        if relation.current_evidence_id else None
    )
    return {
        "id": str(relation.id),
        "data_product": {"id": str(dp.id), "name": dp.name, "product_code": dp.product_code},
        "data_version": {"id": str(dv.id), "label": dv.version_label},
        "model_product": {"id": str(mp.id), "name": mp.name, "product_code": mp.product_code},
        "model_version": {"id": str(mv.id), "label": mv.version_label},
        "current_status": relation.current_status,
        "strongest_evidence_level": relation.strongest_evidence_level,
        "public_visible": await _currently_public(session, relation),
        "active": relation.active,
        "executed": relation.current_status in {"executed", "verified"},
        "verified": relation.current_status == "verified",
        "current_evidence": None if evidence is None else {
            "id": str(evidence.id),
            "evidence_level": evidence.evidence_level,
            "evidence_type": evidence.evidence_type,
            "outcome": evidence.outcome,
            "evidence_scope": evidence.evidence_scope,
            "evidence_reference": evidence.evidence_reference,
            "evidence_note": evidence.evidence_note,
            "structured_assessment": evidence.structured_assessment,
            "transformation_requirements": evidence.transformation_requirements,
            "blocking_reasons": evidence.blocking_reasons,
            "warning_reasons": evidence.warning_reasons,
            "created_at": evidence.created_at.isoformat(),
        },
        "version_locks": {
            "data_version_digest": relation.data_version_digest,
            "model_version_digest": relation.model_version_digest,
            "data_source_digest": relation.data_source_digest,
            "model_source_digest": relation.model_source_digest,
            "data_governance_digest": relation.data_governance_digest,
            "model_governance_digest": relation.model_governance_digest,
        },
    }


@router.get("/dataset-model-relations")
async def list_relations(
    matrix: bool = Query(default=False),
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    rows = list((await session.scalars(select(DatasetModelRelation).where(
        DatasetModelRelation.space_id == context.space_id,
        DatasetModelRelation.active.is_(True),
    ).order_by(DatasetModelRelation.created_at))).all())
    if identity != "space_operator":
        rows = [row for row in rows if await _currently_public(session, row)]
    items = [await _payload(session, row) for row in rows]
    if not matrix or identity != "space_operator":
        return {"items": items, "total": len(items)}
    data_rows = (await session.execute(
        select(DataProduct, DataProductVersion)
        .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
        .join(DataProductPublication, DataProductPublication.data_product_version_id == DataProductVersion.id)
        .where(DataProduct.space_id == context.space_id, DataProductPublication.status == "active")
        .order_by(DataProduct.name)
    )).all()
    model_rows = (await session.execute(
        select(ModelProduct, ModelVersion)
        .join(ModelVersion, ModelVersion.model_product_id == ModelProduct.id)
        .join(ModelPublication, ModelPublication.model_version_id == ModelVersion.id)
        .where(ModelProduct.space_id == context.space_id, ModelPublication.status == "active")
        .order_by(ModelProduct.name)
    )).all()
    return {
        "items": items,
        "total": len(items),
        "matrix": {
            "data_versions": [
                {"product_id": str(p.id), "version_id": str(v.id), "name": p.name,
                 "version": v.version_label, "metadata": v.scope_metadata}
                for p, v in data_rows
            ],
            "model_versions": [
                {"product_id": str(p.id), "version_id": str(v.id), "name": p.name,
                 "version": v.version_label, "metadata": v.compatibility_metadata}
                for p, v in model_rows
            ],
        },
    }


@router.get("/dataset-model-relations/{relation_id}")
async def relation_detail(
    relation_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    relation = await session.get(DatasetModelRelation, relation_id)
    if relation is None or relation.space_id != context.space_id:
        raise HTTPException(404, "Relation not found.")
    if identity != "space_operator" and not await _currently_public(session, relation):
        raise HTTPException(404, "Relation not found.")
    payload = await _payload(session, relation)
    history = list((await session.scalars(select(DatasetModelEvidence).where(
        DatasetModelEvidence.relation_id == relation.id
    ).order_by(DatasetModelEvidence.created_at.desc()))).all())
    payload["evidence_history"] = [
        {"id": str(item.id), "evidence_level": item.evidence_level,
         "evidence_type": item.evidence_type, "outcome": item.outcome,
         "supersedes_evidence_id": str(item.supersedes_evidence_id) if item.supersedes_evidence_id else None,
         "created_at": item.created_at.isoformat()}
        for item in history
    ]
    return payload


@router.get("/data-products/{data_product_id}/model-evidence")
async def data_model_evidence(
    data_product_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    rows = list((await session.scalars(select(DatasetModelRelation).where(
        DatasetModelRelation.space_id == context.space_id,
        DatasetModelRelation.data_product_id == data_product_id,
        DatasetModelRelation.active.is_(True),
    ))).all())
    rows = [row for row in rows if await _currently_public(session, row)]
    return {"items": [await _payload(session, row) for row in rows]}


@router.get("/model-products/{model_product_id}/dataset-evidence")
async def model_dataset_evidence(
    model_product_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    rows = list((await session.scalars(select(DatasetModelRelation).where(
        DatasetModelRelation.space_id == context.space_id,
        DatasetModelRelation.model_product_id == model_product_id,
        DatasetModelRelation.active.is_(True),
    ))).all())
    rows = [row for row in rows if await _currently_public(session, row)]
    return {"items": [await _payload(session, row) for row in rows]}


async def _write(
    payload: EvidenceRequest | StaticReviewRequest,
    identity: str,
    key: str | None,
    session: AsyncSession,
    expected_relation_id: UUID | None = None,
):
    if not key or len(key.strip()) < 8:
        raise HTTPException(400, "Idempotency-Key is required.")
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, expected="space_operator")
            relation, evidence, created = await append_operator_evidence(
                session, actor=actor,
                data_version_id=payload.data_product_version_id,
                model_version_id=payload.model_product_version_id,
                payload=payload.model_dump(mode="json"),
                raw_key=key.strip(),
            )
            if expected_relation_id is not None and relation.id != expected_relation_id:
                raise DatasetModelEvidenceError("relation version pair does not match")
        return {
            "relation_id": str(relation.id), "evidence_id": str(evidence.id),
            "current_status": relation.current_status,
            "public_visible": relation.public_visible, "created": created,
        }
    except DatasetModelEvidenceError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/dataset-model-relations/static-review")
async def static_review(
    payload: StaticReviewRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    return await _write(payload, identity, key, session)


@router.post("/dataset-model-relations/{relation_id}/evidence")
async def append_evidence(
    relation_id: UUID,
    payload: EvidenceRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    return await _write(
        payload, identity, key, session, expected_relation_id=relation_id
    )


@router.post("/dataset-model-relations/{relation_id}/recalculate")
async def recalculate_relation(
    relation_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, _ = await _actor(session, identity, expected="space_operator")
    relation = await session.get(DatasetModelRelation, relation_id)
    if relation is None:
        raise HTTPException(404, "Relation not found.")
    return await _payload(session, relation)


@router.post("/dataset-model-relations/{relation_id}/publish")
async def publish_relation(
    relation_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, _ = await _actor(session, identity, expected="space_operator")
    relation = await session.get(DatasetModelRelation, relation_id)
    if relation is None:
        raise HTTPException(404, "Relation not found.")
    if not relation.public_visible:
        raise HTTPException(409, "Relation is not eligible for public visibility.")
    return await _payload(session, relation)
