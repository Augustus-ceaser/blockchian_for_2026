from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor, command_for
from app.modules.asset_materialization.models import AssetMaterializationPlan
from app.modules.audit import canonical_json_digest_v1
from app.modules.audit.services import append_audit_event_with_outbox
from app.modules.catalog.models import DataProduct, DataProductPublication, DataProductVersion
from app.modules.dataset_model_evidence.models import DatasetModelEvidence, DatasetModelRelation
from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ModelProductExternalSourceLink,
)
from app.modules.marketplace.models import ModelProduct, ModelPublication, ModelVersion

MAX_TOTAL_BYTES = 50 * 1024**3
MIN_REMAINING_BYTES = 100 * 1024**3


class MaterializationPlanError(ValueError):
    pass


def _key_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _plan_manifest(relation: DatasetModelRelation, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "phase5.12.6A/materialization-plan/v1",
        "relation_id": str(relation.id),
        "data_product_version_id": str(relation.data_product_version_id),
        "model_product_version_id": str(relation.model_product_version_id),
        "relation_evidence_id": str(relation.current_evidence_id),
        "version_locks": {
            "data_version_digest": relation.data_version_digest,
            "model_version_digest": relation.model_version_digest,
            "data_source_digest": relation.data_source_digest,
            "model_source_digest": relation.model_source_digest,
            "data_governance_digest": relation.data_governance_digest,
            "model_governance_digest": relation.model_governance_digest,
        },
        **payload,
    }


async def _locked_graph(
    session: AsyncSession, relation_id: UUID
) -> tuple[DatasetModelRelation, DatasetModelEvidence]:
    relation = await session.get(DatasetModelRelation, relation_id)
    if relation is None:
        raise MaterializationPlanError("relation not found")
    if (
        not relation.active
        or not relation.public_visible
        or relation.current_status != "static_schema_compatible_with_transformation"
        or relation.current_evidence_id is None
    ):
        raise MaterializationPlanError("relation is not eligible for materialization planning")
    evidence = await session.get(DatasetModelEvidence, relation.current_evidence_id)
    data_version = await session.get(DataProductVersion, relation.data_product_version_id)
    model_version = await session.get(ModelVersion, relation.model_product_version_id)
    data_product = await session.get(DataProduct, relation.data_product_id)
    model_product = await session.get(ModelProduct, relation.model_product_id)
    data_link = await session.get(DataProductExternalSourceLink, relation.data_source_link_id)
    model_link = await session.get(ModelProductExternalSourceLink, relation.model_source_link_id)
    if not all((evidence, data_version, model_version, data_product, model_product, data_link, model_link)):
        raise MaterializationPlanError("locked relation graph is incomplete")
    data_publication = await session.scalar(
        select(DataProductPublication.id).where(
            DataProductPublication.data_product_version_id == data_version.id,
            DataProductPublication.status == "active",
        )
    )
    model_publication = await session.scalar(
        select(ModelPublication.id).where(
            ModelPublication.model_version_id == model_version.id,
            ModelPublication.status == "active",
        )
    )
    if (
        data_product.lifecycle_status != "active"
        or model_product.lifecycle_status != "active"
        or data_version.status != "approved"
        or model_version.status != "approved"
        or data_publication is None
        or model_publication is None
    ):
        raise MaterializationPlanError("both exact product versions must remain published")
    locks_match = (
        relation.data_version_digest == data_version.snapshot_digest
        and relation.model_version_digest == model_version.snapshot_digest
        and relation.data_source_digest == data_link.source_record_digest
        and relation.model_source_digest == model_link.source_record_digest
        and relation.data_governance_digest == data_link.governance_snapshot_digest
        and relation.model_governance_digest == model_link.governance_snapshot_digest
        and evidence.data_version_digest == relation.data_version_digest
        and evidence.model_version_digest == relation.model_version_digest
        and evidence.data_source_digest == relation.data_source_digest
        and evidence.model_source_digest == relation.model_source_digest
    )
    if not locks_match:
        raise MaterializationPlanError("relation digest lock no longer matches")
    return relation, evidence


