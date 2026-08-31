from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductSource,
    DataProductVersion,
    DataResource,
)
from app.modules.catalog.services import (
    CatalogInvariantError,
    add_product_source,
    approve_version,
    publish_version,
    return_version_to_draft,
    submit_version_for_review,
)
from app.modules.connectors.models import Connector
from app.modules.marketplace.service_modes import (
    default_service_mode,
    validate_service_modes,
)
from app.modules.spaces.models import Space


RESOURCE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
FORBIDDEN_ALLOWED_OUTPUTS = {"raw_images", "model_weights", "connector_credentials"}


class ProductLifecycleError(ValueError):
    pass


def _command(
    actor: DemoActor,
    *,
    action: str,
    raw_key: str,
    subject_id: UUID,
) -> AuditCommandContext:
    return AuditCommandContext(
        command_id=uuid5(
            NAMESPACE_URL, f"medtrust:phase5.1:{action}:{subject_id}:{raw_key}"
        ),
        idempotency_key=digest_idempotency_key(
            f"phase5.1:{action}:{subject_id}:{raw_key}"
        ),
        correlation_id=uuid5(
            NAMESPACE_URL, f"medtrust:phase5.1:data-product:{subject_id}"
        ),
        actor_type="user",
        actor_organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )


async def _existing_event(
    session: AsyncSession,
    *,
    command: AuditCommandContext,
    event_type: str,
    subject_id: UUID,
    request_digest: str,
) -> AuditEvent | None:
    event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.idempotency_key == command.idempotency_key,
            AuditEvent.event_type == event_type,
            AuditEvent.subject_type == "data_product_version",
            AuditEvent.subject_id == subject_id,
        )
    )
    if event is not None and event.evidence_snapshot.get("request_digest") != request_digest:
        raise ProductLifecycleError("idempotency key is already bound to another request")
    return event


def _validate_document(document: dict[str, Any]) -> None:
    basic = document["basic"]
    composition = document["composition"]
    policy = document["policy"]
    binding = document["binding"]
    if basic.get("is_demo") is not True:
        raise ProductLifecycleError(
            "Phase 5.1 only accepts public or synthetic demonstration metadata"
        )
    if not RESOURCE_IDENTIFIER.fullmatch(binding["resource_identifier"]):
        raise ProductLifecycleError(
            "resource_identifier must be a 3-64 character metadata identifier"
        )
    allowed_outputs = set(policy["allowed_outputs"])
    unsafe = sorted(allowed_outputs & FORBIDDEN_ALLOWED_OUTPUTS)
    if unsafe:
        raise ProductLifecycleError(
            f"unsafe outputs cannot be allowed: {', '.join(unsafe)}"
        )
    if policy.get("hard_isolation") is not False:
        raise ProductLifecycleError("hard_isolation must remain false in this prototype")
    try:
        validate_service_modes(
            "data", policy.get("service_modes", ["controlled_compute"])
        )
    except ValueError as exc:
        raise ProductLifecycleError(str(exc)) from exc
    for field in ("case_count", "slide_count", "image_count"):
        if composition[field] < 0:
            raise ProductLifecycleError(f"{field} cannot be negative")


def _documents(document: dict[str, Any], product_code: str) -> dict[str, dict[str, Any]]:
    basic = document["basic"]
    composition = document["composition"]
    policy = document["policy"]
    binding = document["binding"]
    scope = {
        "schema_version": "phase5.1/data-product-scope/v1",
        "case_count": composition["case_count"],
        "slide_count": composition["slide_count"],
        "image_count": composition["image_count"],
        "data_format": composition["data_format"],
        "image_specification": composition["image_specification"],
        "annotation_type": composition["annotation_type"],
        "annotation_coverage": composition["annotation_coverage"],
    }
    linkage = {
        "schema_version": "phase5.1/data-product-linkage/v1",
        "short_name": basic.get("short_name") or "",
        "department": basic["department"],
        "data_owner": basic["data_owner"],
        "contact_department": basic["contact_department"],
        "source_type": basic["source_type"],
        "connector_id": binding["connector_id"],
        "resource_identifier": binding["resource_identifier"],
        "data_ready": binding["data_ready"],
    }
    quality = {
        "schema_version": "phase5.1/data-product-quality/v1",
        "completeness_rate": composition["completeness_rate"],
        "quality_status": composition["quality_status"],
        "resource_summary": composition["resource_summary"],
    }
    policy_document = {
        "schema_version": "phase5.1/data-product-policy/v1",
        "service_modes": list(
            validate_service_modes(
                "data", policy.get("service_modes", ["controlled_compute"])
            )
        ),
        "allowed_purposes": policy["allowed_purposes"],
        "prohibited_purposes": policy["prohibited_purposes"],
        "max_runs": policy["max_runs"],
        "valid_days": policy["valid_days"],
        "fixed_model_version": policy["fixed_model_version"],
        "requires_egress_review": policy["requires_egress_review"],
        "internet_allowed": policy["internet_allowed"],
        "input_read_only": policy["input_read_only"],
        "allowed_outputs": policy["allowed_outputs"],
        "prohibited_outputs": policy["prohibited_outputs"],
        "raw_data_downloadable": False,
        "result_isolated_by_default": True,
        "output_allowlist_required": True,
        "hard_isolation": False,
    }
    provenance = {
        "schema_version": "phase5.1/data-product-provenance/v1",
        "source_type": basic["source_type"],
        "source_statement": (
            "Metadata-only demonstration registration; no patient data or source files "
            "are uploaded to MedTrust Space."
        ),
        "is_demo": True,
        "product_code": product_code,
    }
    return {
        "scope": scope,
        "linkage": linkage,
        "quality": quality,
        "policy": policy_document,
        "provenance": provenance,
    }


