from __future__ import annotations

import hashlib
import io
import json
import secrets
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.registry import ModelRegistration, ModelRegistry
from app.modules.audit import AuditCommandContext, append_audit_event_with_outbox
from app.modules.audit.services import canonical_json_digest_v1
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.contracts.models import (
    Contract,
    ContractParty,
    ContractRevision,
    Policy,
    PolicyExecutionBinding,
)
from app.modules.identity.models import Organization, OrganizationMember, User
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole

from .models import (
    SAFE_RESULT_FILENAMES,
    ApplicationModelSelection,
    ApprovedResultPackage,
    ArtifactReviewDecision,
    ArtifactReviewTask,
    ContractModelObject,
    ContractReadinessConfirmation,
    ModelProduct,
    ModelPublication,
    ModelVersion,
    ResultDownloadGrant,
)


class MarketplaceServiceError(ValueError):
    pass


class ReleaseObjectStore(Protocol):
    bucket_name: str

    def put(self, object_key: str, payload: bytes, content_type: str) -> None: ...

    def get(self, object_key: str) -> bytes: ...


def canonical_digest(document: dict[str, Any]) -> str:
    return canonical_json_digest_v1(document)


def content_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


async def require_actor(
    session: AsyncSession,
    *,
    space_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    role_code: str,
) -> None:
    organization = await session.get(Organization, organization_id)
    user = await session.get(User, user_id)
    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id,
            OrganizationMember.status == "active",
        )
    )
    participant = await session.scalar(
        select(SpaceParticipant).where(
            SpaceParticipant.space_id == space_id,
            SpaceParticipant.organization_id == organization_id,
            SpaceParticipant.admission_status == "admitted",
        )
    )
    participant_role = None
    if participant is not None:
        participant_role = await session.get(
            SpaceParticipantRole, (participant.id, role_code)
        )
    if (
        organization is None
        or organization.status != "active"
        or user is None
        or user.status != "active"
        or membership is None
        or participant is None
        or participant_role is None
    ):
        raise MarketplaceServiceError(
            f"actor lacks active {role_code} authority in this Space"
        )


def _audit_evidence(
    *,
    schema: str,
    command: AuditCommandContext,
    facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "command_id": str(command.command_id),
        **facts,
    }


async def _append(
    session: AsyncSession,
    *,
    command: AuditCommandContext,
    space_id: UUID,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    evidence: dict[str, Any],
    result: str = "success",
) -> None:
    await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        result=result,
        evidence_snapshot=evidence,
        **command.append_kwargs(),
    )


def validate_registry_binding(
    model_version: ModelVersion, registry: ModelRegistry
) -> ModelRegistration:
    entry = registry.require_enabled(model_version.model_digest)
    if (
        entry.entrypoint_id != model_version.entrypoint_id
        or entry.registration_digest != model_version.registry_digest
        or entry.runtime != model_version.runtime
        or entry.input_schema_version != model_version.input_schema_version
        or entry.output_schema_version != model_version.output_schema_version
    ):
        raise MarketplaceServiceError(
            "model catalog version does not match the fixed execution registry"
        )
    return entry


