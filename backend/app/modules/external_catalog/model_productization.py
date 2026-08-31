from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor, command_for
from app.modules.audit.models import AuditEvent
from app.modules.audit.services import append_audit_event_with_outbox, canonical_json_digest_v1
from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalModelGovernanceProfile,
    ExternalModelGovernanceReview,
    ExternalModelRecord,
    ExternalModelVersion,
    ModelMetadataPublicationReviewTask,
    ModelProductExternalSourceLink,
)
from app.modules.marketplace.models import ModelProduct, ModelPublication, ModelVersion
from app.modules.marketplace.services import MarketplaceServiceError, publish_model_version
from app.modules.spaces.models import Space


class ExternalModelProductDraftError(ValueError):
    pass


@dataclass(frozen=True)
class ExternalModelProductDraftResult:
    product: ModelProduct
    version: ModelVersion
    link: ModelProductExternalSourceLink
    event: AuditEvent


REQUIRED_REVIEWS = (
    "source",
    "paper",
    "repository",
    "model_card",
    "license",
    "weights",
    "revision",
    "technical_contract",
    "clinical_boundary",
    "security",
    "model_family",
    "productization",
)
EXTERNAL_MODEL_METADATA_POLICY_VERSION = (
    "external-public-model-metadata-product-policy-v1"
)