async def _connector(
    session: AsyncSession,
    *,
    connector_id: UUID,
    space_id: UUID,
    provider_organization_id: UUID,
) -> Connector:
    connector = await session.get(Connector, connector_id)
    if (
        connector is None
        or connector.space_id != space_id
        or connector.owner_organization_id != provider_organization_id
    ):
        raise ProductLifecycleError("connector is unavailable to this hospital")
    return connector


async def create_product_draft(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: DemoActor,
    document: dict[str, Any],
    raw_key: str,
) -> tuple[DataProduct, DataProductVersion, AuditEvent]:
    _validate_document(document)
    request_digest = canonical_json_digest_v1(document)
    product_id = uuid5(
        NAMESPACE_URL, f"medtrust:phase5.1:data-product:{actor.organization_id}:{raw_key}"
    )
    version_id = uuid5(NAMESPACE_URL, f"medtrust:phase5.1:data-version:{product_id}:1")
    product_code = f"DP-{product_id.hex[:8].upper()}"
    command = _command(
        actor, action="data-product-create", raw_key=raw_key, subject_id=version_id
    )
    await session.scalar(select(Space).where(Space.id == space_id).with_for_update())
    replay = await _existing_event(
        session,
        command=command,
        event_type="data_product.version.created",
        subject_id=version_id,
        request_digest=request_digest,
    )
    if replay is not None:
        product = await session.get(DataProduct, product_id)
        version = await session.get(DataProductVersion, version_id)
        if product is None or version is None:
            raise ProductLifecycleError("idempotent draft graph is incomplete")
        return product, version, replay

    connector = await _connector(
        session,
        connector_id=UUID(document["binding"]["connector_id"]),
        space_id=space_id,
        provider_organization_id=actor.organization_id,
    )
    docs = _documents(document, product_code)
    basic = document["basic"]
    composition = document["composition"]
    binding = document["binding"]
    product = DataProduct(
        id=product_id,
        space_id=space_id,
        provider_organization_id=actor.organization_id,
        product_code=product_code,
        name=basic["name"],
        description=basic["description"],
        product_type="controlled_compute",
        domain=basic["disease_domain"],
        lifecycle_status="draft",
        is_demo=True,
        created_by=actor.user_id,
    )
    version_snapshot = {
        "schema_version": "phase5.1/data-product-version/v1",
        "product_code": product_code,
        "version_label": composition["data_version"],
        "request_digest": request_digest,
    }
    version = DataProductVersion(
        id=version_id,
        space_id=space_id,
        data_product_id=product_id,
        version_no=1,
        version_label=composition["data_version"],
        status="draft",
        content_summary=composition["version_notes"],
        scope_metadata=docs["scope"],
        linkage_metadata=docs["linkage"],
        quality_report=docs["quality"],
        classification_level="public_demo",
        default_use_mode=default_service_mode("data", docs["policy"]),
        default_policy_template=docs["policy"],
        default_policy_digest=canonical_json_digest_v1(docs["policy"]),
        provenance_summary=docs["provenance"],
        snapshot_digest=canonical_json_digest_v1(version_snapshot),
        created_by=actor.user_id,
    )
    resource_document = {
        "schema_version": "phase5.1/data-resource/v1",
        "product_code": product_code,
        "resource_identifier": binding["resource_identifier"],
        "scope": docs["scope"],
        "quality": docs["quality"],
    }
    resource = DataResource(
        id=uuid5(NAMESPACE_URL, f"medtrust:phase5.1:data-resource:{version_id}:1"),
        space_id=space_id,
        data_product_version_id=version_id,
        resource_code=binding["resource_identifier"],
        name=composition["resource_summary"],
        resource_type="image_collection",
        modality=basic["modality"],
        format=composition["data_format"],
        schema_metadata={
            "schema_version": "phase5.1/data-resource-schema/v1",
            "image_specification": composition["image_specification"],
            "annotation_type": composition["annotation_type"],
        },
        scope_metadata=docs["scope"],
        quality_report=docs["quality"],
        classification_level="public_demo",
        resource_digest=canonical_json_digest_v1(resource_document),
        position_no=1,
        created_by=actor.user_id,
    )
    session.add_all([product, version, resource])
    await session.flush()
    await add_product_source(
        session,
        resource,
        connector,
        local_resource_alias=(
            f"catalog://{product_code}/{binding['resource_identifier']}"
        ),
        source_digest=canonical_json_digest_v1(
            {
                "schema_version": "phase5.1/source-binding/v1",
                "connector_id": str(connector.id),
                "resource_identifier": binding["resource_identifier"],
            }
        ),
        source_role="primary",
        source_snapshot_at=datetime.now(timezone.utc),
    )
    appended = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type="data_product.version.created",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.1/data-product-created/v1",
            "request_digest": request_digest,
            "product_code": product.product_code,
            "state_before": None,
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return product, version, appended.event