async def submit_model_version(
    session: AsyncSession,
    version: ModelVersion,
    *,
    registry: ModelRegistry,
    provider_organization_id: UUID,
    provider_user_id: UUID,
    command: AuditCommandContext,
    evidence_facts: dict[str, Any] | None = None,
) -> None:
    product = await session.get(ModelProduct, version.model_product_id)
    if product is None or product.space_id != version.space_id:
        raise MarketplaceServiceError("model product is unavailable")
    await require_actor(
        session,
        space_id=version.space_id,
        organization_id=provider_organization_id,
        user_id=provider_user_id,
        role_code="model_provider",
    )
    if product.provider_organization_id != provider_organization_id:
        raise MarketplaceServiceError("only the model provider may submit this version")
    if version.status != "draft":
        raise MarketplaceServiceError("only a draft model version can be submitted")
    validate_registry_binding(version, registry)
    snapshot = {
        "schema_version": "model-version-snapshot/v1",
        "model_product_id": str(product.id),
        "model_version_id": str(version.id),
        "version_label": version.version_label,
        "entrypoint_id": version.entrypoint_id,
        "model_digest": version.model_digest,
        "manifest_digest": version.manifest_digest,
        "registry_digest": version.registry_digest,
        "runtime": version.runtime,
        "input_schema_version": version.input_schema_version,
        "output_schema_version": version.output_schema_version,
        "compatibility_metadata": version.compatibility_metadata,
        "license_metadata": version.license_metadata,
        "default_policy_digest": version.default_policy_digest,
    }
    version.snapshot_digest = canonical_digest(snapshot)
    version.status = "under_review"
    version._transition_validated = True
    await session.flush()
    await _append(
        session,
        command=command,
        space_id=version.space_id,
        event_type="model_product.version.submitted",
        subject_type="model_version",
        subject_id=version.id,
        evidence=_audit_evidence(
            schema="model-product-version-submitted/v1",
            command=command,
            facts={
                "model_product_id": str(product.id),
                "model_version_id": str(version.id),
                "model_snapshot_digest": version.snapshot_digest,
                "registry_digest": version.registry_digest,
                "state_before": "draft",
                "state_after": "under_review",
                **(evidence_facts or {}),
            },
        ),
    )


async def approve_model_version(
    session: AsyncSession,
    version: ModelVersion,
    *,
    registry: ModelRegistry,
    operator_organization_id: UUID,
    operator_user_id: UUID,
    command: AuditCommandContext,
    evidence_facts: dict[str, Any] | None = None,
) -> None:
    await require_actor(
        session,
        space_id=version.space_id,
        organization_id=operator_organization_id,
        user_id=operator_user_id,
        role_code="space_operator",
    )
    if version.status != "under_review":
        raise MarketplaceServiceError("model version is not awaiting listing review")
    validate_registry_binding(version, registry)
    version.status = "approved"
    version.approved_at = datetime.now(timezone.utc)
    version.approved_by = operator_user_id
    version._transition_validated = True
    await session.flush()
    await _append(
        session,
        command=command,
        space_id=version.space_id,
        event_type="model_product.version.approved",
        subject_type="model_version",
        subject_id=version.id,
        evidence=_audit_evidence(
            schema="model-product-version-approved/v1",
            command=command,
            facts={
                "model_version_id": str(version.id),
                "model_snapshot_digest": version.snapshot_digest,
                "registry_digest": version.registry_digest,
                "state_before": "under_review",
                "state_after": "approved",
                **(evidence_facts or {}),
            },
        ),
    )


async def publish_model_version(
    session: AsyncSession,
    product: ModelProduct,
    version: ModelVersion,
    *,
    operator_organization_id: UUID,
    operator_user_id: UUID,
    command: AuditCommandContext,
    visibility: str = "space",
    evidence_facts: dict[str, Any] | None = None,
) -> ModelPublication:
    await require_actor(
        session,
        space_id=product.space_id,
        organization_id=operator_organization_id,
        user_id=operator_user_id,
        role_code="space_operator",
    )
    if version.model_product_id != product.id or version.status != "approved":
        raise MarketplaceServiceError("only an approved version can be published")
    if await session.scalar(
        select(ModelPublication.id).where(
            ModelPublication.model_product_id == product.id,
            ModelPublication.status == "active",
        )
    ):
        raise MarketplaceServiceError("model product already has an active publication")
    publication = ModelPublication(
        space_id=product.space_id,
        model_product_id=product.id,
        model_version_id=version.id,
        visibility=visibility,
        published_by=operator_user_id,
    )
    session.add(publication)
    product.lifecycle_status = "active"
    product.row_version += 1
    await session.flush()
    await _append(
        session,
        command=command,
        space_id=product.space_id,
        event_type="model_product.version.published",
        subject_type="model_version",
        subject_id=version.id,
        evidence=_audit_evidence(
            schema="model-product-version-published/v1",
            command=command,
            facts={
                "model_product_id": str(product.id),
                "model_version_id": str(version.id),
                "publication_id": str(publication.id),
                "visibility": visibility,
                "state_before": "approved",
                "state_after": "published",
                **(evidence_facts or {}),
            },
        ),
    )
    return publication