async def _audit(
    session: AsyncSession,
    *,
    actor: DemoActor,
    plan: AssetMaterializationPlan,
    event_type: str,
    result: str,
    raw_key: str,
) -> None:
    command = command_for(actor, f"{event_type}:{plan.id}", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=plan.space_id,
        event_type=event_type,
        subject_type="asset_materialization_plan",
        subject_id=plan.id,
        result=result,
        evidence_snapshot={
            "schema_version": "phase5.12.6A/plan-event/v1",
            "relation_id": str(plan.relation_id),
            "data_product_version_id": str(plan.data_product_version_id),
            "model_product_version_id": str(plan.model_product_version_id),
            "plan_digest": plan.plan_digest,
            "total_estimated_bytes": plan.total_estimated_bytes,
            "network_allowlist_count": len(plan.network_allowlist),
            "decision": plan.plan_status,
            "actor_user_id": str(actor.user_id),
        },
        **command.append_kwargs(),
    )


async def create_plan(
    session: AsyncSession,
    *,
    actor: DemoActor,
    relation_id: UUID,
    payload: dict[str, Any],
    raw_key: str,
) -> tuple[AssetMaterializationPlan, bool]:
    if actor.role != "catalog_curator":
        raise MaterializationPlanError("only the catalog curator may create plans")
    relation, evidence = await _locked_graph(session, relation_id)
    key_digest = _key_digest(raw_key)
    existing = await session.scalar(
        select(AssetMaterializationPlan).where(
            AssetMaterializationPlan.create_idempotency_digest == key_digest
        )
    )
    if existing is not None:
        if existing.relation_id != relation.id:
            raise MaterializationPlanError("idempotency key is bound to another plan")
        return existing, False
    total = (
        int(payload["data_estimated_bytes"])
        + int(payload["model_estimated_bytes"])
        + int(payload["derived_estimated_bytes"])
    )
    manifest_payload = {**payload, "total_estimated_bytes": total}
    manifest = _plan_manifest(relation, manifest_payload)
    plan_digest = canonical_json_digest_v1(manifest)
    plan = AssetMaterializationPlan(
        id=uuid5(NAMESPACE_URL, f"medtrust:materialization-plan:{plan_digest}"),
        space_id=relation.space_id,
        relation_id=relation.id,
        data_product_version_id=relation.data_product_version_id,
        model_product_version_id=relation.model_product_version_id,
        relation_evidence_id=evidence.id,
        plan_status="draft",
        data_plan=payload["data_plan"],
        model_plan=payload["model_plan"],
        transformation_plan=payload["transformation_plan"],
        execution_goal=payload["execution_goal"],
        data_estimated_bytes=payload["data_estimated_bytes"],
        model_estimated_bytes=payload["model_estimated_bytes"],
        derived_estimated_bytes=payload["derived_estimated_bytes"],
        total_estimated_bytes=total,
        hardware_requirements=payload["hardware_requirements"],
        network_allowlist=payload["network_allowlist"],
        asset_file_allowlist=payload["asset_file_allowlist"],
        license_snapshot=payload["license_snapshot"],
        access_snapshot=payload["access_snapshot"],
        security_preflight=payload["security_preflight"],
        blocking_reasons=payload["blocking_reasons"],
        data_version_digest=relation.data_version_digest,
        model_version_digest=relation.model_version_digest,
        data_source_digest=relation.data_source_digest,
        model_source_digest=relation.model_source_digest,
        data_governance_digest=relation.data_governance_digest,
        model_governance_digest=relation.model_governance_digest,
        relation_evidence_digest=evidence.source_record_digest,
        plan_digest=plan_digest,
        create_idempotency_digest=key_digest,
        created_by=actor.user_id,
        creator_organization_id=actor.organization_id,
        rejection_reasons=[],
        supersedes_plan_id=payload.get("supersedes_plan_id"),
    )
    session.add(plan)
    await session.flush()
    if plan.supersedes_plan_id is not None:
        superseded = await session.get(
            AssetMaterializationPlan, plan.supersedes_plan_id, with_for_update=True
        )
        if (
            superseded is None
            or superseded.relation_id != relation.id
            or superseded.plan_status != "approved"
        ):
            raise MaterializationPlanError(
                "only an approved plan for the same relation may be superseded"
            )
        superseded.plan_status = "superseded"
        await session.flush()
        await _audit(
            session,
            actor=actor,
            plan=superseded,
            event_type="asset_materialization.plan.superseded",
            result="success",
            raw_key=raw_key,
        )
    await _audit(
        session,
        actor=actor,
        plan=plan,
        event_type="asset_materialization.plan.created",
        result="success",
        raw_key=raw_key,
    )
    return plan, True