async def update_product_draft(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    actor: DemoActor,
    document: dict[str, Any],
    raw_key: str,
) -> tuple[DataProduct, AuditEvent]:
    _validate_document(document)
    if version.status != "draft":
        raise ProductLifecycleError("only a draft version can be edited")
    product = await session.get(DataProduct, version.data_product_id)
    if product is None or product.provider_organization_id != actor.organization_id:
        raise ProductLifecycleError("only the owning hospital may edit this draft")
    request_digest = canonical_json_digest_v1(document)
    command = _command(
        actor, action="data-product-update", raw_key=raw_key, subject_id=version.id
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="data_product.version.updated",
        subject_id=version.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return product, replay

    connector = await _connector(
        session,
        connector_id=UUID(document["binding"]["connector_id"]),
        space_id=version.space_id,
        provider_organization_id=actor.organization_id,
    )
    docs = _documents(document, product.product_code)
    basic = document["basic"]
    composition = document["composition"]
    binding = document["binding"]
    product.name = basic["name"]
    product.description = basic["description"]
    product.domain = basic["disease_domain"]
    product.row_version += 1
    version.version_label = composition["data_version"]
    version.content_summary = composition["version_notes"]
    version.scope_metadata = docs["scope"]
    version.linkage_metadata = docs["linkage"]
    version.quality_report = docs["quality"]
    version.default_use_mode = default_service_mode("data", docs["policy"])
    version.default_policy_template = docs["policy"]
    version.default_policy_digest = canonical_json_digest_v1(docs["policy"])
    version.provenance_summary = docs["provenance"]
    version.snapshot_digest = canonical_json_digest_v1(
        {
            "schema_version": "phase5.1/data-product-version/v1",
            "product_code": product.product_code,
            "version_label": composition["data_version"],
            "request_digest": request_digest,
        }
    )
    resource = await session.scalar(
        select(DataResource).where(DataResource.data_product_version_id == version.id)
    )
    if resource is None:
        raise ProductLifecycleError("draft data resource is missing")
    resource.resource_code = binding["resource_identifier"]
    resource.name = composition["resource_summary"]
    resource.modality = basic["modality"]
    resource.format = composition["data_format"]
    resource.schema_metadata = {
        "schema_version": "phase5.1/data-resource-schema/v1",
        "image_specification": composition["image_specification"],
        "annotation_type": composition["annotation_type"],
    }
    resource.scope_metadata = docs["scope"]
    resource.quality_report = docs["quality"]
    resource.resource_digest = canonical_json_digest_v1(
        {
            "schema_version": "phase5.1/data-resource/v1",
            "product_code": product.product_code,
            "resource_identifier": binding["resource_identifier"],
            "scope": docs["scope"],
            "quality": docs["quality"],
        }
    )
    sources = list(
        (
            await session.scalars(
                select(DataProductSource).where(
                    DataProductSource.data_resource_id == resource.id
                )
            )
        ).all()
    )
    for source in sources:
        await session.delete(source)
    await session.flush()
    await add_product_source(
        session,
        resource,
        connector,
        local_resource_alias=(
            f"catalog://{product.product_code}/{binding['resource_identifier']}"
        ),
        source_digest=canonical_json_digest_v1(
            {
                "schema_version": "phase5.1/source-binding/v1",
                "connector_id": str(connector.id),
                "resource_identifier": binding["resource_identifier"],
            }
        ),
        source_role="primary",
        source_snapshot_at=datetime.now(timezone.utc),
    )
    appended = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="data_product.version.updated",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.1/data-product-updated/v1",
            "request_digest": request_digest,
            "product_code": product.product_code,
            "state_before": "draft",
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return product, appended.event


async def submit_product_version(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    actor: DemoActor,
    raw_key: str,
) -> AuditEvent:
    product = await session.get(DataProduct, version.data_product_id)
    if product is None or product.provider_organization_id != actor.organization_id:
        raise ProductLifecycleError("only the owning hospital may submit this version")
    request_digest = canonical_json_digest_v1(
        {"version_id": str(version.id), "snapshot_digest": version.snapshot_digest}
    )
    command = _command(
        actor, action="data-product-submit", raw_key=raw_key, subject_id=version.id
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="data_product.version.submitted",
        subject_id=version.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return replay
    if version.linkage_metadata.get("data_ready") is not True:
        raise ProductLifecycleError("data readiness must be confirmed before submission")
    try:
        await submit_version_for_review(session, version)
    except CatalogInvariantError as exc:
        raise ProductLifecycleError(str(exc)) from exc
    appended = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="data_product.version.submitted",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.1/data-product-submitted/v1",
            "request_digest": request_digest,
            "product_code": product.product_code,
            "snapshot_digest": version.snapshot_digest,
            "state_before": "draft",
            "state_after": "under_review",
        },
        **command.append_kwargs(),
    )
    return appended.event


