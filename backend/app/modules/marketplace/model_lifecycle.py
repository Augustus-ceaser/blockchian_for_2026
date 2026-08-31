from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor
from app.execution.registry import DIGEST_PATTERN, ModelRegistry, RegistryValidationError
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.spaces.models import Space

from .models import ModelProduct, ModelPublication, ModelVersion
from .service_modes import validate_service_modes
from .services import (
    MarketplaceServiceError,
    approve_model_version,
    publish_model_version,
    submit_model_version,
)


class ModelLifecycleError(ValueError):
    pass


FORBIDDEN_OUTPUTS = {
    "model_weights",
    "intermediate_features",
    "raw_input_images",
    "arbitrary_scripts",
    "unapproved_sample_predictions",
    "runtime_credentials",
}


def _command(
    actor: DemoActor, *, action: str, raw_key: str, subject_id: UUID
) -> AuditCommandContext:
    return AuditCommandContext(
        command_id=uuid5(
            NAMESPACE_URL, f"medtrust:phase5.2:{action}:{subject_id}:{raw_key}"
        ),
        idempotency_key=digest_idempotency_key(
            f"phase5.2:{action}:{subject_id}:{raw_key}"
        ),
        correlation_id=uuid5(
            NAMESPACE_URL, f"medtrust:phase5.2:model-product:{subject_id}"
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
            AuditEvent.subject_type == "model_version",
            AuditEvent.subject_id == subject_id,
        )
    )
    if event is not None and event.evidence_snapshot.get("request_digest") != request_digest:
        raise ModelLifecycleError("idempotency key is already bound to another request")
    return event


def _registration(document: dict[str, Any], registry: ModelRegistry):
    runtime = document["runtime"]
    try:
        entry = registry.require_enabled(runtime["model_digest"])
    except RegistryValidationError as exc:
        raise ModelLifecycleError(str(exc)) from exc
    expected = {
        "entrypoint_id": entry.entrypoint_id,
        "model_digest": entry.model_digest,
        "runtime": entry.runtime,
        "input_schema_version": entry.input_schema_version,
        "output_schema_version": entry.output_schema_version,
    }
    for field, value in expected.items():
        if runtime.get(field) != value:
            raise ModelLifecycleError(f"{field} does not match the fixed registry")
    if (
        runtime.get("network_access") is not False
        or runtime.get("input_read_only") is not True
        or runtime.get("dynamic_dependencies") is not False
        or runtime.get("arbitrary_code") is not False
        or runtime.get("model_ready") is not True
        or runtime.get("executor_type") != "local_builtin"
    ):
        raise ModelLifecycleError("unsafe or unavailable model runtime boundary")
    if (
        int(runtime.get("cpu_limit", 0)) != entry.cpu_limit
        or int(runtime.get("memory_limit_mb", 0)) != entry.memory_limit
        or int(runtime.get("timeout_seconds", 0)) != entry.timeout_seconds
    ):
        raise ModelLifecycleError("resource limits do not match the fixed registry")
    return entry


def _validate(document: dict[str, Any], registry: ModelRegistry):
    basic = document["basic"]
    policy = document["policy"]
    schema = document["schema"]
    if basic.get("is_demo") is not True or basic.get("clinical_use") is not False:
        raise ModelLifecycleError("Phase 5.2 accepts non-clinical demo models only")
    if not DIGEST_PATTERN.fullmatch(str(document["runtime"].get("model_digest"))):
        raise ModelLifecycleError("model_digest must be sha256:<64 lowercase hex>")
    entry = _registration(document, registry)
    if set(schema["allowed_outputs"]) & FORBIDDEN_OUTPUTS:
        raise ModelLifecycleError("unsafe outputs cannot be allowed")
    if (
        policy.get("model_download") is not False
        or policy.get("reverse_engineering") is not False
        or policy.get("redistribution") is not False
        or policy.get("dynamic_script_execution") is not False
        or policy.get("unauthorized_network") is not False
    ):
        raise ModelLifecycleError("unsafe model license or execution permission")
    try:
        validate_service_modes(
            "model", policy.get("service_modes", ["controlled_compute"])
        )
    except ValueError as exc:
        raise ModelLifecycleError(str(exc)) from exc
    return entry