def _stable_id(kind: str, record_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:phase5.12.3b2:{kind}:{record_id}")


def _latest_reviews(
    reviews: list[ExternalModelGovernanceReview],
) -> dict[str, ExternalModelGovernanceReview]:
    latest: dict[str, ExternalModelGovernanceReview] = {}
    for review in sorted(
        reviews, key=lambda item: (item.reviewed_at, item.created_at, item.id), reverse=True
    ):
        latest.setdefault(review.review_dimension, review)
    return latest


def _official_url(
    record: ExternalModelRecord, source_review: ExternalModelGovernanceReview
) -> str:
    payload = source_review.decision_payload or {}
    candidates = (
        payload.get("official_source_url"),
        source_review.evidence_reference,
        record.model_card_url,
        record.code_repository_url,
        record.paper_url,
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("https://", "http://")):
            return candidate
    raise ExternalModelProductDraftError("eligible model has no verified official URL")


def _validate(
    record: ExternalModelRecord,
    version: ExternalModelVersion | None,
    profile: ExternalModelGovernanceProfile | None,
    reviews: list[ExternalModelGovernanceReview],
) -> tuple[ExternalModelVersion, ExternalModelGovernanceProfile, dict[str, ExternalModelGovernanceReview], str]:
    if (
        profile is None
        or not profile.productization_eligible
        or profile.primary_status != "eligible_for_model_draft"
    ):
        raise ExternalModelProductDraftError("model is not currently eligible_for_model_draft")
    if version is None or version.record_id != record.id or not version.is_current:
        raise ExternalModelProductDraftError("current external model version is missing")
    if record.current_version_id != version.id or version.record_digest != record.raw_record_digest:
        raise ExternalModelProductDraftError("external model version does not match the current record")
    if record.execution_status != "not_materialized":
        raise ExternalModelProductDraftError("external model materialization boundary is invalid")

    latest = _latest_reviews(reviews)
    if any(dimension not in latest for dimension in REQUIRED_REVIEWS):
        raise ExternalModelProductDraftError("eligible model is missing a required governance review")
    if any(review.source_record_digest != record.raw_record_digest for review in latest.values()):
        raise ExternalModelProductDraftError("governance evidence is bound to another source digest")
    if latest["source"].decision not in {"official_source_confirmed", "author_source_confirmed"}:
        raise ExternalModelProductDraftError("model source is not verified")
    if latest["license"].decision in {
        "unknown", "unverified", "redistribution_prohibited", "restricted"
    }:
        raise ExternalModelProductDraftError("model license is not eligible for a metadata draft")
    if latest["weights"].decision in {"unknown", "not_released", "unavailable"}:
        raise ExternalModelProductDraftError("model weight availability is not sufficiently reviewed")
    if latest["revision"].decision not in {
        "commit_pinned", "release_tag_pinned", "model_revision_pinned"
    }:
        raise ExternalModelProductDraftError("model revision is not pinned")
    if latest["security"].decision != "cleared":
        raise ExternalModelProductDraftError("model security review is not cleared")
    if latest["productization"].decision != "approved":
        raise ExternalModelProductDraftError("model productization review is not approved")
    return version, profile, latest, _official_url(record, latest["source"])


async def _validated_publication_graph(
    session: AsyncSession,
    *,
    record_id: UUID,
) -> tuple[
    ModelProduct,
    ModelVersion,
    ModelProductExternalSourceLink,
    ExternalModelRecord,
    ExternalModelGovernanceProfile,
    dict[str, ExternalModelGovernanceReview],
]:
    link = await session.scalar(
        select(ModelProductExternalSourceLink).where(
            ModelProductExternalSourceLink.external_model_record_id == record_id
        )
    )
    if link is None:
        raise ExternalModelProductDraftError("external model source link is missing")
    product = await session.get(ModelProduct, link.model_product_id)
    model_version = await session.get(ModelVersion, link.model_version_id)
    record = await session.get(ExternalModelRecord, link.external_model_record_id)
    external_version = await session.get(
        ExternalModelVersion, link.external_model_version_id
    )
    profile = await session.get(
        ExternalModelGovernanceProfile, link.governance_profile_id
    )
    if product is None or model_version is None or record is None:
        raise ExternalModelProductDraftError(
            "external model product graph is incomplete"
        )
    reviews = list(
        (
            await session.scalars(
                select(ExternalModelGovernanceReview).where(
                    ExternalModelGovernanceReview.record_id == record.id
                )
            )
        ).all()
    )
    external_version, profile, latest, official_url = _validate(
        record, external_version, profile, reviews
    )
    review_ids = {name: str(review.id) for name, review in latest.items()}
    governance_snapshot = {
        "schema_version": "phase5.12.3b2/model-governance-snapshot/v1",
        "record_id": str(record.id),
        "record_digest": record.raw_record_digest,
        "external_version_id": str(external_version.id),
        "catalog_version": external_version.catalog_version,
        "profile_id": str(profile.id),
        "profile_status": profile.primary_status,
        "review_ids": review_ids,
        "official_url": official_url,
    }
    if (
        review_ids != link.review_ids
        or record.raw_record_digest != link.source_record_digest
        or canonical_json_digest_v1(governance_snapshot)
        != link.governance_snapshot_digest
    ):
        raise ExternalModelProductDraftError(
            "external governance snapshot no longer matches the draft"
        )
    boundary = model_version.compatibility_metadata
    if (
        link.materialization_status != "metadata_only"
        or link.weight_holder_status != "external_upstream"
        or link.execution_readiness != "not_ready"
        or link.platform_validation != "not_validated"
        or model_version.entrypoint_id != "external-metadata-only"
        or model_version.runtime != "external_metadata_only"
        or boundary.get("metadata_only") is not True
        or boundary.get("materialized") is not False
        or boundary.get("execution_ready") is not False
        or boundary.get("platform_validation") != "not_validated"
        or model_version.default_policy_template.get("execution_allowed") is not False
        or model_version.default_policy_template.get("download_allowed") is not False
    ):
        raise ExternalModelProductDraftError(
            "external model metadata publication boundary failed"
        )
    return product, model_version, link, record, profile, latest


async def _publication_event(
    session: AsyncSession,
    *,
    idempotency_key: str,
    event_type: str,
    version_id: UUID,
    request_digest: str,
) -> AuditEvent | None:
    event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.idempotency_key == idempotency_key,
            AuditEvent.event_type == event_type,
            AuditEvent.subject_type == "model_version",
            AuditEvent.subject_id == version_id,
        )
    )
    if event is not None and event.evidence_snapshot.get(
        "command_request_digest"
    ) != request_digest:
        raise ExternalModelProductDraftError(
            "idempotency key is bound to another publication request"
        )
    return event