async def return_product_version(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    actor: DemoActor,
    review: dict[str, Any],
    raw_key: str,
) -> AuditEvent:
    request_digest = canonical_json_digest_v1(review)
    command = _command(
        actor, action="data-product-return", raw_key=raw_key, subject_id=version.id
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="data_product.version.returned",
        subject_id=version.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return replay
    try:
        await return_version_to_draft(session, version)
    except CatalogInvariantError as exc:
        raise ProductLifecycleError(str(exc)) from exc
    appended = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="data_product.version.returned",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.1/data-product-returned/v1",
            "request_digest": request_digest,
            "review_opinion": review["review_opinion"],
            "requested_materials": review["requested_materials"],
            "risk_level": review["risk_level"],
            "state_before": "under_review",
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return appended.event


async def approve_and_publish_product_version(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    actor: DemoActor,
    review: dict[str, Any],
    raw_key: str,
) -> tuple[DataProductPublication, AuditEvent, AuditEvent]:
    if review["allow_catalog"] is not True:
        raise ProductLifecycleError(
            "approval requires catalog visibility confirmation; otherwise return the draft"
        )
    product = await session.get(DataProduct, version.data_product_id)
    if product is None:
        raise ProductLifecycleError("data product is missing")
    request_digest = canonical_json_digest_v1(review)
    approved_command = _command(
        actor,
        action="data-product-approve",
        raw_key=f"{raw_key}:approve",
        subject_id=version.id,
    )
    published_command = _command(
        actor,
        action="data-product-publish",
        raw_key=f"{raw_key}:publish",
        subject_id=version.id,
    )
    approved_replay = await _existing_event(
        session,
        command=approved_command,
        event_type="data_product.version.approved",
        subject_id=version.id,
        request_digest=request_digest,
    )
    published_replay = await _existing_event(
        session,
        command=published_command,
        event_type="data_product.version.published",
        subject_id=version.id,
        request_digest=request_digest,
    )
    existing_publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == version.id,
            DataProductPublication.status == "active",
        )
    )
    if approved_replay is not None and published_replay is not None:
        if existing_publication is None:
            raise ProductLifecycleError("published replay is missing its publication")
        return existing_publication, approved_replay, published_replay
    if approved_replay is not None or published_replay is not None:
        raise ProductLifecycleError("approval command is only partially persisted")
    try:
        await approve_version(session, version, approved_by=actor.user_id)
        approved = await append_audit_event_with_outbox(
            session,
            space_id=version.space_id,
            event_type="data_product.version.approved",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.1/data-product-approved/v1",
                "request_digest": request_digest,
                "review_opinion": review["review_opinion"],
                "additional_conditions": review["additional_conditions"],
                "risk_level": review["risk_level"],
                "state_before": "under_review",
                "state_after": "approved",
            },
            **approved_command.append_kwargs(),
        )
        publication = await publish_version(
            session,
            product,
            version,
            published_by=actor.user_id,
            visibility="space",
        )
        published_kwargs = published_command.append_kwargs()
        published_kwargs["causation_id"] = approved.event.event_id
        published = await append_audit_event_with_outbox(
            session,
            space_id=version.space_id,
            event_type="data_product.version.published",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.1/data-product-published/v1",
                "request_digest": request_digest,
                "publication_id": str(publication.id),
                "visibility": publication.visibility,
                "state_before": "approved",
                "state_after": "published",
            },
            **published_kwargs,
        )
    except CatalogInvariantError as exc:
        raise ProductLifecycleError(str(exc)) from exc
    return publication, approved.event, published.event
