from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor, command_for
from app.modules.audit.models import AuditEvent
from app.modules.audit.services import (
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
)
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
    DataResource,
)
from app.modules.catalog.services import (
    CatalogInvariantError,
    approve_version,
    publish_version,
    submit_version_for_review,
)
from app.modules.catalog.product_lifecycle import return_product_version
from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ExternalCatalogSource,
    ExternalDatasetGovernanceProfile,
    ExternalDatasetGovernanceReview,
    ExternalDatasetRecord,
    ExternalDatasetVersion,
)
from app.modules.spaces.models import Space


class ExternalProductDraftError(ValueError):
    pass


EXTERNAL_METADATA_POLICY_VERSION = "external-public-metadata-product-policy-v1"


@dataclass(frozen=True)
class ExternalProductDraftResult:
    product: DataProduct
    version: DataProductVersion
    resource: DataResource
    link: DataProductExternalSourceLink
    event: AuditEvent


async def discard_external_metadata_draft(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: DemoActor,
    record_id: UUID,
    reason: str,
    raw_key: str,
) -> AuditEvent:
    """Discard one unneeded B2 draft without deleting its audit evidence.

    This command is deliberately narrower than the general Phase 5.9 lifecycle
    flow: it accepts only an unpublished external metadata draft with no
    publication, and leaves the source link and version for traceability.
    """

    link = await session.scalar(
        select(DataProductExternalSourceLink)
        .where(DataProductExternalSourceLink.external_dataset_record_id == record_id)
        .with_for_update()
    )
    if link is None:
        raise ExternalProductDraftError("external metadata draft does not exist")
    product = await session.scalar(
        select(DataProduct).where(
            DataProduct.id == link.data_product_id,
            DataProduct.space_id == space_id,
        ).with_for_update()
    )
    version = await session.get(DataProductVersion, link.data_product_version_id)
    if product is None or version is None:
        raise ExternalProductDraftError("external product draft graph is incomplete")
    if link.curator_organization_id != actor.organization_id:
        raise ExternalProductDraftError("only the curator can discard this external draft")
    if product.lifecycle_status not in {"draft", "archived"}:
        raise ExternalProductDraftError("only an unpublished external draft can be discarded")
    publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_id == product.id,
            DataProductPublication.status == "active",
        )
    )
    if publication is not None:
        raise ExternalProductDraftError("published external products cannot use B2 discard")
    if version.status != "draft":
        raise ExternalProductDraftError("only a draft version can use B2 discard")

    request = {
        "schema_version": "phase5.11.3b2/external-draft-discard-request/v1",
        "record_id": str(record_id),
        "product_id": str(product.id),
        "reason": reason.strip(),
    }
    request_digest = canonical_json_digest_v1(request)
    command = command_for(actor, f"external-data-product-draft-discard:{record_id}", raw_key)
    existing = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.space_id == space_id,
            AuditEvent.idempotency_key == command.idempotency_key,
            AuditEvent.event_type == "data_product.version.updated",
            AuditEvent.subject_type == "data_product_version",
            AuditEvent.subject_id == version.id,
        )
    )
    if existing is not None:
        if existing.evidence_snapshot.get("command_request_digest") != request_digest:
            raise ExternalProductDraftError("discard idempotency key was reused with a different reason")
        return existing
    if product.lifecycle_status == "archived":
        raise ExternalProductDraftError("external draft was already discarded")

    product.lifecycle_status = "archived"
    product.deleted_at = datetime.now(timezone.utc)
    product.row_version += 1
    await session.flush()
    appended = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type="data_product.version.updated",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.11.3b2/external-draft-discarded/v1",
            "command_request_digest": request_digest,
            "record_id": str(record_id),
            "product_id": str(product.id),
            "state_before": "draft",
            "state_after": "archived",
            "materialization_status": "metadata_only",
        },
        **command.append_kwargs(),
    )
    return appended.event


def _latest_reviews(
    reviews: list[ExternalDatasetGovernanceReview],
) -> dict[str, ExternalDatasetGovernanceReview]:
    latest: dict[str, ExternalDatasetGovernanceReview] = {}
    for review in reviews:
        latest.setdefault(review.review_dimension, review)
    return latest