def _publication_evidence(
    *,
    product: ModelProduct,
    link: ModelProductExternalSourceLink,
    record: ExternalModelRecord,
    task: ModelMetadataPublicationReviewTask,
) -> dict[str, Any]:
    return {
        "product_id": str(product.id),
        "external_record_id": str(record.id),
        "external_model_id": record.external_model_id,
        "review_task_id": str(task.id),
        "source_record_digest": link.source_record_digest,
        "governance_snapshot_digest": link.governance_snapshot_digest,
        "curator_organization_id": str(task.submitter_organization_id),
        "curator_user_id": str(task.submitter_user_id),
        "upstream_provider": link.upstream_provider,
        "policy_version": EXTERNAL_MODEL_METADATA_POLICY_VERSION,
        "materialization_status": "metadata_only",
        "weight_holder_status": "external_upstream",
        "executor_registered": False,
        "execution_readiness": "not_ready",
        "platform_validation": "not_validated",
        "application_eligibility": False,
        "compute_eligibility": False,
    }


async def _latest_publication_task(
    session: AsyncSession, version_id: UUID
) -> ModelMetadataPublicationReviewTask | None:
    return await session.scalar(
        select(ModelMetadataPublicationReviewTask)
        .where(ModelMetadataPublicationReviewTask.model_version_id == version_id)
        .order_by(ModelMetadataPublicationReviewTask.sequence_no.desc())
        .limit(1)
    )


async def submit_external_model_metadata_product(
    session: AsyncSession,
    *,
    record_id: UUID,
    actor: DemoActor,
    raw_key: str,
) -> tuple[ModelMetadataPublicationReviewTask, AuditEvent, AuditEvent]:
    if actor.role != "catalog_curator":
        raise ExternalModelProductDraftError(
            "only the independent catalog curator may submit this product"
        )
    product, version, link, record, _, _ = await _validated_publication_graph(
        session, record_id=record_id
    )
    request = {
        "schema_version": "phase5.12.4/model-publication-submit-request/v1",
        "version_id": str(version.id),
        "source_record_digest": link.source_record_digest,
        "governance_snapshot_digest": link.governance_snapshot_digest,
        "policy_version": EXTERNAL_MODEL_METADATA_POLICY_VERSION,
    }
    request_digest = canonical_json_digest_v1(request)
    native_command = command_for(
        actor, f"external-model-submit-native:{version.id}", f"{raw_key}:native"
    )
    external_command = command_for(
        actor, f"external-model-submit:{version.id}", f"{raw_key}:external"
    )
    native_replay = await _publication_event(
        session,
        idempotency_key=native_command.idempotency_key,
        event_type="model_product.version.submitted",
        version_id=version.id,
        request_digest=request_digest,
    )
    external_replay = await _publication_event(
        session,
        idempotency_key=external_command.idempotency_key,
        event_type="external_model_catalog.product.submitted",
        version_id=version.id,
        request_digest=request_digest,
    )
    task = await _latest_publication_task(session, version.id)
    if native_replay is not None and external_replay is not None:
        if task is None or task.submission_digest != request_digest:
            raise ExternalModelProductDraftError(
                "submitted replay is missing its review task"
            )
        return task, native_replay, external_replay
    if native_replay is not None or external_replay is not None:
        raise ExternalModelProductDraftError(
            "external model submit command is partially persisted"
        )
    if version.status != "draft" or product.lifecycle_status != "draft":
        raise ExternalModelProductDraftError(
            "only an unpublished external model draft may be submitted"
        )
    if task is not None and task.task_status == "pending":
        raise ExternalModelProductDraftError(
            "external model already has a pending publication review"
        )
    sequence_no = 1 if task is None else task.sequence_no + 1
    task = ModelMetadataPublicationReviewTask(
        id=uuid5(
            NAMESPACE_URL,
            f"medtrust:phase5.12.4:model-publication-review:{version.id}:{sequence_no}",
        ),
        space_id=version.space_id,
        model_product_id=product.id,
        model_version_id=version.id,
        external_source_link_id=link.id,
        sequence_no=sequence_no,
        task_status="pending",
        submission_digest=request_digest,
        submitter_organization_id=actor.organization_id,
        submitter_user_id=actor.user_id,
    )
    session.add(task)
    version.status = "under_review"
    version._transition_validated = True
    await session.flush()
    evidence = _publication_evidence(
        product=product, link=link, record=record, task=task
    )
    native = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="model_product.version.submitted",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.12.4/model-version-submitted/v1",
            "command_request_digest": request_digest,
            "state_before": "draft",
            "state_after": "under_review",
            **evidence,
        },
        **native_command.append_kwargs(),
    )
    external_kwargs = external_command.append_kwargs()
    external_kwargs["causation_id"] = native.event.event_id
    external = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="external_model_catalog.product.submitted",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.12.4/external-model-submitted/v1",
            "command_request_digest": request_digest,
            "state_before": "draft",
            "state_after": "under_review",
            **evidence,
        },
        **external_kwargs,
    )
    return task, native.event, external.event