def _documents(document: dict[str, Any], entry) -> dict[str, dict[str, Any]]:
    basic = document["basic"]
    runtime = document["runtime"]
    schema = document["schema"]
    policy = document["policy"]
    compatibility = {
        "schema_version": "phase5.2/model-compatibility/v1",
        "short_name": basic.get("short_name") or "",
        "team": basic["team"],
        "task_type": basic["task_type"],
        "task_description": basic["task_description"],
        "modality": basic["modality"],
        "version_notes": runtime["version_notes"],
        "framework": runtime["framework"],
        "device": runtime["device"],
        "input_schema": schema["input_schema"],
        "output_schema": schema["output_schema"],
        "allowed_outputs": schema["allowed_outputs"],
        "prohibited_outputs": schema["prohibited_outputs"],
        "resource_limits": {
            "cpu": entry.cpu_limit,
            "memory_mb": entry.memory_limit,
            "timeout_seconds": entry.timeout_seconds,
        },
        "executor_type": "local_builtin",
        "asset_ready": True,
        "non_clinical": True,
    }
    license_document = {
        "schema_version": "phase5.2/model-license/v1",
        "source_type": basic["source_type"],
        "owner": basic["model_owner"],
        "contact_department": basic["contact_department"],
        "allowed_purposes": policy["allowed_purposes"],
        "prohibited_purposes": policy["prohibited_purposes"],
        "multi_center_validation": policy["multi_center_validation"],
        "commercial_validation": policy["commercial_validation"],
        "research_publication": policy["research_publication"],
        "provider_result_confirmation": policy["provider_result_confirmation"],
        "model_download": False,
        "reverse_engineering": False,
        "redistribution": False,
        "non_clinical": True,
    }
    policy_document = {
        "schema_version": "phase5.2/model-policy/v1",
        "service_modes": list(
            validate_service_modes(
                "model", policy.get("service_modes", ["controlled_compute"])
            )
        ),
        "max_runs": policy["max_runs"],
        "valid_days": policy["valid_days"],
        "fixed_version": True,
        "fixed_digest": True,
        "fixed_entrypoint": True,
        "network_access": False,
        "input_read_only": True,
        "dynamic_dependencies": False,
        "dynamic_script_execution": False,
        "allowed_outputs": schema["allowed_outputs"],
        "prohibited_outputs": schema["prohibited_outputs"],
        "hard_isolation": False,
    }
    return {
        "compatibility": compatibility,
        "license": license_document,
        "policy": policy_document,
    }


async def create_model_draft(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: DemoActor,
    document: dict[str, Any],
    registry: ModelRegistry,
    raw_key: str,
) -> tuple[ModelProduct, ModelVersion, AuditEvent]:
    entry = _validate(document, registry)
    request_digest = canonical_json_digest_v1(document)
    product_id = uuid5(
        NAMESPACE_URL, f"medtrust:phase5.2:model-product:{actor.organization_id}:{raw_key}"
    )
    version_id = uuid5(NAMESPACE_URL, f"medtrust:phase5.2:model-version:{product_id}:1")
    product_code = f"MP-{product_id.hex[:8].upper()}"
    command = _command(
        actor, action="model-product-create", raw_key=raw_key, subject_id=version_id
    )
    await session.scalar(select(Space).where(Space.id == space_id).with_for_update())
    replay = await _existing_event(
        session,
        command=command,
        event_type="model_product.version.created",
        subject_id=version_id,
        request_digest=request_digest,
    )
    if replay is not None:
        product = await session.get(ModelProduct, product_id)
        version = await session.get(ModelVersion, version_id)
        if product is None or version is None:
            raise ModelLifecycleError("idempotent model draft graph is incomplete")
        return product, version, replay
    basic = document["basic"]
    runtime = document["runtime"]
    docs = _documents(document, entry)
    product = ModelProduct(
        id=product_id,
        space_id=space_id,
        provider_organization_id=actor.organization_id,
        product_code=product_code,
        name=basic["name"],
        description=basic["description"],
        domain=basic["disease_domain"],
        lifecycle_status="draft",
        is_demo=True,
        created_by=actor.user_id,
    )
    manifest = {
        "schema_version": "phase5.2/model-manifest/v1",
        "product_code": product_code,
        "version": runtime["version_label"],
        "entrypoint_id": entry.entrypoint_id,
        "model_digest": entry.model_digest,
        "registry_digest": entry.registration_digest,
    }
    version = ModelVersion(
        id=version_id,
        space_id=space_id,
        model_product_id=product_id,
        version_no=1,
        version_label=runtime["version_label"],
        status="draft",
        entrypoint_id=entry.entrypoint_id,
        model_digest=entry.model_digest,
        manifest_digest=canonical_json_digest_v1(manifest),
        registry_digest=entry.registration_digest,
        runtime=entry.runtime,
        input_schema_version=entry.input_schema_version,
        output_schema_version=entry.output_schema_version,
        compatibility_metadata=docs["compatibility"],
        license_metadata=docs["license"],
        default_policy_template=docs["policy"],
        default_policy_digest=canonical_json_digest_v1(docs["policy"]),
        created_by=actor.user_id,
    )
    session.add_all([product, version])
    await session.flush()
    appended = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type="model_product.version.created",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.2/model-created/v1",
            "request_digest": request_digest,
            "product_code": product_code,
            "registry_digest": entry.registration_digest,
            "state_before": None,
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return product, version, appended.event