def _review_payload(review: ExternalDatasetGovernanceReview) -> dict[str, Any]:
    return review.decision_payload if isinstance(review.decision_payload, dict) else {}


def _official_source(
    record: ExternalDatasetRecord,
    review: ExternalDatasetGovernanceReview,
) -> tuple[str, str | None]:
    payload = _review_payload(review)
    url = payload.get("official_source_url") or review.evidence_reference
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise ExternalProductDraftError("eligible dataset has no verified official source URL")
    holder = payload.get("rights_holder") or record.official_source_name
    return url, holder if isinstance(holder, str) and holder.strip() else None


def _redistribution_status(review: ExternalDatasetGovernanceReview) -> str:
    payload = _review_payload(review)
    value = payload.get("redistribution")
    if value is True or value == "true":
        return "allowed"
    if value is False or value == "false":
        return "prohibited"
    if review.decision == "permissive":
        return "allowed"
    if review.decision in {"research_only", "noncommercial", "controlled", "custom_terms"}:
        return "restricted"
    return "unknown"


def _product_id(record_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:phase5.11.3b2:external-product:{record_id}")


def _version_id(record_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:phase5.11.3b2:external-version:{record_id}:1")


def _resource_id(record_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:phase5.11.3b2:external-resource:{record_id}:1")


def _link_id(record_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:phase5.11.3b2:external-source-link:{record_id}")


def _validate_inputs(
    *,
    record: ExternalDatasetRecord,
    version: ExternalDatasetVersion | None,
    profile: ExternalDatasetGovernanceProfile | None,
    latest: dict[str, ExternalDatasetGovernanceReview],
) -> tuple[ExternalDatasetVersion, str, str | None, str, dict[str, ExternalDatasetGovernanceReview]]:
    if profile is None or not profile.productization_eligible or profile.primary_status != "eligible_for_draft":
        raise ExternalProductDraftError("dataset is not currently eligible_for_draft")
    if version is None or version.record_id != record.id or not version.is_current:
        raise ExternalProductDraftError("current external dataset version is missing")
    required = ("source", "license", "access", "productization")
    if any(name not in latest for name in required):
        raise ExternalProductDraftError("eligible dataset is missing a required governance review")
    if any(review.source_record_digest != record.raw_record_digest for review in latest.values()):
        raise ExternalProductDraftError("governance evidence is bound to a different source digest")
    if latest["source"].decision != "official_source_confirmed":
        raise ExternalProductDraftError("official source review is not confirmed")
    if latest["license"].decision in {"unknown", "unverified"}:
        raise ExternalProductDraftError("license review is not sufficiently verified")
    if latest["access"].decision == "unknown":
        raise ExternalProductDraftError("access review is not sufficiently verified")
    if latest["productization"].decision != "approved":
        raise ExternalProductDraftError("productization review is not approved")
    if profile.duplicate_review_status not in {"not_duplicate", "duplicate_resolved", "separate_valid_record"}:
        raise ExternalProductDraftError("duplicate status is not resolved")
    official_url, rights_holder = _official_source(record, latest["source"])
    redistribution = _redistribution_status(latest["license"])
    return version, official_url, rights_holder, redistribution, latest


async def _validated_publication_graph(
    session: AsyncSession,
    *,
    version: DataProductVersion,
) -> tuple[
    DataProduct,
    DataProductExternalSourceLink,
    ExternalDatasetRecord,
    ExternalDatasetGovernanceProfile,
    dict[str, ExternalDatasetGovernanceReview],
]:
    product = await session.get(DataProduct, version.data_product_id)
    link = await session.scalar(
        select(DataProductExternalSourceLink).where(
            DataProductExternalSourceLink.data_product_version_id == version.id
        )
    )
    if product is None or link is None:
        raise ExternalProductDraftError("external metadata product graph is incomplete")
    if product.lifecycle_status == "archived":
        raise ExternalProductDraftError("archived external products cannot be submitted")
    if (
        version.default_use_mode != "external_metadata_catalog"
        or link.materialization_status != "metadata_only"
        or link.data_holder_status != "external_upstream"
        or link.execution_readiness != "not_ready"
    ):
        raise ExternalProductDraftError("external metadata publication invariants failed")

    record = await session.get(ExternalDatasetRecord, link.external_dataset_record_id)
    external_version = await session.get(
        ExternalDatasetVersion, link.external_dataset_version_id
    )
    profile = await session.get(
        ExternalDatasetGovernanceProfile, link.governance_profile_id
    )
    review_ids = {
        "source": link.source_review_id,
        "license": link.license_review_id,
        "access": link.access_review_id,
        "productization": link.productization_review_id,
    }
    reviews = {
        name: await session.get(ExternalDatasetGovernanceReview, review_id)
        for name, review_id in review_ids.items()
    }
    if record is None or any(review is None for review in reviews.values()):
        raise ExternalProductDraftError("external governance evidence is incomplete")
    typed_reviews = {
        name: review
        for name, review in reviews.items()
        if review is not None
    }
    latest_reviews: dict[str, ExternalDatasetGovernanceReview] = {}
    for dimension in review_ids:
        latest_review = await session.scalar(
            select(ExternalDatasetGovernanceReview)
            .where(
                ExternalDatasetGovernanceReview.record_id == record.id,
                ExternalDatasetGovernanceReview.review_dimension == dimension,
            )
            .order_by(
                ExternalDatasetGovernanceReview.reviewed_at.desc(),
                ExternalDatasetGovernanceReview.created_at.desc(),
                ExternalDatasetGovernanceReview.id.desc(),
            )
            .limit(1)
        )
        if latest_review is None or latest_review.id != review_ids[dimension]:
            raise ExternalProductDraftError(
                "external governance snapshot does not contain the latest reviews"
            )
        latest_reviews[dimension] = latest_review
    (
        validated_version,
        official_url,
        _,
        redistribution,
        _,
    ) = _validate_inputs(
        record=record,
        version=external_version,
        profile=profile,
        latest=latest_reviews,
    )
    if (
        record.current_version_id != validated_version.id
        or link.external_id != record.external_id
        or link.catalog_version != validated_version.catalog_version
        or link.source_record_digest != record.raw_record_digest
        or link.upstream_official_url != official_url
        or link.redistribution_status != redistribution
    ):
        raise ExternalProductDraftError("external source snapshot no longer matches")
    governance_snapshot = {
        "schema_version": "phase5.11.3b2/governance-snapshot/v1",
        "record_id": str(record.id),
        "record_digest": record.raw_record_digest,
        "version_id": str(validated_version.id),
        "catalog_version": validated_version.catalog_version,
        "profile_id": str(profile.id),
        "profile_status": profile.primary_status,
        "profile_license_status": profile.license_review_status,
        "profile_access_status": profile.access_review_status,
        "review_ids": {
            name: str(review.id) for name, review in typed_reviews.items()
        },
        "official_source_url": official_url,
        "redistribution_status": redistribution,
    }
    if canonical_json_digest_v1(governance_snapshot) != link.governance_snapshot_digest:
        raise ExternalProductDraftError("governance snapshot digest no longer matches")
    return product, link, record, profile, typed_reviews


async def _event_for_command(
    session: AsyncSession,
    *,
    command_idempotency_key: str,
    event_type: str,
    version_id: UUID,
    request_digest: str,
) -> AuditEvent | None:
    event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.idempotency_key == command_idempotency_key,
            AuditEvent.event_type == event_type,
            AuditEvent.subject_type == "data_product_version",
            AuditEvent.subject_id == version_id,
        )
    )
    if event is not None and event.evidence_snapshot.get(
        "command_request_digest"
    ) != request_digest:
        raise ExternalProductDraftError(
            "idempotency key is already bound to another publication request"
        )
    return event


def _publication_evidence(
    *,
    product: DataProduct,
    link: DataProductExternalSourceLink,
    record: ExternalDatasetRecord,
    curator: DemoActor,
    operator: DemoActor | None,
) -> dict[str, Any]:
    return {
        "product_id": str(product.id),
        "external_record_id": str(record.id),
        "external_id": record.external_id,
        "source_record_digest": link.source_record_digest,
        "governance_snapshot_digest": link.governance_snapshot_digest,
        "draft_creator_organization_id": str(link.curator_organization_id),
        "publication_curator_organization_id": str(curator.organization_id),
        "publication_curator_user_id": str(curator.user_id),
        "platform_operator_organization_id": (
            str(operator.organization_id) if operator else None
        ),
        "platform_operator_user_id": str(operator.user_id) if operator else None,
        "policy_version": EXTERNAL_METADATA_POLICY_VERSION,
        "materialization_status": "metadata_only",
        "data_holder_status": "external_upstream",
        "execution_readiness": "not_ready",
        "application_eligibility": False,
    }


async def submit_external_metadata_version(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    actor: DemoActor,
    raw_key: str,
) -> tuple[AuditEvent, AuditEvent]:
    if actor.role != "catalog_curator":
        raise ExternalProductDraftError(
            "only the independent catalog curator may submit external metadata products"
        )
    product, link, record, _, _ = await _validated_publication_graph(
        session, version=version
    )
    if (
        actor.organization_id == link.curator_organization_id
        or actor.organization_id == product.provider_organization_id
    ):
        raise ExternalProductDraftError(
            "publication curator must be independent from the original draft owner"
        )
    request = {
        "schema_version": "phase5.11.4/external-product-submit-request/v1",
        "version_id": str(version.id),
        "snapshot_digest": version.snapshot_digest,
        "source_record_digest": link.source_record_digest,
        "governance_snapshot_digest": link.governance_snapshot_digest,
        "policy_version": EXTERNAL_METADATA_POLICY_VERSION,
    }
    request_digest = canonical_json_digest_v1(request)
    native_command = command_for(
        actor, f"external-product-submit-native:{version.id}", f"{raw_key}:native"
    )
    external_command = command_for(
        actor, f"external-product-submit:{version.id}", f"{raw_key}:external"
    )
    native_replay = await _event_for_command(
        session,
        command_idempotency_key=native_command.idempotency_key,
        event_type="data_product.version.submitted",
        version_id=version.id,
        request_digest=request_digest,
    )
    external_replay = await _event_for_command(
        session,
        command_idempotency_key=external_command.idempotency_key,
        event_type="external_catalog.product.submitted",
        version_id=version.id,
        request_digest=request_digest,
    )
    if native_replay is not None and external_replay is not None:
        return native_replay, external_replay
    if native_replay is not None or external_replay is not None:
        raise ExternalProductDraftError("external submit command is partially persisted")
    try:
        await submit_version_for_review(
            session, version, require_bound_source=False
        )
    except CatalogInvariantError as exc:
        raise ExternalProductDraftError(str(exc)) from exc
    evidence = _publication_evidence(
        product=product, link=link, record=record, curator=actor, operator=None
    )
    native = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="data_product.version.submitted",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.11.4/data-product-submitted/v1",
            "command_request_digest": request_digest,
            "snapshot_digest": version.snapshot_digest,
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
        event_type="external_catalog.product.submitted",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.11.4/external-product-submitted/v1",
            "command_request_digest": request_digest,
            "state_before": "draft",
            "state_after": "under_review",
            **evidence,
        },
        **external_kwargs,
    )
    return native.event, external.event