async def submit_plan(
    session: AsyncSession, *, actor: DemoActor, plan_id: UUID, raw_key: str
) -> tuple[AssetMaterializationPlan, bool]:
    if actor.role != "catalog_curator":
        raise MaterializationPlanError("only the catalog curator may submit plans")
    plan = await session.get(AssetMaterializationPlan, plan_id, with_for_update=True)
    if plan is None:
        raise MaterializationPlanError("plan not found")
    if plan.plan_status == "submitted" and plan.submit_idempotency_digest == _key_digest(raw_key):
        return plan, False
    if plan.plan_status != "draft" or plan.creator_organization_id != actor.organization_id:
        raise MaterializationPlanError("only the owning curator may submit a draft")
    await _locked_graph(session, plan.relation_id)
    plan.plan_status = "submitted"
    plan.submitted_by = actor.user_id
    plan.submitted_at = datetime.now(timezone.utc)
    plan.submit_idempotency_digest = _key_digest(raw_key)
    await session.flush()
    await _audit(
        session,
        actor=actor,
        plan=plan,
        event_type="asset_materialization.plan.submitted",
        result="success",
        raw_key=raw_key,
    )
    return plan, True


def _approval_blockers(plan: AssetMaterializationPlan) -> list[str]:
    blockers = list(plan.blocking_reasons)
    security = plan.security_preflight
    files = plan.asset_file_allowlist
    safe_network = bool(plan.network_allowlist) and all(
        value.startswith("https://") and "?" not in value and "#" not in value
        for value in plan.network_allowlist
    )
    safe_files = bool(files) and all(
        isinstance(item.get("bytes"), int)
        and item["bytes"] >= 0
        and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(item.get("sha256", ""))))
        and not str(item.get("path", "")).startswith(("/", "\\"))
        and not re.match(r"^[A-Za-z]:", str(item.get("path", "")))
        and ".." not in str(item.get("path", "")).replace("\\", "/").split("/")
        for item in files
    )
    security_controls = all(
        security.get(field) is True
        for field in (
            "redirects_bounded",
            "dns_rebinding_protected",
            "archive_traversal_blocked",
            "symlinks_forbidden",
            "executables_forbidden",
            "dynamic_import_forbidden",
            "native_extensions_forbidden",
            "dependencies_pinned",
            "integrity_metadata_complete",
        )
    )
    checks = {
        "license evidence is not approved": plan.license_snapshot.get("result") == "pass",
        "access evidence is not approved": plan.access_snapshot.get("result") == "pass",
        "security preflight did not pass": plan.security_preflight.get("result") == "pass",
        "model revision is not immutable": bool(plan.model_plan.get("revision")),
        "transformation is incomplete": plan.transformation_plan.get("complete") is True,
        "transformation is not deterministic": plan.transformation_plan.get("deterministic") is True,
        "hardware is insufficient": plan.hardware_requirements.get("available") is True,
        "runtime network must be disabled": plan.model_plan.get("runtime_network") is False,
        "remote code must be disabled": plan.model_plan.get("trust_remote_code") is False,
        "private access tokens are prohibited": plan.access_snapshot.get("private_token_required") is False,
        "gated access is prohibited": plan.access_snapshot.get("gated") is False,
        "file allowlist is incomplete or unsafe": safe_files,
        "network allowlist must contain query-free HTTPS URLs": safe_network,
        "required supply-chain controls are incomplete": security_controls,
        "pickle serialization risk is unresolved": security.get("pickle_allowed") is False,
        "planned bytes exceed 50 GiB": plan.total_estimated_bytes <= MAX_TOTAL_BYTES,
        "remaining disk would be below 100 GiB": (
            int(plan.hardware_requirements.get("disk_free_bytes", 0))
            - plan.total_estimated_bytes
            >= MIN_REMAINING_BYTES
        ),
    }
    blockers.extend(message for message, passed in checks.items() if not passed)
    return list(dict.fromkeys(blockers))