async def return_external_model_metadata_product(
    session: AsyncSession,
    *,
    record_id: UUID,
    actor: DemoActor,
    review: dict[str, Any],
    raw_key: str,
) -> tuple[ModelMetadataPublicationReviewTask, AuditEvent, AuditEvent]:
    if actor.role != "space_operator":
        raise ExternalModelProductDraftError(
            "only the platform operator may return this publication"
        )
    product, version, link, record, _, _ = await _validated_publication_graph(
        session, record_id=record_id
    )
    task = await _latest_publication_task(session, version.id)
    if task is None:
        raise ExternalModelProductDraftError(
            "external model has no publication review"
        )
    if (
        task.submitter_user_id == actor.user_id
        or task.submitter_organization_id == actor.organization_id
    ):
        raise ExternalModelProductDraftError("self-review is not allowed")
    request_digest = canonical_json_digest_v1(review)
    native_command = command_for(
        actor, f"external-model-return-native:{version.id}", f"{raw_key}:native"
    )
    external_command = command_for(
        actor, f"external-model-return:{version.id}", f"{raw_key}:external"
    )
    native_replay = await _publication_event(
        session,
        idempotency_key=native_command.idempotency_key,
        event_type="model_product.version.returned",
        version_id=version.id,
        request_digest=request_digest,
    )
    external_replay = await _publication_event(
        session,
        idempotency_key=external_command.idempotency_key,
        event_type="external_model_catalog.product.publication.rejected",
        version_id=version.id,
        request_digest=request_digest,
    )
    if native_replay is not None and external_replay is not None:
        return task, native_replay, external_replay
    if native_replay is not None or external_replay is not None:
        raise ExternalModelProductDraftError(
            "external model return command is partially persisted"
        )
    if task.task_status != "pending" or version.status != "under_review":
        raise ExternalModelProductDraftError(
            "external model is not awaiting publication review"
        )
    now = datetime.now(timezone.utc)
    task.task_status = "decided"
    task.decision = "returned"
    task.review_digest = request_digest
    task.reviewer_organization_id = actor.organization_id
    task.reviewer_user_id = actor.user_id
    task.decided_at = now
    version.status = "draft"
    version._transition_validated = True
    await session.flush()
    evidence = _publication_evidence(
        product=product, link=link, record=record, task=task
    )
    native = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="model_product.version.returned",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.12.4/model-version-returned/v1",
            "command_request_digest": request_digest,
            "review": review,
            "state_before": "under_review",
            "state_after": "draft",
            **evidence,
        },
        **native_command.append_kwargs(),
    )
    external_kwargs = external_command.append_kwargs()
    external_kwargs["causation_id"] = native.event.event_id
    external = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="external_model_catalog.product.publication.rejected",
        subject_type="model_version",
        subject_id=version.id,
        result="denied",
        evidence_snapshot={
            "schema_version": "phase5.12.4/external-model-returned/v1",
            "command_request_digest": request_digest,
            "decision": "returned",
            "state_before": "under_review",
            "state_after": "draft",
            **evidence,
        },
        **external_kwargs,
    )
    return task, native.event, external.event