async def return_external_metadata_version(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    actor: DemoActor,
    review: dict[str, Any],
    raw_key: str,
) -> tuple[AuditEvent, AuditEvent]:
    product, link, record, _, _ = await _validated_publication_graph(
        session, version=version
    )
    request_digest = canonical_json_digest_v1(review)
    native = await return_product_version(
        session,
        version=version,
        actor=actor,
        review=review,
        raw_key=f"{raw_key}:native",
    )
    command = command_for(
        actor, f"external-product-publication-rejected:{version.id}", f"{raw_key}:external"
    )
    replay = await _event_for_command(
        session,
        command_idempotency_key=command.idempotency_key,
        event_type="external_catalog.product.publication.rejected",
        version_id=version.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return native, replay
    kwargs = command.append_kwargs()
    kwargs["causation_id"] = native.event_id
    appended = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="external_catalog.product.publication.rejected",
        subject_type="data_product_version",
        subject_id=version.id,
        result="denied",
        evidence_snapshot={
            "schema_version": "phase5.11.4/external-product-publication-rejected/v1",
            "command_request_digest": request_digest,
            "product_id": str(product.id),
            "external_record_id": str(record.id),
            "source_record_digest": link.source_record_digest,
            "governance_snapshot_digest": link.governance_snapshot_digest,
            "state_before": "under_review",
            "state_after": "draft",
            "decision": "returned",
        },
        **kwargs,
    )
    return native, appended.event