async def attach_model_to_demand(
    session: AsyncSession,
    *,
    application_id: UUID,
    model_version_id: UUID,
) -> ApplicationModelSelection:
    from app.modules.applications.models import Application
    from app.modules.external_catalog.eligibility import (
        ExternalModelProductEligibilityError,
        require_materialized_model_product,
    )

    try:
        await require_materialized_model_product(session, model_version_id)
    except ExternalModelProductEligibilityError as exc:
        raise MarketplaceServiceError(str(exc)) from exc

    application = await session.get(Application, application_id)
    version = await session.get(ModelVersion, model_version_id)
    product = (
        None if version is None else await session.get(ModelProduct, version.model_product_id)
    )
    publication = (
        None
        if version is None
        else await session.scalar(
            select(ModelPublication).where(
                ModelPublication.model_version_id == version.id,
                ModelPublication.status == "active",
            )
        )
    )
    if (
        application is None
        or application.status != "draft"
        or version is None
        or product is None
        or publication is None
        or version.status != "approved"
        or application.space_id != version.space_id
    ):
        raise MarketplaceServiceError("demand requires a published model version")
    selection = ApplicationModelSelection(
        application_id=application.id,
        space_id=application.space_id,
        model_provider_organization_id=product.provider_organization_id,
        model_product_id=product.id,
        model_version_id=version.id,
        model_snapshot_digest=version.snapshot_digest,
        requested_model_policy_digest=version.default_policy_digest,
        registry_digest=version.registry_digest,
    )
    session.add(selection)
    await session.flush()
    return selection