async def approve_and_publish_external_model_metadata_product(
    session: AsyncSession,
    *,
    record_id: UUID,
    actor: DemoActor,
    review: dict[str, Any],
    raw_key: str,
) -> tuple[
    ModelMetadataPublicationReviewTask,
    ModelPublication,
    AuditEvent,
    AuditEvent,
    AuditEvent,
]:
    if actor.role != "space_operator":
        raise ExternalModelProductDraftError(
            "only the platform operator may approve this publication"
        )
    if review.get("allow_catalog") is not True:
        raise ExternalModelProductDraftError(
            "approval requires explicit catalog visibility confirmation"
        )
    product, version, link, record, _, _ = await _validated_publication_graph(
        session, record_id=record_id
    )
    task = await _latest_publication_task(session, version.id)
    if task is None:
        raise ExternalModelProductDraftError(
            "external model has no publication review"
        )
    if (
        task.submitter_user_id == actor.user_id
        or task.submitter_organization_id == actor.organization_id
    ):
        raise ExternalModelProductDraftError("self-approval is not allowed")
    request_digest = canonical_json_digest_v1(review)
    commands = {
        "approved": command_for(
            actor, f"external-model-approve:{version.id}", f"{raw_key}:approve"
        ),
        "published": command_for(
            actor, f"external-model-publish:{version.id}", f"{raw_key}:publish"
        ),
        "external": command_for(
            actor, f"external-model-publication:{version.id}", f"{raw_key}:external"
        ),
    }
    replays = {
        "approved": await _publication_event(
            session,
            idempotency_key=commands["approved"].idempotency_key,
            event_type="model_product.version.approved",
            version_id=version.id,
            request_digest=request_digest,
        ),
        "published": await _publication_event(
            session,
            idempotency_key=commands["published"].idempotency_key,
            event_type="model_product.version.published",
            version_id=version.id,
            request_digest=request_digest,
        ),
        "external": await _publication_event(
            session,
            idempotency_key=commands["external"].idempotency_key,
            event_type="external_model_catalog.product.published",
            version_id=version.id,
            request_digest=request_digest,
        ),
    }
    publication = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == version.id,
            ModelPublication.status == "active",
        )
    )
    if all(replays.values()):
        if publication is None:
            raise ExternalModelProductDraftError(
                "published replay is missing its publication"
            )
        return (
            task,
            publication,
            replays["approved"],
            replays["published"],
            replays["external"],
        )
    if any(replays.values()):
        raise ExternalModelProductDraftError(
            "external model approval is partially persisted"
        )
    if task.task_status != "pending" or version.status != "under_review":
        raise ExternalModelProductDraftError(
            "external model is not awaiting publication review"
        )
    now = datetime.now(timezone.utc)
    task.task_status = "decided"
    task.decision = "approved"
    task.review_digest = request_digest
    task.reviewer_organization_id = actor.organization_id
    task.reviewer_user_id = actor.user_id
    task.decided_at = now
    version.status = "approved"
    version.approved_at = now
    version.approved_by = actor.user_id
    version._transition_validated = True
    await session.flush()
    evidence = _publication_evidence(
        product=product, link=link, record=record, task=task
    )
    approved = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="model_product.version.approved",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.12.4/model-version-approved/v1",
            "command_request_digest": request_digest,
            "review": review,
            "operator_organization_id": str(actor.organization_id),
            "operator_user_id": str(actor.user_id),
            "state_before": "under_review",
            "state_after": "approved",
            **evidence,
        },
        **commands["approved"].append_kwargs(),
    )
    try:
        publication = await publish_model_version(
            session,
            product,
            version,
            operator_organization_id=actor.organization_id,
            operator_user_id=actor.user_id,
            command=commands["published"],
            visibility="space",
            evidence_facts={
                "command_request_digest": request_digest,
                **evidence,
            },
        )
    except MarketplaceServiceError as exc:
        raise ExternalModelProductDraftError(str(exc)) from exc
    published = await _publication_event(
        session,
        idempotency_key=commands["published"].idempotency_key,
        event_type="model_product.version.published",
        version_id=version.id,
        request_digest=request_digest,
    )
    assert published is not None
    external_kwargs = commands["external"].append_kwargs()
    external_kwargs["causation_id"] = published.event_id
    external = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="external_model_catalog.product.published",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.12.4/external-model-published/v1",
            "command_request_digest": request_digest,
            "publication_id": str(publication.id),
            "published_at": publication.published_at.isoformat(),
            "operator_organization_id": str(actor.organization_id),
            "operator_user_id": str(actor.user_id),
            "state_before": "approved",
            "state_after": "published",
            **evidence,
        },
        **external_kwargs,
    )
    return task, publication, approved.event, published, external.event