async def approve_and_publish_external_metadata_version(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    actor: DemoActor,
    review: dict[str, Any],
    raw_key: str,
) -> tuple[DataProductPublication, AuditEvent, AuditEvent, AuditEvent]:
    if actor.role != "space_operator":
        raise ExternalProductDraftError(
            "only the platform operator may approve external metadata products"
        )
    if review.get("allow_catalog") is not True:
        raise ExternalProductDraftError("approval requires catalog visibility")
    product, link, record, _, _ = await _validated_publication_graph(
        session, version=version
    )
    submitted = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "external_catalog.product.submitted",
            AuditEvent.subject_type == "data_product_version",
            AuditEvent.subject_id == version.id,
            AuditEvent.result == "success",
        )
        .order_by(AuditEvent.stream_sequence.desc())
        .limit(1)
    )
    if submitted is None:
        raise ExternalProductDraftError(
            "external metadata product has no curator submission"
        )
    if (
        submitted.actor_user_id == actor.user_id
        or submitted.actor_organization_id == actor.organization_id
    ):
        raise ExternalProductDraftError("self-approval is not allowed")
    request = {
        "schema_version": "phase5.11.4/external-product-approval-request/v1",
        "version_id": str(version.id),
        "review": review,
        "submit_event_id": str(submitted.event_id),
        "policy_version": EXTERNAL_METADATA_POLICY_VERSION,
    }
    request_digest = canonical_json_digest_v1(request)
    commands = {
        "approved": command_for(
            actor, f"external-product-approve:{version.id}", f"{raw_key}:approve"
        ),
        "published": command_for(
            actor, f"external-product-publish:{version.id}", f"{raw_key}:publish"
        ),
        "external": command_for(
            actor,
            f"external-product-publication:{version.id}",
            f"{raw_key}:external",
        ),
    }
    replays = {
        "approved": await _event_for_command(
            session,
            command_idempotency_key=commands["approved"].idempotency_key,
            event_type="data_product.version.approved",
            version_id=version.id,
            request_digest=request_digest,
        ),
        "published": await _event_for_command(
            session,
            command_idempotency_key=commands["published"].idempotency_key,
            event_type="data_product.version.published",
            version_id=version.id,
            request_digest=request_digest,
        ),
        "external": await _event_for_command(
            session,
            command_idempotency_key=commands["external"].idempotency_key,
            event_type="external_catalog.product.published",
            version_id=version.id,
            request_digest=request_digest,
        ),
    }
    publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == version.id,
            DataProductPublication.status == "active",
        )
    )
    if all(replays.values()):
        if publication is None:
            raise ExternalProductDraftError(
                "published replay is missing its active publication"
            )
        return (
            publication,
            replays["approved"],
            replays["published"],
            replays["external"],
        )
    if any(replays.values()):
        raise ExternalProductDraftError("external approval command is partially persisted")
    try:
        await approve_version(session, version, approved_by=actor.user_id)
        evidence = _publication_evidence(
            product=product,
            link=link,
            record=record,
            curator=DemoActor(
                role="catalog_curator",
                organization_id=submitted.actor_organization_id,
                user_id=submitted.actor_user_id,
                organization_name="",
                user_name="",
            ),
            operator=actor,
        )
        approved = await append_audit_event_with_outbox(
            session,
            space_id=version.space_id,
            event_type="data_product.version.approved",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.11.4/data-product-approved/v1",
                "command_request_digest": request_digest,
                "review_opinion": review["review_opinion"],
                "additional_conditions": review["additional_conditions"],
                "risk_level": review["risk_level"],
                "state_before": "under_review",
                "state_after": "approved",
                **evidence,
            },
            **commands["approved"].append_kwargs(),
        )
        publication = await publish_version(
            session,
            product,
            version,
            published_by=actor.user_id,
            visibility="space",
        )
        published_kwargs = commands["published"].append_kwargs()
        published_kwargs["causation_id"] = approved.event.event_id
        published = await append_audit_event_with_outbox(
            session,
            space_id=version.space_id,
            event_type="data_product.version.published",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.11.4/data-product-published/v1",
                "command_request_digest": request_digest,
                "publication_id": str(publication.id),
                "visibility": publication.visibility,
                "state_before": "approved",
                "state_after": "published",
                **evidence,
            },
            **published_kwargs,
        )
        external_kwargs = commands["external"].append_kwargs()
        external_kwargs["causation_id"] = published.event.event_id
        external = await append_audit_event_with_outbox(
            session,
            space_id=version.space_id,
            event_type="external_catalog.product.published",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.11.4/external-product-published/v1",
                "command_request_digest": request_digest,
                "publication_id": str(publication.id),
                "published_at": publication.published_at.isoformat(),
                "state_before": "approved",
                "state_after": "published",
                **evidence,
            },
            **external_kwargs,
        )
    except CatalogInvariantError as exc:
        raise ExternalProductDraftError(str(exc)) from exc
    return publication, approved.event, published.event, external.event