async def confirm_contract_readiness(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    readiness_type: str,
    organization_id: UUID,
    user_id: UUID,
    target_snapshot: dict[str, Any],
    evidence_snapshot: dict[str, Any],
    command: AuditCommandContext,
    registry: ModelRegistry | None = None,
) -> ContractReadinessConfirmation:
    role = {
        "data_ready": "data_provider",
        "model_ready": "model_provider",
        "platform_ready": "space_operator",
    }.get(readiness_type)
    if role is None:
        raise MarketplaceServiceError("unknown readiness type")
    contract = await session.get(Contract, revision.contract_id)
    if contract is None or revision.status != "active":
        raise MarketplaceServiceError("readiness requires an active ContractRevision")
    await require_actor(
        session,
        space_id=contract.space_id,
        organization_id=organization_id,
        user_id=user_id,
        role_code=role,
    )
    required_party_role = {
        "data_ready": "data_provider",
        "model_ready": "model_provider",
    }.get(readiness_type)
    if required_party_role is not None:
        party = await session.scalar(
            select(ContractParty).where(
                ContractParty.contract_revision_id == revision.id,
                ContractParty.party_role == required_party_role,
                ContractParty.organization_id == organization_id,
            )
        )
        if party is None:
            raise MarketplaceServiceError("readiness confirmer is not the contracted party")
    if readiness_type == "model_ready":
        model_object = await session.scalar(
            select(ContractModelObject).where(
                ContractModelObject.contract_revision_id == revision.id
            )
        )
        version = (
            None
            if model_object is None
            else await session.get(ModelVersion, model_object.model_version_id)
        )
        if version is None or registry is None:
            raise MarketplaceServiceError("model readiness requires a fixed model object")
        validate_registry_binding(version, registry)
        if target_snapshot.get("model_snapshot_digest") != model_object.model_snapshot_digest:
            raise MarketplaceServiceError("model readiness target digest is inconsistent")
    if readiness_type == "platform_ready":
        for required in ("data_ready", "model_ready"):
            if await session.scalar(
                select(ContractReadinessConfirmation.id).where(
                    ContractReadinessConfirmation.contract_revision_id == revision.id,
                    ContractReadinessConfirmation.readiness_type == required,
                )
            ) is None:
                raise MarketplaceServiceError("provider readiness is incomplete")
        bindings = list(
            (
                await session.scalars(
                    select(PolicyExecutionBinding)
                    .join(Policy, Policy.id == PolicyExecutionBinding.policy_id)
                    .where(
                        Policy.contract_revision_id == revision.id,
                        PolicyExecutionBinding.is_required.is_(True),
                    )
                )
            ).all()
        )
        if not bindings:
            raise MarketplaceServiceError("active contract has no execution bindings")
        for binding in bindings:
            connector = await session.get(Connector, binding.connector_id)
            capability = await session.get(
                ConnectorCapability,
                (
                    binding.connector_id,
                    binding.required_capability_code,
                    binding.required_capability_version,
                ),
            )
            if (
                connector is None
                or connector.runtime_status != "online"
                or connector.verification_status != "verified"
                or capability is None
                or capability.status != "verified"
            ):
                raise MarketplaceServiceError("execution binding is not currently ready")
    confirmation = ContractReadinessConfirmation(
        space_id=contract.space_id,
        contract_revision_id=revision.id,
        readiness_type=readiness_type,
        responsible_organization_id=organization_id,
        confirmed_by_user_id=user_id,
        target_snapshot=target_snapshot,
        target_digest=canonical_digest(target_snapshot),
        evidence_snapshot=evidence_snapshot,
        evidence_digest=canonical_digest(evidence_snapshot),
    )
    session.add(confirmation)
    await session.flush()
    await _append(
        session,
        command=command,
        space_id=contract.space_id,
        event_type="contract.readiness.confirmed",
        subject_type="contract_readiness",
        subject_id=confirmation.id,
        evidence=_audit_evidence(
            schema="contract-readiness-confirmed/v1",
            command=command,
            facts={
                "contract_revision_id": str(revision.id),
                "readiness_type": readiness_type,
                "responsible_organization_id": str(organization_id),
                "target_digest": confirmation.target_digest,
                "evidence_digest": confirmation.evidence_digest,
            },
        ),
    )
    return confirmation


async def require_all_readiness(
    session: AsyncSession, revision_id: UUID
) -> tuple[ContractReadinessConfirmation, ...]:
    rows = tuple(
        (
            await session.scalars(
                select(ContractReadinessConfirmation).where(
                    ContractReadinessConfirmation.contract_revision_id == revision_id
                )
            )
        ).all()
    )
    if {row.readiness_type for row in rows} != {
        "data_ready",
        "model_ready",
        "platform_ready",
    }:
        raise MarketplaceServiceError("data, model and platform readiness are required")
    return rows