async def create_external_model_metadata_draft(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: DemoActor,
    record: ExternalModelRecord,
    source: ExternalCatalogSource,
    version: ExternalModelVersion | None,
    profile: ExternalModelGovernanceProfile | None,
    reviews: list[ExternalModelGovernanceReview],
    curator_note: str,
    raw_key: str,
) -> ExternalModelProductDraftResult:
    version, profile, latest, official_url = _validate(record, version, profile, reviews)
    if source.space_id != space_id or source.resource_kind != "model" or record.source_id != source.id:
        raise ExternalModelProductDraftError("external model is outside the current space")

    product_id = _stable_id("model-product", record.id)
    model_version_id = _stable_id("model-version", record.id)
    link_id = _stable_id("source-link", record.id)
    request = {
        "schema_version": "phase5.12.3b2/model-product-draft-request/v1",
        "record_id": str(record.id),
        "version_id": str(version.id),
        "source_record_digest": record.raw_record_digest,
        "curator_note": curator_note.strip(),
    }
    request_digest = canonical_json_digest_v1(request)
    command = command_for(actor, f"external-model-product-draft:{record.id}", raw_key)

    locked_space = await session.scalar(select(Space).where(Space.id == space_id).with_for_update())
    if locked_space is None:
        raise ExternalModelProductDraftError("space does not exist")
    existing_link = await session.scalar(
        select(ModelProductExternalSourceLink)
        .where(ModelProductExternalSourceLink.external_model_record_id == record.id)
        .with_for_update()
    )
    existing_event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.space_id == space_id,
            AuditEvent.idempotency_key == command.idempotency_key,
            AuditEvent.event_type == "model_product.version.created",
            AuditEvent.subject_type == "model_version",
            AuditEvent.subject_id == model_version_id,
        )
    )
    if existing_link is not None:
        product = await session.get(ModelProduct, existing_link.model_product_id)
        model_version = await session.get(ModelVersion, existing_link.model_version_id)
        if product is None or model_version is None or existing_event is None:
            raise ExternalModelProductDraftError("existing external model draft graph is incomplete")
        if model_version.compatibility_metadata.get("request_digest") != request_digest:
            raise ExternalModelProductDraftError("external model already has a different draft request")
        return ExternalModelProductDraftResult(product, model_version, existing_link, existing_event)
    if existing_event is not None:
        raise ExternalModelProductDraftError("audit fact exists without its external model draft")

    governance_snapshot = {
        "schema_version": "phase5.12.3b2/model-governance-snapshot/v1",
        "record_id": str(record.id),
        "record_digest": record.raw_record_digest,
        "external_version_id": str(version.id),
        "catalog_version": version.catalog_version,
        "profile_id": str(profile.id),
        "profile_status": profile.primary_status,
        "review_ids": {name: str(review.id) for name, review in latest.items()},
        "official_url": official_url,
    }
    governance_digest = canonical_json_digest_v1(governance_snapshot)
    boundary = {
        "schema_version": "phase5.12.3b2/model-metadata-boundary/v1",
        "metadata_only": True,
        "materialized": False,
        "local_weights": False,
        "execution_image": None,
        "execution_ready": False,
        "platform_validation": "not_validated",
        "external_model_record_id": str(record.id),
        "external_model_version_id": str(version.id),
        "governance_snapshot_digest": governance_digest,
        "request_digest": request_digest,
    }
    policy = {
        "schema_version": "phase5.12.3b2/model-metadata-policy/v1",
        "allowed_purposes": ["catalog_discovery", "governance_revalidation"],
        "prohibited_purposes": [
            "weight_download",
            "model_execution",
            "application_selection",
            "contract_readiness",
        ],
        "execution_allowed": False,
        "download_allowed": False,
        "internet_allowed": False,
        "hard_isolation": False,
    }
    product = ModelProduct(
        id=product_id,
        space_id=space_id,
        provider_organization_id=actor.organization_id,
        product_code=f"EXT-MP-{record.id.hex[:10].upper()}",
        name=record.display_name_en or record.display_name_cn or record.canonical_name,
        description=(
            "Metadata-only external public model catalog entry. No model weights or "
            "execution image are hosted, downloaded, registered or executable."
        ),
        domain=", ".join(record.disease_areas) or "external public model",
        lifecycle_status="draft",
        is_demo=False,
        created_by=actor.user_id,
    )
    manifest = {
        "schema_version": "phase5.12.3b2/model-metadata-manifest/v1",
        "product_code": product.product_code,
        "external_model_id": record.external_model_id,
        "source_record_digest": record.raw_record_digest,
        "governance_snapshot_digest": governance_digest,
        "weights_included": False,
    }
    metadata_digest = canonical_json_digest_v1(manifest)
    model_version = ModelVersion(
        id=model_version_id,
        space_id=space_id,
        model_product_id=product_id,
        version_no=1,
        version_label=record.revision or record.commit_sha or record.release_tag or version.catalog_version,
        status="draft",
        entrypoint_id="external-metadata-only",
        model_digest=metadata_digest,
        manifest_digest=metadata_digest,
        registry_digest=canonical_json_digest_v1(
            {"schema_version": "phase5.12.3b2/no-registry-entry/v1", "registered": False}
        ),
        runtime="external_metadata_only",
        input_schema_version="external-metadata/v1",
        output_schema_version="external-metadata/v1",
        compatibility_metadata={
            **boundary,
            "framework": record.framework,
            "architecture": record.architecture,
            "input_schema": record.input_schema,
            "output_schema": record.output_schema,
            "preprocessing_summary": record.preprocessing_summary,
        },
        license_metadata={
            "schema_version": "phase5.12.3b2/external-model-license/v1",
            "license_name": record.license_name,
            "license_url": record.license_url,
            "review_status": profile.license_review_status,
            "upstream_provider": record.upstream_provider,
            "weights_status": profile.weight_review_status,
        },
        default_policy_template=policy,
        default_policy_digest=canonical_json_digest_v1(policy),
        snapshot_digest=canonical_json_digest_v1(
            {"schema_version": "phase5.12.3b2/model-version-snapshot/v1", **governance_snapshot}
        ),
        created_by=actor.user_id,
    )
    link = ModelProductExternalSourceLink(
        id=link_id,
        model_product_id=product_id,
        model_version_id=model_version_id,
        external_model_record_id=record.id,
        external_model_version_id=version.id,
        external_catalog_source_id=source.id,
        external_model_id=record.external_model_id,
        catalog_version=version.catalog_version,
        source_record_digest=record.raw_record_digest,
        governance_profile_id=profile.id,
        governance_snapshot_digest=governance_digest,
        review_ids={name: str(review.id) for name, review in latest.items()},
        upstream_official_url=official_url,
        upstream_provider=record.upstream_provider,
        curator_organization_id=actor.organization_id,
        materialization_status="metadata_only",
        weight_holder_status="external_upstream",
        execution_readiness="not_ready",
        platform_validation="not_validated",
        created_by=actor.user_id,
    )
    session.add(product)
    await session.flush([product])
    session.add(model_version)
    await session.flush([model_version])
    session.add(link)
    await session.flush([link])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type="model_product.version.created",
        subject_type="model_version",
        subject_id=model_version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.12.3b2/external-model-product-created/v1",
            "command_request_digest": request_digest,
            "product_code": product.product_code,
            "record_id": str(record.id),
            "source_record_digest": record.raw_record_digest,
            "governance_snapshot_digest": governance_digest,
            "materialization_status": "metadata_only",
            "weight_holder_status": "external_upstream",
            "execution_readiness": "not_ready",
            "platform_validation": "not_validated",
            "state_before": None,
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return ExternalModelProductDraftResult(product, model_version, link, appended.event)
