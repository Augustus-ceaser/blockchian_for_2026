from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor, command_for
from app.modules.audit.services import append_audit_event_with_outbox
from app.modules.catalog.models import DataProduct, DataProductPublication, DataProductVersion
from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ModelProductExternalSourceLink,
)
from app.modules.marketplace.models import ModelProduct, ModelPublication, ModelVersion
from app.modules.dataset_model_evidence.models import (
    DatasetModelEvidence,
    DatasetModelRelation,
)


class DatasetModelEvidenceError(ValueError):
    pass


STATIC_TYPES = {
    "static_schema_compatible",
    "static_schema_compatible_with_transformation",
    "static_schema_incompatible",
    "insufficient_metadata",
}
DECLARATION_TYPES = {
    "author_declared_training",
    "author_declared_evaluation",
    "author_declared_benchmark",
    "external_related_reference",
}
FORBIDDEN_OPERATOR_TYPES = {"executed", "execution_failed", "verified"}


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _source_digest(payload: dict[str, Any]) -> str:
    return _digest(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


async def _audit(
    session: AsyncSession,
    *,
    actor: DemoActor,
    relation: DatasetModelRelation,
    event_type: str,
    action: str,
    raw_key: str,
    snapshot: dict[str, Any],
) -> None:
    command = command_for(actor, action, raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=relation.space_id,
        event_type=event_type,
        subject_type="dataset_model_relation",
        subject_id=relation.id,
        result="success",
        evidence_snapshot=snapshot,
        **command.append_kwargs(),
    )


async def _locked_graph(
    session: AsyncSession, data_version_id: UUID, model_version_id: UUID
) -> tuple[Any, ...]:
    data_version = await session.get(DataProductVersion, data_version_id)
    model_version = await session.get(ModelVersion, model_version_id)
    if data_version is None or model_version is None:
        raise DatasetModelEvidenceError("product version not found")
    data_product = await session.get(DataProduct, data_version.data_product_id)
    model_product = await session.get(ModelProduct, model_version.model_product_id)
    data_link = await session.scalar(select(DataProductExternalSourceLink).where(
        DataProductExternalSourceLink.data_product_version_id == data_version.id
    ))
    model_link = await session.scalar(select(ModelProductExternalSourceLink).where(
        ModelProductExternalSourceLink.model_version_id == model_version.id
    ))
    if not all((data_product, model_product, data_link, model_link)):
        raise DatasetModelEvidenceError("only governed external product versions are supported")
    if not data_version.snapshot_digest or not model_version.snapshot_digest:
        raise DatasetModelEvidenceError("version snapshot digest is missing")
    return data_product, data_version, data_link, model_product, model_version, model_link


async def _public_eligible(session: AsyncSession, graph: tuple[Any, ...]) -> bool:
    dp, dv, _, mp, mv, _ = graph
    data_pub = await session.scalar(select(DataProductPublication.id).where(
        DataProductPublication.data_product_version_id == dv.id,
        DataProductPublication.status == "active",
    ))
    model_pub = await session.scalar(select(ModelPublication.id).where(
        ModelPublication.model_version_id == mv.id,
        ModelPublication.status == "active",
    ))
    return (
        dp.lifecycle_status == "active"
        and mp.lifecycle_status == "active"
        and dv.status == "approved"
        and mv.status == "approved"
        and data_pub is not None
        and model_pub is not None
    )


async def append_operator_evidence(
    session: AsyncSession,
    *,
    actor: DemoActor,
    data_version_id: UUID,
    model_version_id: UUID,
    payload: dict[str, Any],
    raw_key: str,
) -> tuple[DatasetModelRelation, DatasetModelEvidence, bool]:
    if actor.role != "space_operator":
        raise DatasetModelEvidenceError("only the platform operator may create evidence")
    evidence_type = str(payload["evidence_type"])
    if evidence_type in FORBIDDEN_OPERATOR_TYPES:
        raise DatasetModelEvidenceError("runtime and verification evidence require internal run services")
    if evidence_type not in STATIC_TYPES | DECLARATION_TYPES:
        raise DatasetModelEvidenceError("unsupported evidence type")
    level = (
        "platform_static_review" if evidence_type in STATIC_TYPES
        else "external_declaration"
    )
    transformations = payload.get("transformation_requirements", [])
    if any(item.get("implementation_verified") is not False for item in transformations):
        raise DatasetModelEvidenceError("static transformations must remain unverified")
    graph = await _locked_graph(session, data_version_id, model_version_id)
    dp, dv, dl, mp, mv, ml = graph
    relation = await session.scalar(select(DatasetModelRelation).where(
        DatasetModelRelation.data_product_version_id == dv.id,
        DatasetModelRelation.model_product_version_id == mv.id,
    ))
    created = relation is None
    if relation is None:
        relation = DatasetModelRelation(
            id=uuid5(NAMESPACE_URL, f"medtrust:dataset-model:{dv.id}:{mv.id}"),
            space_id=dv.space_id,
            data_product_id=dp.id,
            data_product_version_id=dv.id,
            model_product_id=mp.id,
            model_product_version_id=mv.id,
            current_status="not_assessed",
            strongest_evidence_level="none",
            data_source_link_id=dl.id,
            model_source_link_id=ml.id,
            data_version_digest=dv.snapshot_digest,
            model_version_digest=mv.snapshot_digest,
            data_source_digest=dl.source_record_digest,
            model_source_digest=ml.source_record_digest,
            data_governance_digest=dl.governance_snapshot_digest,
            model_governance_digest=ml.governance_snapshot_digest,
        )
        session.add(relation)
        await session.flush()
        await _audit(
            session,
            actor=actor,
            relation=relation,
            event_type="dataset_model_relation.created",
            action=f"dataset-model-relation-created:{relation.id}",
            raw_key=raw_key,
            snapshot={
                "schema_version": "phase5.12.5/relation-created/v1",
                "data_product_id": str(dp.id),
                "data_version_id": str(dv.id),
                "model_product_id": str(mp.id),
                "model_version_id": str(mv.id),
                "data_version_digest": dv.snapshot_digest,
                "model_version_digest": mv.snapshot_digest,
            },
        )
    locked = (
        relation.data_version_digest == dv.snapshot_digest
        and relation.model_version_digest == mv.snapshot_digest
        and relation.data_source_digest == dl.source_record_digest
        and relation.model_source_digest == ml.source_record_digest
        and relation.data_governance_digest == dl.governance_snapshot_digest
        and relation.model_governance_digest == ml.governance_snapshot_digest
    )
    if not locked:
        raise DatasetModelEvidenceError("relation digest lock no longer matches")
    idempotency_digest = _digest(raw_key)
    existing = await session.scalar(select(DatasetModelEvidence).where(
        DatasetModelEvidence.idempotency_digest == idempotency_digest
    ))
    if existing is not None:
        if existing.relation_id != relation.id or existing.evidence_type != evidence_type:
            raise DatasetModelEvidenceError("idempotency key is bound to another review")
        return relation, existing, False
    supersedes_id = payload.get("supersedes_evidence_id")
    if supersedes_id is not None:
        superseded = await session.get(DatasetModelEvidence, supersedes_id)
        if superseded is None or superseded.relation_id != relation.id:
            raise DatasetModelEvidenceError("superseded evidence is not part of this relation")
    previous_status = relation.current_status
    previous_public = relation.public_visible
    evidence = DatasetModelEvidence(
        id=uuid5(NAMESPACE_URL, f"medtrust:dataset-model-evidence:{idempotency_digest}"),
        relation_id=relation.id,
        evidence_level=level,
        evidence_type=evidence_type,
        outcome=payload.get("outcome", "supports"),
        evidence_scope=payload.get("evidence_scope", "input_schema"),
        evidence_reference=payload.get("evidence_reference", {}),
        evidence_note=payload["evidence_note"],
        structured_assessment=payload.get("structured_assessment", {}),
        transformation_requirements=transformations,
        blocking_reasons=payload.get("blocking_reasons", []),
        warning_reasons=payload.get("warning_reasons", []),
        data_product_version_id=dv.id,
        model_product_version_id=mv.id,
        data_version_digest=dv.snapshot_digest,
        model_version_digest=mv.snapshot_digest,
        data_source_digest=dl.source_record_digest,
        model_source_digest=ml.source_record_digest,
        data_governance_digest=dl.governance_snapshot_digest,
        model_governance_digest=ml.governance_snapshot_digest,
        reviewer_user_id=actor.user_id,
        reviewer_organization_id=actor.organization_id,
        source_record_digest=_source_digest({
            "data_version_digest": dv.snapshot_digest,
            "model_version_digest": mv.snapshot_digest,
            "evidence_type": evidence_type,
            "payload": payload,
        }),
        idempotency_digest=idempotency_digest,
        supersedes_evidence_id=supersedes_id,
    )
    session.add(evidence)
    await session.flush()
    relation.current_evidence_id = evidence.id
    relation.current_status = (
        evidence_type if evidence_type in STATIC_TYPES else "external_declaration_only"
    )
    level_rank = {
        "none": 0,
        "external_declaration": 1,
        "platform_static_review": 2,
        "runtime_execution": 3,
        "platform_verification": 4,
    }
    if level_rank[level] >= level_rank[relation.strongest_evidence_level]:
        relation.strongest_evidence_level = level
    relation.public_visible = await _public_eligible(session, graph)
    relation.updated_at = datetime.now(timezone.utc)
    await _audit(
        session,
        actor=actor,
        relation=relation,
        event_type="dataset_model_evidence.created",
        action=f"dataset-model-evidence-created:{evidence.id}",
        raw_key=raw_key,
        snapshot={
            "schema_version": "phase5.12.5/evidence/v1",
            "evidence_id": str(evidence.id),
            "source_record_digest": evidence.source_record_digest,
            "data_product_id": str(dp.id),
            "data_version_id": str(dv.id),
            "model_product_id": str(mp.id),
            "model_version_id": str(mv.id),
            "evidence_level": level,
            "evidence_type": evidence_type,
            "outcome": evidence.outcome,
            "old_status": previous_status,
            "new_status": relation.current_status,
            "public_visible": relation.public_visible,
            "data_version_digest": dv.snapshot_digest,
            "model_version_digest": mv.snapshot_digest,
        },
    )
    if supersedes_id is not None:
        await _audit(
            session,
            actor=actor,
            relation=relation,
            event_type="dataset_model_evidence.superseded",
            action=f"dataset-model-evidence-superseded:{evidence.id}",
            raw_key=raw_key,
            snapshot={
                "schema_version": "phase5.12.5/evidence-superseded/v1",
                "evidence_id": str(evidence.id),
                "superseded_evidence_id": str(supersedes_id),
            },
        )
    if previous_status != relation.current_status:
        await _audit(
            session,
            actor=actor,
            relation=relation,
            event_type="dataset_model_relation.status_changed",
            action=f"dataset-model-relation-status:{evidence.id}",
            raw_key=raw_key,
            snapshot={
                "schema_version": "phase5.12.5/relation-status/v1",
                "evidence_id": str(evidence.id),
                "old_status": previous_status,
                "new_status": relation.current_status,
            },
        )
    if previous_public != relation.public_visible:
        await _audit(
            session,
            actor=actor,
            relation=relation,
            event_type="dataset_model_relation.publication_changed",
            action=f"dataset-model-relation-publication:{evidence.id}",
            raw_key=raw_key,
            snapshot={
                "schema_version": "phase5.12.5/relation-publication/v1",
                "evidence_id": str(evidence.id),
                "old_public_visible": previous_public,
                "new_public_visible": relation.public_visible,
            },
        )
    return relation, evidence, created