async def create_external_metadata_draft(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: DemoActor,
    record: ExternalDatasetRecord,
    source: ExternalCatalogSource,
    version: ExternalDatasetVersion | None,
    profile: ExternalDatasetGovernanceProfile | None,
    reviews: list[ExternalDatasetGovernanceReview],
    curator_note: str,
    raw_key: str,
) -> ExternalProductDraftResult:
    version, official_url, rights_holder, redistribution, latest = _validate_inputs(
        record=record,
        version=version,
        profile=profile,
        latest=_latest_reviews(reviews),
    )
    if source.space_id != space_id or record.source_id != source.id:
        raise ExternalProductDraftError("external dataset is outside the current space")

    request_snapshot = {
        "schema_version": "phase5.11.3b2/external-product-draft-request/v1",
        "record_id": str(record.id),
        "external_id": record.external_id,
        "source_record_digest": record.raw_record_digest,
        "external_dataset_version_id": str(version.id),
        "catalog_version": version.catalog_version,
        "curator_note": curator_note.strip(),
    }
    request_digest = canonical_json_digest_v1(request_snapshot)
    command = command_for(actor, f"external-data-product-draft:{record.id}", raw_key)

    # The Space lock serializes the deterministic graph and the audit stream.
    locked_space = await session.scalar(select(Space).where(Space.id == space_id).with_for_update())
    if locked_space is None:
        raise ExternalProductDraftError("space does not exist")

    existing_link = await session.scalar(
        select(DataProductExternalSourceLink)
        .where(DataProductExternalSourceLink.external_dataset_record_id == record.id)
        .with_for_update()
    )
    product_id = _product_id(record.id)
    version_id = _version_id(record.id)
    existing_event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.space_id == space_id,
            AuditEvent.idempotency_key == command.idempotency_key,
            AuditEvent.event_type == "data_product.version.created",
            AuditEvent.subject_type == "data_product_version",
            AuditEvent.subject_id == version_id,
        )
    )
    if existing_link is not None:
        existing_version = await session.get(DataProductVersion, version_id)
        existing_product = await session.get(DataProduct, existing_link.data_product_id)
        existing_resource = await session.scalar(
            select(DataResource).where(DataResource.data_product_version_id == version_id)
        )
        if existing_version is None or existing_product is None or existing_resource is None:
            raise ExternalProductDraftError("existing external product draft graph is incomplete")
        if existing_version.linkage_metadata.get("request_digest") != request_digest:
            raise ExternalProductDraftError("external dataset already has a different draft request")
        if existing_event is None:
            raise ExternalProductDraftError("external product draft exists without its audit fact")
        return ExternalProductDraftResult(
            existing_product, existing_version, existing_resource, existing_link, existing_event
        )
    if existing_event is not None:
        raise ExternalProductDraftError("audit fact exists without its external product draft")

    product_code = f"EXT-DP-{record.id.hex[:10].upper()}"
    display_name = record.display_name_en or record.display_name_cn or record.canonical_name
    product = DataProduct(
        id=product_id,
        space_id=space_id,
        provider_organization_id=actor.organization_id,
        product_code=product_code,
        name=display_name,
        description=(
            "Metadata-only external public dataset catalog entry. The dataset files remain "
            "with the upstream holder and are not hosted, downloaded or executable in this draft."
        ),
        product_type="api",
        domain=", ".join(record.disease_areas) or "external public data",
        lifecycle_status="draft",
        is_demo=False,
        created_by=actor.user_id,
    )
    governance_snapshot = {
        "schema_version": "phase5.11.3b2/governance-snapshot/v1",
        "record_id": str(record.id),
        "record_digest": record.raw_record_digest,
        "version_id": str(version.id),
        "catalog_version": version.catalog_version,
        "profile_id": str(profile.id),
        "profile_status": profile.primary_status,
        "profile_license_status": profile.license_review_status,
        "profile_access_status": profile.access_review_status,
        "review_ids": {name: str(review.id) for name, review in latest.items()},
        "official_source_url": official_url,
        "redistribution_status": redistribution,
    }
    governance_digest = canonical_json_digest_v1(governance_snapshot)
    scope = {
        "schema_version": "phase5.11.3b2/external-metadata-scope/v1",
        "metadata_only": True,
        "modalities": record.modalities,
        "disease_areas": record.disease_areas,
        "organs": record.organs,
        "task_types": record.task_types,
        "sample_count": record.sample_count,
        "patient_count": record.patient_count,
        "file_count": record.file_count,
        "approximate_size_bytes": record.approximate_size_bytes,
        "data_formats": record.data_formats,
    }
    linkage = {
        "schema_version": "phase5.11.3b2/external-source-linkage/v1",
        "external_dataset_record_id": str(record.id),
        "external_dataset_version_id": str(version.id),
        "external_catalog_source_id": str(source.id),
        "external_id": record.external_id,
        "catalog_version": version.catalog_version,
        "source_record_digest": record.raw_record_digest,
        "governance_profile_id": str(profile.id),
        "governance_snapshot_digest": governance_digest,
        "upstream_official_url": official_url,
        "materialization_status": "metadata_only",
        "data_holder_status": "external_upstream",
        "redistribution_status": redistribution,
        "execution_readiness": "not_ready",
        "request_digest": request_digest,
        "curator_note": curator_note.strip(),
    }
    quality = {
        "schema_version": "phase5.11.3b2/external-metadata-quality/v1",
        "governance_status": profile.primary_status,
        "metadata_completeness_score": profile.metadata_completeness_score,
        "warning_reasons": profile.warning_reasons,
        "source_files_present": False,
        "materialized": False,
        "execution_ready": False,
    }
    policy = {
        "schema_version": "phase5.11.3b2/external-metadata-policy/v1",
        "allowed_purposes": ["catalog_discovery", "governance_revalidation"],
        "prohibited_purposes": [
            "raw_data_download",
            "data_hosting",
            "model_training",
            "controlled_execution",
            "redistribution_without_separate_review",
        ],
        "raw_data_downloadable": False,
        "model_weights_downloadable": False,
        "execution_allowed": False,
        "internet_allowed": False,
        "input_read_only": True,
        "hard_isolation": False,
    }
    provenance = {
        "schema_version": "phase5.11.3b2/external-metadata-provenance/v1",
        "source_type": "external_public_catalog",
        "source_statement": "The product is a curated metadata record; upstream files are not copied into MedTrust Space.",
        "upstream_official_url": official_url,
        "upstream_rights_holder": rights_holder,
        "source_record_digest": record.raw_record_digest,
        "governance_snapshot_digest": governance_digest,
        "curator_organization_id": str(actor.organization_id),
        "materialization_status": "metadata_only",
        "data_holder_status": "external_upstream",
    }
    product_version = DataProductVersion(
        id=version_id,
        space_id=space_id,
        data_product_id=product_id,
        version_no=1,
        version_label=version.catalog_version,
        status="draft",
        content_summary=(
            f"Governed metadata draft for {record.canonical_name}. "
            "No source files are present; readiness remains not_ready."
        ),
        scope_metadata=scope,
        linkage_metadata=linkage,
        quality_report=quality,
        classification_level="public_metadata",
        default_use_mode="external_metadata_catalog",
        default_policy_template=policy,
        default_policy_digest=canonical_json_digest_v1(policy),
        provenance_summary=provenance,
        snapshot_digest=canonical_json_digest_v1(
            {"schema_version": "phase5.11.3b2/data-product-version/v1", **governance_snapshot}
        ),
        created_by=actor.user_id,
    )
    resource_document = {
        "schema_version": "phase5.11.3b2/external-metadata-resource/v1",
        "record_id": str(record.id),
        "version_id": str(version.id),
        "record_digest": record.raw_record_digest,
        "governance_snapshot_digest": governance_digest,
    }
    resource = DataResource(
        id=_resource_id(record.id),
        space_id=space_id,
        data_product_version_id=version_id,
        resource_code=f"ext_{record.external_id}",
        name=f"{record.canonical_name} metadata",
        resource_type="external_metadata_catalog_record",
        modality=", ".join(record.modalities) or "metadata",
        format="json_metadata",
        schema_metadata={
            "schema_version": "phase5.11.3b2/external-metadata-schema/v1",
            "fields": [
                "canonical_name", "modalities", "disease_areas", "organs",
                "task_types", "license", "access", "official_source_url",
            ],
            "source_payload_included": False,
        },
        scope_metadata=scope,
        quality_report=quality,
        classification_level="public_metadata",
        resource_digest=canonical_json_digest_v1(resource_document),
        position_no=1,
        created_by=actor.user_id,
    )
    link = DataProductExternalSourceLink(
        id=_link_id(record.id),
        data_product_id=product_id,
        data_product_version_id=version_id,
        external_dataset_record_id=record.id,
        external_dataset_version_id=version.id,
        external_catalog_source_id=source.id,
        external_id=record.external_id,
        catalog_version=version.catalog_version,
        source_record_digest=record.raw_record_digest,
        governance_profile_id=profile.id,
        governance_snapshot_digest=governance_digest,
        source_review_id=latest["source"].id,
        license_review_id=latest["license"].id,
        access_review_id=latest["access"].id,
        productization_review_id=latest["productization"].id,
        upstream_official_url=official_url,
        upstream_rights_holder=rights_holder,
        curator_organization_id=actor.organization_id,
        materialization_status="metadata_only",
        data_holder_status="external_upstream",
        redistribution_status=redistribution,
        execution_readiness="not_ready",
        created_by=actor.user_id,
    )
    # The link intentionally has no ORM relationship to the catalog graph;
    # flush its referenced product graph first so PostgreSQL sees the FKs.
    session.add(product)
    await session.flush([product])
    session.add(product_version)
    await session.flush([product_version])
    session.add(resource)
    await session.flush([resource])
    session.add(link)
    await session.flush([link])
    appended = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type="data_product.version.created",
        subject_type="data_product_version",
        subject_id=version_id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.11.3b2/external-data-product-created/v1",
            "command_request_digest": request_digest,
            "product_code": product_code,
            "record_id": str(record.id),
            "external_dataset_version_id": str(version.id),
            "source_record_digest": record.raw_record_digest,
            "governance_snapshot_digest": governance_digest,
            "materialization_status": "metadata_only",
            "data_holder_status": "external_upstream",
            "execution_readiness": "not_ready",
            "state_before": None,
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return ExternalProductDraftResult(product, product_version, resource, link, appended.event)