async def create_artifact_review_plan(
    session: AsyncSession,
    artifact: Artifact,
    *,
    created_by: UUID,
    command: AuditCommandContext | None = None,
) -> tuple[ArtifactReviewTask, ...]:
    if artifact.release_status != "quarantined":
        raise MarketplaceServiceError("only a quarantined Artifact can be reviewed")
    run = await session.get(ComputeRun, artifact.compute_run_id)
    job = None if run is None else await session.get(ComputeJob, run.compute_job_id)
    revision = (
        None if job is None else await session.get(ContractRevision, job.contract_revision_id)
    )
    contract = None if revision is None else await session.get(Contract, revision.contract_id)
    space = None if contract is None else await session.get(Space, contract.space_id)
    if run is None or job is None or revision is None or contract is None or space is None:
        raise MarketplaceServiceError("Artifact contract provenance is incomplete")
    parties = list(
        (
            await session.scalars(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    by_role = {party.party_role: party.organization_id for party in parties}
    provider = by_role.get("data_provider") or by_role.get("provider")
    model_provider = by_role.get("model_provider")
    requester = by_role.get("data_requester") or by_role.get("consumer")
    if provider is None:
        raise MarketplaceServiceError("data provider party is missing")
    required_model_review = bool(
        model_provider is not None
        and revision.terms_document.get("policy_convergence", {})
        .get("final", {})
        .get("model_technical_confirmation", True)
    )
    specs = [
        ("data_provider_egress_review", provider, True),
        ("platform_compliance_review", space.operator_organization_id, True),
    ]
    if model_provider is not None:
        specs.append(
            (
                "model_provider_quality_review",
                model_provider,
                required_model_review,
            )
        )
    tasks: list[ArtifactReviewTask] = []
    for review_type, organization_id, required in specs:
        routing = {
            "schema_version": "artifact-review-route/v1",
            "artifact_id": str(artifact.id),
            "artifact_digest": artifact.content_digest,
            "review_type": review_type,
            "responsible_organization_id": str(organization_id),
            "is_required": required,
        }
        task = ArtifactReviewTask(
            space_id=artifact.space_id,
            artifact_id=artifact.id,
            target_content_digest=artifact.content_digest,
            review_type=review_type,
            responsible_organization_id=organization_id,
            is_required=required,
            routing_rule_digest=canonical_digest(routing),
        )
        session.add(task)
        tasks.append(task)
    await session.flush()
    if command is not None:
        await _append(
            session,
            command=command,
            space_id=artifact.space_id,
            event_type="artifact.review.plan.created",
            subject_type="artifact",
            subject_id=artifact.id,
            evidence=_audit_evidence(
                schema="artifact-review-plan-created/v1",
                command=command,
                facts={
                    "artifact_id": str(artifact.id),
                    "artifact_digest": artifact.content_digest,
                    "contract_revision_id": str(revision.id),
                    "review_types": [item.review_type for item in tasks],
                    "required_review_types": [
                        item.review_type for item in tasks if item.is_required
                    ],
                },
            ),
        )
    return tuple(tasks)


async def claim_artifact_review_task(
    session: AsyncSession,
    task: ArtifactReviewTask,
    *,
    user_id: UUID,
) -> None:
    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == task.responsible_organization_id,
            OrganizationMember.user_id == user_id,
            OrganizationMember.status == "active",
        )
    )
    if membership is None or task.status != "pending":
        raise MarketplaceServiceError("Artifact review task cannot be claimed")
    task.assigned_user_id = user_id
    task.claimed_at = datetime.now(timezone.utc)
    task.status = "claimed"
    task.row_version += 1
    task._transition_validated = True
    await session.flush()


async def decide_artifact_review_task(
    session: AsyncSession,
    task: ArtifactReviewTask,
    *,
    decision: str,
    reason_code: str,
    comment: str | None = None,
    evidence_snapshot: dict[str, Any],
    command: AuditCommandContext,
) -> ArtifactReviewDecision:
    if task.status != "claimed" or task.assigned_user_id is None:
        raise MarketplaceServiceError("Artifact review task must be claimed")
    if decision not in {"approved", "rejected"}:
        raise MarketplaceServiceError("unknown Artifact review decision")
    artifact = await session.get(Artifact, task.artifact_id)
    if artifact is None or artifact.content_digest != task.target_content_digest:
        raise MarketplaceServiceError("Artifact review target changed")
    policy_evaluation = artifact.output_policy_evaluation
    if decision == "approved" and (
        not isinstance(policy_evaluation, dict)
        or policy_evaluation.get("decision") != "permit"
        or bool(policy_evaluation.get("deny_policy_digests"))
    ):
        raise MarketplaceServiceError("human approval cannot override Policy deny")
    if task.review_type == "platform_compliance_review":
        preceding = list(
            (
                await session.scalars(
                    select(ArtifactReviewTask).where(
                        ArtifactReviewTask.artifact_id == task.artifact_id,
                        ArtifactReviewTask.is_required.is_(True),
                        ArtifactReviewTask.review_type
                        != "platform_compliance_review",
                    )
                )
            ).all()
        )
        preceding_decisions = {
            row.artifact_review_task_id: row
            for row in (
                await session.scalars(
                    select(ArtifactReviewDecision).where(
                        ArtifactReviewDecision.artifact_review_task_id.in_(
                            [item.id for item in preceding]
                        )
                    )
                )
            ).all()
        }
        if any(
            item.status != "decided"
            or preceding_decisions.get(item.id) is None
            or preceding_decisions[item.id].decision != "approved"
            for item in preceding
        ):
            raise MarketplaceServiceError(
                "platform compliance review must be last"
            )
    now = datetime.now(timezone.utc)
    decision_document = {
        "schema_version": "artifact-review-decision/v1",
        "task_id": str(task.id),
        "artifact_id": str(artifact.id),
        "artifact_digest": artifact.content_digest,
        "review_type": task.review_type,
        "responsible_organization_id": str(task.responsible_organization_id),
        "decided_by_user_id": str(task.assigned_user_id),
        "decision": decision,
        "reason_code": reason_code,
        "evidence_digest": canonical_digest(evidence_snapshot),
        "decided_at": now.isoformat(),
    }
    row = ArtifactReviewDecision(
        artifact_review_task_id=task.id,
        responsible_organization_id=task.responsible_organization_id,
        decided_by_user_id=task.assigned_user_id,
        target_content_digest=task.target_content_digest,
        decision=decision,
        reason_code=reason_code,
        comment=comment,
        evidence_snapshot=evidence_snapshot,
        evidence_digest=decision_document["evidence_digest"],
        decision_digest=canonical_digest(decision_document),
        decided_at=now,
    )
    session.add(row)
    task.status = "decided"
    task.decided_at = now
    task.row_version += 1
    task._transition_validated = True
    await session.flush()
    await _append(
        session,
        command=command,
        space_id=task.space_id,
        event_type="artifact.multiparty_review.decided",
        subject_type="artifact_review_decision",
        subject_id=row.id,
        evidence=_audit_evidence(
            schema="artifact-multiparty-review-decided/v1",
            command=command,
            facts={
                "artifact_id": str(artifact.id),
                "artifact_digest": artifact.content_digest,
                "review_type": task.review_type,
                "decision": decision,
                "decision_digest": row.decision_digest,
            },
        ),
    )
    return row


def build_safe_result_archive(files: dict[str, bytes]) -> tuple[bytes, dict[str, Any]]:
    allowed = set(SAFE_RESULT_FILENAMES)
    if set(files) != allowed:
        raise MarketplaceServiceError(
            "result package contains a non-whitelisted file or is missing an allowlisted file"
        )
    manifest_items = [
        {"name": name, "size_bytes": len(payload), "digest": content_digest(payload)}
        for name, payload in sorted(files.items())
    ]
    manifest = {
        "schema_version": "approved-result-package/v1",
        "files": manifest_items,
        "contains_raw_data": False,
        "contains_patient_level_results": False,
        "contains_model_weights": False,
        "contains_raw_features": False,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(name, payload)
    return output.getvalue(), manifest


async def create_approved_result_package(
    session: AsyncSession,
    artifact: Artifact,
    *,
    requester_organization_id: UUID,
    created_by: UUID,
    safe_files: dict[str, bytes],
    object_store: ReleaseObjectStore,
    command: AuditCommandContext,
) -> ApprovedResultPackage:
    if artifact.release_status != "quarantined":
        raise MarketplaceServiceError("source Artifact must remain quarantined")
    tasks = list(
        (
            await session.scalars(
                select(ArtifactReviewTask).where(
                    ArtifactReviewTask.artifact_id == artifact.id
                )
            )
        ).all()
    )
    decisions = {
        row.artifact_review_task_id: row
        for row in (
            await session.scalars(
                select(ArtifactReviewDecision).where(
                    ArtifactReviewDecision.artifact_review_task_id.in_(
                        [task.id for task in tasks]
                    )
                )
            )
        ).all()
    }
    required = [task for task in tasks if task.is_required]
    if not required or any(
        task.status != "decided"
        or decisions.get(task.id) is None
        or decisions[task.id].decision != "approved"
        for task in required
    ):
        raise MarketplaceServiceError("all required Artifact reviews must approve")
    archive, manifest = build_safe_result_archive(safe_files)
    review_evidence = {
        "schema_version": "result-package-review-evidence/v1",
        "artifact_id": str(artifact.id),
        "artifact_digest": artifact.content_digest,
        "decisions": sorted(
            [
                {
                    "review_type": task.review_type,
                    "decision_digest": decisions[task.id].decision_digest,
                }
                for task in required
            ],
            key=lambda item: item["review_type"],
        ),
    }
    authority = {
        "schema_version": "result-package-authority/v1",
        "artifact_policy_evaluation_digest": artifact.output_policy_evaluation_digest,
        "requester_organization_id": str(requester_organization_id),
        "safe_filename_allowlist": list(SAFE_RESULT_FILENAMES),
    }
    package = ApprovedResultPackage(
        space_id=artifact.space_id,
        artifact_id=artifact.id,
        requester_organization_id=requester_organization_id,
        package_digest=content_digest(archive),
        manifest_snapshot=manifest,
        review_evidence_digest=canonical_digest(review_evidence),
        authority_evaluation_digest=canonical_digest(authority),
        bucket_name=object_store.bucket_name,
        object_key=f"packages/{artifact.space_id}/{artifact.id}/approved-results.zip",
        size_bytes=len(archive),
        created_by=created_by,
    )
    session.add(package)
    await session.flush()
    # External storage cannot participate in PostgreSQL atomic commit.  The
    # immutable package row is created only after the release copy succeeds;
    # a transaction rollback leaves an unreferenced, non-discoverable object
    # that the operator can garbage-collect safely.
    object_store.put(package.object_key, archive, "application/zip")
    await _append(
        session,
        command=command,
        space_id=artifact.space_id,
        event_type="result.package.created",
        subject_type="result_package",
        subject_id=package.id,
        evidence=_audit_evidence(
            schema="result-package-created/v1",
            command=command,
            facts={
                "artifact_id": str(artifact.id),
                "artifact_digest": artifact.content_digest,
                "result_package_id": str(package.id),
                "package_digest": package.package_digest,
                "review_evidence_digest": package.review_evidence_digest,
                "file_names": [item["name"] for item in manifest["files"]],
            },
        ),
    )
    return package


@dataclass(frozen=True)
class DownloadGrantSecret:
    grant: ResultDownloadGrant
    token: str


async def create_download_grant(
    session: AsyncSession,
    package: ApprovedResultPackage,
    *,
    requester_organization_id: UUID,
    requester_user_id: UUID,
    command: AuditCommandContext,
    lifetime_seconds: int = 300,
    max_downloads: int = 1,
) -> DownloadGrantSecret:
    if package.status != "available" or package.requester_organization_id != requester_organization_id:
        raise MarketplaceServiceError("result package is not available to this requester")
    await require_actor(
        session,
        space_id=package.space_id,
        organization_id=requester_organization_id,
        user_id=requester_user_id,
        role_code="data_requester",
    )
    existing = await session.scalar(
        select(ResultDownloadGrant).where(
            ResultDownloadGrant.result_package_id == package.id,
            ResultDownloadGrant.requester_organization_id
            == requester_organization_id,
            ResultDownloadGrant.requester_user_id == requester_user_id,
            ResultDownloadGrant.status == "active",
            ResultDownloadGrant.expires_at > datetime.now(timezone.utc),
        )
    )
    if existing is not None:
        raise MarketplaceServiceError(
            "an active download grant already exists for this package"
        )
    token = secrets.token_urlsafe(32)
    token_hash = content_digest(token.encode("utf-8"))
    request_document = {
        "schema_version": "result-download-grant-request/v1",
        "result_package_id": str(package.id),
        "requester_organization_id": str(requester_organization_id),
        "requester_user_id": str(requester_user_id),
        "max_downloads": max_downloads,
        "lifetime_seconds": lifetime_seconds,
    }
    grant = ResultDownloadGrant(
        space_id=package.space_id,
        result_package_id=package.id,
        requester_organization_id=requester_organization_id,
        requester_user_id=requester_user_id,
        token_digest=token_hash,
        request_digest=canonical_digest(request_document),
        max_downloads=max_downloads,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds),
    )
    session.add(grant)
    await session.flush()
    await _append(
        session,
        command=command,
        space_id=package.space_id,
        event_type="result.download.grant.created",
        subject_type="result_download_grant",
        subject_id=grant.id,
        evidence=_audit_evidence(
            schema="result-download-grant-created/v1",
            command=command,
            facts={
                "result_package_id": str(package.id),
                "grant_id": str(grant.id),
                "request_digest": grant.request_digest,
                "max_downloads": max_downloads,
                "expires_at": grant.expires_at.isoformat(),
            },
        ),
    )
    return DownloadGrantSecret(grant=grant, token=token)


async def consume_download_grant(
    session: AsyncSession,
    *,
    token: str,
    requester_organization_id: UUID,
    requester_user_id: UUID,
    object_store: ReleaseObjectStore,
    command: AuditCommandContext,
) -> tuple[bytes, ApprovedResultPackage]:
    token_hash = content_digest(token.encode("utf-8"))
    grant = await session.scalar(
        select(ResultDownloadGrant)
        .where(ResultDownloadGrant.token_digest == token_hash)
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    if (
        grant is None
        or grant.status != "active"
        or grant.requester_organization_id != requester_organization_id
        or grant.requester_user_id != requester_user_id
        or grant.expires_at <= now
        or grant.download_count >= grant.max_downloads
    ):
        raise MarketplaceServiceError("download grant is invalid, expired or exhausted")
    package = await session.get(ApprovedResultPackage, grant.result_package_id)
    if package is None or package.status != "available":
        raise MarketplaceServiceError("result package is unavailable")
    payload = object_store.get(package.object_key)
    if content_digest(payload) != package.package_digest:
        raise MarketplaceServiceError("release package digest mismatch")
    grant.download_count += 1
    grant.last_downloaded_at = now
    if grant.download_count >= grant.max_downloads:
        grant.status = "exhausted"
    grant._transition_validated = True
    await session.flush()
    await _append(
        session,
        command=command,
        space_id=package.space_id,
        event_type="result.download.completed",
        subject_type="result_download_grant",
        subject_id=grant.id,
        evidence=_audit_evidence(
            schema="result-download-completed/v1",
            command=command,
            facts={
                "result_package_id": str(package.id),
                "grant_id": str(grant.id),
                "package_digest": package.package_digest,
                "download_count": grant.download_count,
                "status": grant.status,
            },
        ),
    )
    return payload, package


async def record_download_rejection(
    session: AsyncSession,
    *,
    grant: ResultDownloadGrant,
    reason_code: str,
    command: AuditCommandContext,
) -> None:
    await _append(
        session,
        command=command,
        space_id=grant.space_id,
        event_type="result.download.rejected",
        subject_type="result_download_grant",
        subject_id=grant.id,
        result="denied",
        evidence=_audit_evidence(
            schema="result-download-rejected/v1",
            command=command,
            facts={
                "result_package_id": str(grant.result_package_id),
                "grant_id": str(grant.id),
                "reason_code": reason_code,
                "status": grant.status,
                "download_count": grant.download_count,
                "expires_at": grant.expires_at.isoformat(),
            },
        ),
    )