async def update_model_draft(
    session: AsyncSession,
    *,
    version: ModelVersion,
    actor: DemoActor,
    document: dict[str, Any],
    registry: ModelRegistry,
    raw_key: str,
) -> tuple[ModelProduct, AuditEvent]:
    entry = _validate(document, registry)
    if version.status != "draft":
        raise ModelLifecycleError("only a draft model version can be edited")
    product = await session.get(ModelProduct, version.model_product_id)
    if product is None or product.provider_organization_id != actor.organization_id:
        raise ModelLifecycleError("only the owning model provider may edit this draft")
    request_digest = canonical_json_digest_v1(document)
    command = _command(
        actor, action="model-product-update", raw_key=raw_key, subject_id=version.id
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="model_product.version.updated",
        subject_id=version.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return product, replay
    basic = document["basic"]
    runtime = document["runtime"]
    docs = _documents(document, entry)
    product.name = basic["name"]
    product.description = basic["description"]
    product.domain = basic["disease_domain"]
    product.row_version += 1
    version.version_label = runtime["version_label"]
    version.entrypoint_id = entry.entrypoint_id
    version.model_digest = entry.model_digest
    version.registry_digest = entry.registration_digest
    version.runtime = entry.runtime
    version.input_schema_version = entry.input_schema_version
    version.output_schema_version = entry.output_schema_version
    version.compatibility_metadata = docs["compatibility"]
    version.license_metadata = docs["license"]
    version.default_policy_template = docs["policy"]
    version.default_policy_digest = canonical_json_digest_v1(docs["policy"])
    version.manifest_digest = canonical_json_digest_v1(
        {
            "schema_version": "phase5.2/model-manifest/v1",
            "product_code": product.product_code,
            "version": version.version_label,
            "entrypoint_id": entry.entrypoint_id,
            "model_digest": entry.model_digest,
            "registry_digest": entry.registration_digest,
        }
    )
    await session.flush()
    appended = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="model_product.version.updated",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.2/model-updated/v1",
            "request_digest": request_digest,
            "product_code": product.product_code,
            "state_before": "draft",
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return product, appended.event


async def submit_model_draft(
    session: AsyncSession,
    *,
    version: ModelVersion,
    actor: DemoActor,
    registry: ModelRegistry,
    raw_key: str,
) -> AuditEvent:
    product = await session.get(ModelProduct, version.model_product_id)
    if product is None or product.provider_organization_id != actor.organization_id:
        raise ModelLifecycleError("only the owning model provider may submit this version")
    request_digest = canonical_json_digest_v1(
        {
            "version_id": str(version.id),
            "manifest_digest": version.manifest_digest,
            "registry_digest": version.registry_digest,
        }
    )
    command = _command(
        actor, action="model-product-submit", raw_key=raw_key, subject_id=version.id
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="model_product.version.submitted",
        subject_id=version.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return replay
    try:
        await submit_model_version(
            session,
            version,
            registry=registry,
            provider_organization_id=actor.organization_id,
            provider_user_id=actor.user_id,
            command=command,
            evidence_facts={"request_digest": request_digest},
        )
    except MarketplaceServiceError as exc:
        raise ModelLifecycleError(str(exc)) from exc
    event = await _existing_event(
        session,
        command=command,
        event_type="model_product.version.submitted",
        subject_id=version.id,
        request_digest=request_digest,
    )
    assert event is not None
    return event


async def return_model_draft(
    session: AsyncSession,
    *,
    version: ModelVersion,
    actor: DemoActor,
    review: dict[str, Any],
    raw_key: str,
) -> AuditEvent:
    request_digest = canonical_json_digest_v1(review)
    command = _command(
        actor, action="model-product-return", raw_key=raw_key, subject_id=version.id
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="model_product.version.returned",
        subject_id=version.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return replay
    if version.status != "under_review":
        raise ModelLifecycleError("model version is not awaiting listing review")
    version.status = "draft"
    version._transition_validated = True
    await session.flush()
    appended = await append_audit_event_with_outbox(
        session,
        space_id=version.space_id,
        event_type="model_product.version.returned",
        subject_type="model_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.2/model-returned/v1",
            "request_digest": request_digest,
            **review,
            "state_before": "under_review",
            "state_after": "draft",
        },
        **command.append_kwargs(),
    )
    return appended.event


async def approve_and_publish_model(
    session: AsyncSession,
    *,
    version: ModelVersion,
    actor: DemoActor,
    registry: ModelRegistry,
    review: dict[str, Any],
    raw_key: str,
) -> tuple[ModelPublication, AuditEvent, AuditEvent]:
    if review["allow_catalog"] is not True:
        raise ModelLifecycleError(
            "approval requires catalog visibility confirmation; otherwise return the draft"
        )
    product = await session.get(ModelProduct, version.model_product_id)
    if product is None:
        raise ModelLifecycleError("model product is missing")
    request_digest = canonical_json_digest_v1(review)
    approved_command = _command(
        actor,
        action="model-product-approve",
        raw_key=f"{raw_key}:approve",
        subject_id=version.id,
    )
    published_command = _command(
        actor,
        action="model-product-publish",
        raw_key=f"{raw_key}:publish",
        subject_id=version.id,
    )
    approved_replay = await _existing_event(
        session,
        command=approved_command,
        event_type="model_product.version.approved",
        subject_id=version.id,
        request_digest=request_digest,
    )
    published_replay = await _existing_event(
        session,
        command=published_command,
        event_type="model_product.version.published",
        subject_id=version.id,
        request_digest=request_digest,
    )
    publication = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == version.id,
            ModelPublication.status == "active",
        )
    )
    if approved_replay is not None and published_replay is not None:
        if publication is None:
            raise ModelLifecycleError("published replay is missing its publication")
        return publication, approved_replay, published_replay
    if approved_replay is not None or published_replay is not None:
        raise ModelLifecycleError("approval command is only partially persisted")
    try:
        await approve_model_version(
            session,
            version,
            registry=registry,
            operator_organization_id=actor.organization_id,
            operator_user_id=actor.user_id,
            command=approved_command,
            evidence_facts={"request_digest": request_digest, **review},
        )
        publication = await publish_model_version(
            session,
            product,
            version,
            operator_organization_id=actor.organization_id,
            operator_user_id=actor.user_id,
            command=published_command,
            visibility="space",
            evidence_facts={"request_digest": request_digest},
        )
    except MarketplaceServiceError as exc:
        raise ModelLifecycleError(str(exc)) from exc
    approved = await _existing_event(
        session,
        command=approved_command,
        event_type="model_product.version.approved",
        subject_id=version.id,
        request_digest=request_digest,
    )
    published = await _existing_event(
        session,
        command=published_command,
        event_type="model_product.version.published",
        subject_id=version.id,
        request_digest=request_digest,
    )
    assert approved is not None and published is not None
    return publication, approved, published