async def decide_plan(
    session: AsyncSession,
    *,
    actor: DemoActor,
    plan_id: UUID,
    approve: bool,
    reasons: list[str],
    raw_key: str,
) -> tuple[AssetMaterializationPlan, bool]:
    if actor.role != "space_operator":
        raise MaterializationPlanError("only the platform operator may decide plans")
    plan = await session.get(AssetMaterializationPlan, plan_id, with_for_update=True)
    if plan is None:
        raise MaterializationPlanError("plan not found")
    digest = _key_digest(raw_key)
    terminal = "approved" if approve else "rejected"
    if plan.plan_status == terminal and plan.decision_idempotency_digest == digest:
        return plan, False
    if plan.plan_status != "submitted":
        raise MaterializationPlanError("only submitted plans may be decided")
    if plan.creator_organization_id == actor.organization_id:
        raise MaterializationPlanError("self approval is prohibited")
    await _locked_graph(session, plan.relation_id)
    blockers = _approval_blockers(plan)
    if approve and blockers:
        raise MaterializationPlanError("approval blocked: " + "; ".join(blockers))
    if not approve and not reasons:
        raise MaterializationPlanError("rejection reasons are required")
    plan.plan_status = terminal
    plan.approved_by = actor.user_id if approve else None
    plan.approver_organization_id = actor.organization_id
    plan.approved_at = datetime.now(timezone.utc) if approve else None
    plan.decided_at = datetime.now(timezone.utc)
    plan.decision_idempotency_digest = digest
    plan.rejection_reasons = [] if approve else reasons
    await session.flush()
    await _audit(
        session,
        actor=actor,
        plan=plan,
        event_type=(
            "asset_materialization.plan.approved"
            if approve
            else "asset_materialization.plan.rejected"
        ),
        result="success" if approve else "denied",
        raw_key=raw_key,
    )
    return plan, True


async def cancel_plan(
    session: AsyncSession, *, actor: DemoActor, plan_id: UUID, raw_key: str
) -> tuple[AssetMaterializationPlan, bool]:
    if actor.role != "space_operator":
        raise MaterializationPlanError("only the platform operator may cancel plans")
    plan = await session.get(AssetMaterializationPlan, plan_id, with_for_update=True)
    if plan is None:
        raise MaterializationPlanError("plan not found")
    digest = _key_digest(raw_key)
    if plan.plan_status == "cancelled" and plan.decision_idempotency_digest == digest:
        return plan, False
    if plan.plan_status not in {"draft", "submitted"}:
        raise MaterializationPlanError("terminal plans cannot be cancelled")
    plan.plan_status = "cancelled"
    plan.approver_organization_id = actor.organization_id
    plan.decided_at = datetime.now(timezone.utc)
    plan.decision_idempotency_digest = digest
    await session.flush()
    await _audit(
        session,
        actor=actor,
        plan=plan,
        event_type="asset_materialization.plan.cancelled",
        result="success",
        raw_key=raw_key,
    )
    return plan, True
