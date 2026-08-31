from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor
from app.modules.applications.models import (
    Application,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
    ApplicationSnapshot,
)
from app.modules.applications.services import submit_application
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
from app.modules.external_catalog.eligibility import (
    ExternalDataProductEligibilityError,
    ExternalModelProductEligibilityError,
    require_materialized_data_product,
    require_materialized_model_product,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.service_modes import (
    CONTROLLED_COMPUTE,
    service_mode_enabled,
)
from app.modules.marketplace.services import require_actor
from app.modules.reviews.models import ReviewDecision, ReviewTask
from app.modules.reviews.services import (
    cancel_review_task,
    claim_review_task,
    submit_review_decision,
)
from app.modules.spaces.models import Space


class ApplicationLifecycleError(ValueError):
    pass


REQUEST_SCHEMA = "phase5.3/application-request/v1"
COMPATIBILITY_SCHEMA = "phase5.3/compatibility/v1"
RULESET_VERSION = "phase5.3/compatibility-rules/v1"
CLIENT_SELECTION_RECEIPT_SCHEMA = "phase5.14/client-selection-receipt/v1"
ALLOWED_OUTPUTS = {
    "aggregate_metrics",
    "confusion_matrix",
    "execution_summary",
}
FORBIDDEN_OUTPUTS = {
    "raw_images",
    "patient_level_predictions",
    "raw_features",
    "model_weights",
    "source_code",
    "connector_credentials",
    "arbitrary_files",
}
PROJECT_ACTIONS = {
    "model_external_validation": "model_validation",
    "research_analysis": "research_analysis",
    "multicenter_validation": "research_analysis",
    "teaching_demo": "model_validation",
    "algorithm_performance_evaluation": "model_validation",
    "other": "research_analysis",
}
REVIEW_ROLE = {
    "application_precheck": "space_operator",
    "data_provider_review": "data_provider",
    "model_provider_review": "model_provider",
}


def _command(
    actor: DemoActor,
    *,
    action: str,
    raw_key: str,
    subject_id: UUID,
) -> AuditCommandContext:
    return AuditCommandContext(
        command_id=uuid5(
            NAMESPACE_URL, f"medtrust:phase5.3:{action}:{subject_id}:{raw_key}"
        ),
        idempotency_key=digest_idempotency_key(
            f"phase5.3:{action}:{subject_id}:{raw_key}"
        ),
        correlation_id=uuid5(
            NAMESPACE_URL, f"medtrust:phase5.3:application:{subject_id}"
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
    subject_type: str,
    subject_id: UUID | None = None,
    request_digest: str | None = None,
) -> AuditEvent | None:
    query = select(AuditEvent).where(
        AuditEvent.idempotency_key == command.idempotency_key,
        AuditEvent.event_type == event_type,
        AuditEvent.subject_type == subject_type,
    )
    if subject_id is not None:
        query = query.where(AuditEvent.subject_id == subject_id)
    event = await session.scalar(query)
    if (
        event is not None
        and request_digest is not None
        and event.evidence_snapshot.get("request_digest") != request_digest
    ):
        raise ApplicationLifecycleError(
            "idempotency key is already bound to another request"
        )
    return event


async def _append(
    session: AsyncSession,
    *,
    command: AuditCommandContext,
    space_id: UUID,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    evidence: dict[str, Any],
) -> AuditEvent:
    result = await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        result="success",
        evidence_snapshot=evidence,
        **command.append_kwargs(),
    )
    return result.event


def _request_digest(document: dict[str, Any]) -> str:
    return canonical_json_digest_v1(document)


def _validate_document(document: dict[str, Any]) -> None:
    if document.get("schema_version") != REQUEST_SCHEMA:
        raise ApplicationLifecycleError("unsupported application request schema")
    profile = document["profile"]
    execution = document["execution"]
    declarations = document["declarations"]
    outputs = set(execution["requested_outputs"])
    if profile["project_type"] not in PROJECT_ACTIONS:
        raise ApplicationLifecycleError("unsupported project type")
    if not outputs or outputs - ALLOWED_OUTPUTS:
        raise ApplicationLifecycleError(
            "requested outputs must use the approved aggregate allowlist"
        )
    if outputs & FORBIDDEN_OUTPUTS:
        raise ApplicationLifecycleError("sensitive outputs cannot be requested")
    if execution["run_count"] < 1 or execution["valid_days"] < 1:
        raise ApplicationLifecycleError("run count and validity must be positive")
    if execution["internet_required"]:
        raise ApplicationLifecycleError(
            "the current controlled-compute prototype does not allow internet access"
        )
    if not execution["fixed_data_version"] or not execution["fixed_model_version"]:
        raise ApplicationLifecycleError(
            "data and model versions must remain fixed"
        )
    required_declarations = {
        "no_raw_data_download",
        "no_model_weight_download",
        "approved_purpose_only",
        "accept_multiparty_review",
        "accept_result_isolation",
        "accept_full_audit",
    }
    if any(declarations.get(name) is not True for name in required_declarations):
        raise ApplicationLifecycleError("all required declarations must be accepted")
    if profile["clinical_diagnosis"]:
        raise ApplicationLifecycleError(
            "demonstration products cannot be requested for clinical diagnosis"
        )


async def _selection_graph(
    session: AsyncSession,
    *,
    space_id: UUID,
    data_version_id: UUID,
    model_version_id: UUID,
) -> tuple[
    DataProduct,
    DataProductVersion,
    DataProductPublication,
    ModelProduct,
    ModelVersion,
    ModelPublication,
    DataResource | None,
    Connector | None,
]:
    try:
        await require_materialized_data_product(session, data_version_id)
    except ExternalDataProductEligibilityError as exc:
        raise ApplicationLifecycleError(str(exc)) from exc
    try:
        await require_materialized_model_product(session, model_version_id)
    except ExternalModelProductEligibilityError as exc:
        raise ApplicationLifecycleError(str(exc)) from exc
    data_version = await session.get(DataProductVersion, data_version_id)
    data_product = (
        None
        if data_version is None
        else await session.get(DataProduct, data_version.data_product_id)
    )
    data_publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == data_version_id,
            DataProductPublication.status == "active",
        )
    )
    model_version = await session.get(ModelVersion, model_version_id)
    model_product = (
        None
        if model_version is None
        else await session.get(ModelProduct, model_version.model_product_id)
    )
    model_publication = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == model_version_id,
            ModelPublication.status == "active",
        )
    )
    if (
        data_product is None
        or data_version is None
        or data_publication is None
        or data_version.status != "approved"
        or data_product.space_id != space_id
        or model_product is None
        or model_version is None
        or model_publication is None
        or model_version.status != "approved"
        or model_product.lifecycle_status != "active"
        or model_product.space_id != space_id
    ):
        raise ApplicationLifecycleError(
            "only active published data and model versions can be selected"
        )
    resource = await session.scalar(
        select(DataResource).where(
            DataResource.data_product_version_id == data_version.id
        )
    )
    source = (
        None
        if resource is None
        else await session.scalar(
            select(DataProductSource).where(
                DataProductSource.data_resource_id == resource.id
            )
        )
    )
    connector = (
        None if source is None else await session.get(Connector, source.connector_id)
    )
    return (
        data_product,
        data_version,
        data_publication,
        model_product,
        model_version,
        model_publication,
        resource,
        connector,
    )


def _base_parameters(document: dict[str, Any]) -> dict[str, Any]:
    parameters = {
        "schema_version": REQUEST_SCHEMA,
        "request": deepcopy(document),
        "compatibility": None,
    }
    client_snapshot = document.get("recommendation_context")
    if isinstance(client_snapshot, dict):
        parameters["client_selection_snapshot_receipt"] = {
            "schema_version": CLIENT_SELECTION_RECEIPT_SCHEMA,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_digest": canonical_json_digest_v1(client_snapshot),
            "verification_status": "not_platform_verified",
            "authority": "receipt_only",
            "eligibility_authority": "server_compatibility_report",
        }
    return parameters


def _require_controlled_compute_offerings(
    data_version: DataProductVersion,
    model_version: ModelVersion,
) -> None:
    if not service_mode_enabled(
        "data", data_version.default_policy_template, CONTROLLED_COMPUTE
    ):
        raise ApplicationLifecycleError(
            "selected data product version does not offer controlled compute"
        )
    if not service_mode_enabled(
        "model", model_version.default_policy_template, CONTROLLED_COMPUTE
    ):
        raise ApplicationLifecycleError(
            "selected model product version does not offer controlled compute"
        )


async def create_application_draft(
    session: AsyncSession,
    *,
    space_id: UUID,
    actor: DemoActor,
    document: dict[str, Any],
    raw_key: str,
) -> tuple[Application, AuditEvent]:
    _validate_document(document)
    await require_actor(
        session,
        space_id=space_id,
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        role_code="data_requester",
    )
    request_digest = _request_digest(document)
    application_id = uuid5(
        NAMESPACE_URL,
        f"medtrust:phase5.3:application:{actor.organization_id}:{raw_key}",
    )
    command = _command(
        actor,
        action="application-create",
        raw_key=raw_key,
        subject_id=application_id,
    )
    await session.scalar(select(Space).where(Space.id == space_id).with_for_update())
    replay = await _existing_event(
        session,
        command=command,
        event_type="application.created",
        subject_type="application",
        subject_id=application_id,
        request_digest=request_digest,
    )
    if replay is not None:
        application = await session.get(Application, application_id)
        if application is None:
            raise ApplicationLifecycleError("idempotent application graph is incomplete")
        return application, replay

    (
        data_product,
        data_version,
        _,
        model_product,
        model_version,
        _,
        _,
        _,
    ) = await _selection_graph(
        session,
        space_id=space_id,
        data_version_id=UUID(document["data_version_id"]),
        model_version_id=UUID(document["model_version_id"]),
    )
    _require_controlled_compute_offerings(data_version, model_version)
    profile = document["profile"]
    execution = document["execution"]
    application = Application(
        id=application_id,
        space_id=space_id,
        application_number=f"APP-{application_id.hex[:8].upper()}",
        applicant_organization_id=actor.organization_id,
        applicant_user_id=actor.user_id,
        provider_organization_id=data_product.provider_organization_id,
        purpose=profile["research_purpose"],
        legal_or_ethics_basis=profile["ethics_or_approval_statement"],
        algorithm_name=model_product.name,
        algorithm_version=model_version.version_label,
        algorithm_digest=model_version.model_digest,
        requested_duration_seconds=execution["valid_days"] * 86400,
        requested_run_limit=execution["run_count"],
        is_demo=profile["is_demo"],
        created_by=actor.user_id,
    )
    session.add(application)
    await session.flush()
    session.add_all(
        [
            ApplicationItem(
                application_id=application.id,
                space_id=space_id,
                provider_organization_id=data_product.provider_organization_id,
                data_product_id=data_product.id,
                data_product_version_id=data_version.id,
                position_no=1,
                requested_product_snapshot_digest=data_version.snapshot_digest,
                requested_policy_digest=data_version.default_policy_digest,
                requested_scope=deepcopy(document["data_scope"]),
            ),
            ApplicationRequestedAction(
                application_id=application.id,
                action_code=PROJECT_ACTIONS[profile["project_type"]],
                parameters=_base_parameters(document),
            ),
            ApplicationRequestedOutputType(
                application_id=application.id,
                output_type="aggregate_statistics",
                requires_manual_review=False,
            ),
            ApplicationModelSelection(
                application_id=application.id,
                space_id=space_id,
                model_provider_organization_id=model_product.provider_organization_id,
                model_product_id=model_product.id,
                model_version_id=model_version.id,
                model_snapshot_digest=model_version.snapshot_digest,
                requested_model_policy_digest=model_version.default_policy_digest,
                registry_digest=model_version.registry_digest,
            ),
        ]
    )
    await session.flush()
    event = await _append(
        session,
        command=command,
        space_id=space_id,
        event_type="application.created",
        subject_type="application",
        subject_id=application.id,
        evidence={
            "schema_version": "phase5.3/application-created/v1",
            "request_digest": request_digest,
            "application_id": str(application.id),
            "application_number": application.application_number,
            "data_product_version_id": str(data_version.id),
            "model_version_id": str(model_version.id),
            "state_after": "draft",
        },
    )
    return application, event


async def _draft_components(
    session: AsyncSession, application_id: UUID
) -> tuple[
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationModelSelection,
]:
    item = await session.scalar(
        select(ApplicationItem).where(ApplicationItem.application_id == application_id)
    )
    action = await session.scalar(
        select(ApplicationRequestedAction).where(
            ApplicationRequestedAction.application_id == application_id
        )
    )
    selection = await session.scalar(
        select(ApplicationModelSelection).where(
            ApplicationModelSelection.application_id == application_id
        )
    )
    if item is None or action is None or selection is None:
        raise ApplicationLifecycleError("application draft graph is incomplete")
    return item, action, selection


async def update_application_draft(
    session: AsyncSession,
    application: Application,
    *,
    actor: DemoActor,
    document: dict[str, Any],
    expected_row_version: int,
    raw_key: str,
) -> tuple[Application, AuditEvent]:
    _validate_document(document)
    if application.applicant_organization_id != actor.organization_id:
        raise ApplicationLifecycleError(
            "only the requester organization may edit its draft"
        )
    request_digest = _request_digest(document)
    command = _command(
        actor,
        action="application-update",
        raw_key=raw_key,
        subject_id=application.id,
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="application.updated",
        subject_type="application",
        subject_id=application.id,
        request_digest=request_digest,
    )
    if replay is not None:
        return application, replay
    if application.status != "draft":
        raise ApplicationLifecycleError(
            "only the requester organization may edit its draft"
        )
    if application.row_version != expected_row_version:
        raise ApplicationLifecycleError(
            "application draft changed; refresh before saving again"
        )

    (
        data_product,
        data_version,
        _,
        model_product,
        model_version,
        _,
        _,
        _,
    ) = await _selection_graph(
        session,
        space_id=application.space_id,
        data_version_id=UUID(document["data_version_id"]),
        model_version_id=UUID(document["model_version_id"]),
    )
    _require_controlled_compute_offerings(data_version, model_version)
    item, action, selection = await _draft_components(session, application.id)
    profile = document["profile"]
    execution = document["execution"]
    application.provider_organization_id = data_product.provider_organization_id
    application.purpose = profile["research_purpose"]
    application.legal_or_ethics_basis = profile["ethics_or_approval_statement"]
    application.algorithm_name = model_product.name
    application.algorithm_version = model_version.version_label
    application.algorithm_digest = model_version.model_digest
    application.requested_duration_seconds = execution["valid_days"] * 86400
    application.requested_run_limit = execution["run_count"]
    application.is_demo = profile["is_demo"]
    application.row_version += 1

    item.provider_organization_id = data_product.provider_organization_id
    item.data_product_id = data_product.id
    item.data_product_version_id = data_version.id
    item.requested_product_snapshot_digest = data_version.snapshot_digest
    item.requested_policy_digest = data_version.default_policy_digest
    item.requested_scope = deepcopy(document["data_scope"])

    desired_action = PROJECT_ACTIONS[profile["project_type"]]
    if action.action_code != desired_action:
        await session.delete(action)
        action = ApplicationRequestedAction(
            application_id=application.id,
            action_code=desired_action,
            parameters=_base_parameters(document),
        )
        session.add(action)
    else:
        action.parameters = _base_parameters(document)

    selection.model_provider_organization_id = (
        model_product.provider_organization_id
    )
    selection.model_product_id = model_product.id
    selection.model_version_id = model_version.id
    selection.model_snapshot_digest = model_version.snapshot_digest
    selection.requested_model_policy_digest = model_version.default_policy_digest
    selection.registry_digest = model_version.registry_digest
    await session.flush()
    event = await _append(
        session,
        command=command,
        space_id=application.space_id,
        event_type="application.updated",
        subject_type="application",
        subject_id=application.id,
        evidence={
            "schema_version": "phase5.3/application-updated/v1",
            "request_digest": request_digest,
            "application_id": str(application.id),
            "row_version": application.row_version,
            "data_product_version_id": str(data_version.id),
            "model_version_id": str(model_version.id),
            "compatibility_invalidated": True,
            "state_before": "draft",
            "state_after": "draft",
        },
    )
    return application, event


def _canonical_purposes(values: list[Any]) -> set[str]:
    result: set[str] = set()
    for value in values:
        text = str(value).strip().lower()
        if text in {
            "research_analysis",
            "model_validation",
            "external_performance_validation",
            "teaching_demo",
            "commercial_validation",
        }:
            result.add(text)
        if "research" in text or "科研" in text:
            result.add("research_analysis")
        if (
            "external" in text
            or "performance" in text
            or "validation" in text
            or "验证" in text
        ):
            result.update({"model_validation", "external_performance_validation"})
        if "teaching" in text or "教学" in text:
            result.add("teaching_demo")
        if "commercial" in text or "商业" in text:
            result.add("commercial_validation")
    return result


def _dimensions(value: Any) -> tuple[int | None, int | None, int | None]:
    if isinstance(value, dict):
        width = value.get("width")
        height = value.get("height")
        channels = value.get("channels")
        return (
            int(width) if isinstance(width, int) else None,
            int(height) if isinstance(height, int) else None,
            int(channels) if isinstance(channels, int) else None,
        )
    numbers = [int(item) for item in re.findall(r"\d+", str(value))]
    if len(numbers) >= 3:
        return numbers[0], numbers[1], numbers[2]
    if len(numbers) >= 2:
        return numbers[0], numbers[1], None
    return None, None, None


def registered_data_modality(
    product: DataProduct,
    version: DataProductVersion,
    resource: DataResource | None,
) -> str | None:
    modality = (
        version.linkage_metadata.get("modality")
        or (resource.modality if resource is not None else None)
    )
    if modality:
        return str(modality)
    identity = " ".join(
        str(value)
        for value in (
            product.product_code,
            product.name,
            version.linkage_metadata.get("short_name"),
            version.linkage_metadata.get("resource_identifier"),
        )
        if value
    ).lower()
    image_specification = str(
        version.scope_metadata.get("image_specification") or ""
    ).lower()
    if (
        product.is_demo
        and "pathmnist" in identity
        and "28" in image_specification
        and "rgb" in image_specification
    ):
        return "digital_pathology"
    return None


def canonical_modality(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if "pathology" in text or "病理" in text:
        return "digital_pathology"
    return text


def _check(
    code: str,
    name: str,
    result: str,
    *,
    data_requirement: Any = None,
    model_requirement: Any = None,
    request_value: Any = None,
    explanation: str,
    remediation: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "result": result,
        "data_requirement": data_requirement,
        "model_requirement": model_requirement,
        "request_value": request_value,
        "explanation": explanation,
        "remediation": remediation,
    }


async def run_compatibility_check(
    session: AsyncSession,
    application: Application,
    *,
    actor: DemoActor,
    raw_key: str,
) -> tuple[dict[str, Any], AuditEvent]:
    if (
        application.status != "draft"
        or application.applicant_organization_id != actor.organization_id
    ):
        raise ApplicationLifecycleError(
            "only the requester organization may check its draft"
        )
    item, action, selection = await _draft_components(session, application.id)
    request = deepcopy(action.parameters["request"])
    (
        data_product,
        data_version,
        data_publication,
        model_product,
        model_version,
        model_publication,
        resource,
        connector,
    ) = await _selection_graph(
        session,
        space_id=application.space_id,
        data_version_id=item.data_product_version_id,
        model_version_id=selection.model_version_id,
    )
    data_policy = data_version.default_policy_template
    model_policy = model_version.default_policy_template
    compatibility = model_version.compatibility_metadata
    license_metadata = model_version.license_metadata
    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "data_publication",
            "数据版本有效发布",
            "PASS",
            data_requirement="active publication",
            request_value=str(data_publication.id),
            explanation="所选数据版本仍处于有效发布状态。",
        )
    )
    checks.append(
        _check(
            "model_publication",
            "模型版本有效发布",
            "PASS",
            model_requirement="active publication",
            request_value=str(model_publication.id),
            explanation="所选模型版本仍处于有效发布状态。",
        )
    )
    data_controlled_compute = service_mode_enabled(
        "data", data_policy, CONTROLLED_COMPUTE
    )
    model_controlled_compute = service_mode_enabled(
        "model", model_policy, CONTROLLED_COMPUTE
    )
    controlled_compute_result = (
        "PASS" if data_controlled_compute and model_controlled_compute else "BLOCKER"
    )
    checks.append(
        _check(
            "controlled_compute_offering",
            "受控计算服务方式",
            controlled_compute_result,
            data_requirement=CONTROLLED_COMPUTE,
            model_requirement=CONTROLLED_COMPUTE,
            request_value={
                "data_offers_controlled_compute": data_controlled_compute,
                "model_offers_controlled_compute": model_controlled_compute,
            },
            explanation=(
                "数据与模型版本均明确支持受控调用计算。"
                if controlled_compute_result == "PASS"
                else "所选数据或模型版本未开放受控调用计算。"
            ),
            remediation="重新选择同时开放受控调用计算的数据与模型版本。",
        )
    )

    data_modality = registered_data_modality(
        data_product, data_version, resource
    )
    model_modality = (
        compatibility.get("input_schema", {}).get("modality")
        if isinstance(compatibility.get("input_schema"), dict)
        else None
    ) or compatibility.get("modality")
    normalized_data_modality = canonical_modality(data_modality)
    normalized_model_modality = canonical_modality(model_modality)
    modality_result = (
        "PASS"
        if data_modality
        and model_modality
        and normalized_data_modality == normalized_model_modality
        else "BLOCKER"
    )
    checks.append(
        _check(
            "modality",
            "数据模态",
            modality_result,
            data_requirement=data_modality,
            model_requirement=model_modality,
            explanation=(
                "数据模态与模型输入声明一致。"
                if modality_result == "PASS"
                else "数据模态与模型输入声明不一致或缺失。"
            ),
            remediation="重新选择模态兼容的已发布数据或模型。",
        )
    )

    data_shape = (
        data_version.scope_metadata.get("image_specification")
        or (resource.schema_metadata if resource is not None else {})
    )
    model_input = compatibility.get("input_schema", {})
    data_width, data_height, data_channels = _dimensions(data_shape)
    model_width, model_height, model_channels = _dimensions(model_input)
    dimensions_known = all(
        value is not None
        for value in (data_width, data_height, model_width, model_height)
    )
    dimension_result = (
        "PASS"
        if dimensions_known
        and data_width == model_width
        and data_height == model_height
        and (
            data_channels is None
            or model_channels is None
            or data_channels == model_channels
        )
        else "WARNING"
        if not dimensions_known
        else "BLOCKER"
    )
    checks.append(
        _check(
            "input_dimensions",
            "输入尺寸与通道",
            dimension_result,
            data_requirement=data_shape,
            model_requirement=model_input,
            explanation=(
                "登记的图像尺寸和通道与模型输入一致。"
                if dimension_result == "PASS"
                else "元数据不足，需在合同或执行前再次确认。"
                if dimension_result == "WARNING"
                else "登记的图像尺寸或通道与模型输入不一致。"
            ),
            remediation="补充准确 Schema 或选择匹配版本。",
        )
    )

    data_dtype = (
        resource.schema_metadata.get("dtype") if resource is not None else None
    )
    model_dtype = (
        model_input.get("dtype") if isinstance(model_input, dict) else None
    )
    dtype_result = (
        "PASS"
        if data_dtype and model_dtype and str(data_dtype) == str(model_dtype)
        else "WARNING"
        if not data_dtype or not model_dtype
        else "BLOCKER"
    )
    checks.append(
        _check(
            "dtype",
            "数据类型",
            dtype_result,
            data_requirement=data_dtype,
            model_requirement=model_dtype,
            explanation=(
                "数据类型一致。"
                if dtype_result == "PASS"
                else "数据类型元数据不完整。"
                if dtype_result == "WARNING"
                else "数据类型不兼容。"
            ),
            remediation="补充 dtype 元数据或选择兼容版本。",
        )
    )

    requested_purpose = request["profile"]["purpose_code"]
    data_purposes = _canonical_purposes(
        data_policy.get("allowed_purposes", [])
        + data_policy.get("allowed_actions", [])
    )
    model_purposes = _canonical_purposes(
        model_policy.get("allowed_purposes", [])
        + model_policy.get("allowed_actions", [])
        + license_metadata.get("allowed_purposes", [])
    )
    purpose_result = (
        "PASS"
        if requested_purpose in data_purposes
        and requested_purpose in model_purposes
        else "BLOCKER"
    )
    checks.append(
        _check(
            "purpose",
            "允许用途交集",
            purpose_result,
            data_requirement=sorted(data_purposes),
            model_requirement=sorted(model_purposes),
            request_value=requested_purpose,
            explanation=(
                "申请用途同时位于数据和模型的允许范围。"
                if purpose_result == "PASS"
                else "申请用途不在数据与模型许可的共同范围内。"
            ),
            remediation="调整用途或重新选择产品版本。",
        )
    )

    requested_outputs = set(request["execution"]["requested_outputs"])
    data_outputs = set(data_policy.get("allowed_outputs", []))
    model_outputs = set(
        model_policy.get("allowed_outputs", [])
        or compatibility.get("allowed_outputs", [])
    )
    output_result = (
        "PASS"
        if requested_outputs <= ALLOWED_OUTPUTS
        and requested_outputs <= data_outputs
        and requested_outputs <= model_outputs
        else "BLOCKER"
    )
    checks.append(
        _check(
            "outputs",
            "输出白名单",
            output_result,
            data_requirement=sorted(data_outputs),
            model_requirement=sorted(model_outputs),
            request_value=sorted(requested_outputs),
            explanation=(
                "所有请求输出同时满足平台、数据和模型白名单。"
                if output_result == "PASS"
                else "至少一个请求输出超出数据或模型白名单。"
            ),
            remediation="仅保留 aggregate_metrics、confusion_matrix、execution_summary 中共同允许的项目。",
        )
    )

    run_limit = request["execution"]["run_count"]
    max_runs = min(
        int(data_policy.get("max_runs", run_limit)),
        int(model_policy.get("max_runs", run_limit)),
    )
    checks.append(
        _check(
            "run_limit",
            "最大运行次数",
            "PASS" if run_limit <= max_runs else "BLOCKER",
            data_requirement=data_policy.get("max_runs"),
            model_requirement=model_policy.get("max_runs"),
            request_value=run_limit,
            explanation=(
                "请求次数未超过双方限制。"
                if run_limit <= max_runs
                else "请求次数超过数据或模型限制。"
            ),
            remediation=f"将运行次数调整为 {max_runs} 次以内。",
        )
    )

    valid_days = request["execution"]["valid_days"]
    max_days = min(
        int(data_policy.get("valid_days", valid_days)),
        int(model_policy.get("valid_days", valid_days)),
    )
    checks.append(
        _check(
            "validity",
            "申请有效期",
            "PASS" if valid_days <= max_days else "BLOCKER",
            data_requirement=data_policy.get("valid_days"),
            model_requirement=model_policy.get("valid_days"),
            request_value=valid_days,
            explanation=(
                "有效期未超过双方策略。"
                if valid_days <= max_days
                else "有效期超过数据或模型策略。"
            ),
            remediation=f"将有效期调整为 {max_days} 天以内。",
        )
    )

    connector_ready = False
    if connector is not None:
            connector_ready = bool(
                await session.scalar(
                    select(ConnectorCapability.capability_code).where(
                        ConnectorCapability.connector_id == connector.id,
                    ConnectorCapability.capability_code
                    == "controlled_compute_execution",
                    ConnectorCapability.status == "verified",
                )
            )
        )
    checks.append(
        _check(
            "connector_capability",
            "数据节点执行能力",
            "PASS" if connector_ready else "BLOCKER",
            data_requirement="verified controlled_compute_execution",
            request_value=connector.runtime_status if connector else None,
            explanation=(
                "数据节点已登记必要的受控执行能力。"
                if connector_ready
                else "数据节点缺少已验证的受控执行能力。"
            ),
            remediation="由医院或运营方补充节点能力后重新检查。",
        )
    )

    model_ready = compatibility.get("asset_ready", True) and (
        compatibility.get("executor_type", "local_builtin") == "local_builtin"
    )
    checks.append(
        _check(
            "model_executor",
            "模型执行器能力",
            "PASS" if model_ready else "BLOCKER",
            model_requirement={
                "asset_ready": compatibility.get("asset_ready"),
                "executor_type": compatibility.get("executor_type"),
                "runtime": model_version.runtime,
            },
            explanation=(
                "模型绑定固定白名单执行器。"
                if model_ready
                else "模型执行器未登记为可用固定资产。"
            ),
            remediation="选择已完成固定资产登记的模型版本。",
        )
    )

    clinical = request["profile"]["clinical_diagnosis"]
    demo_or_nonclinical = (
        data_product.is_demo
        or model_product.is_demo
        or bool(compatibility.get("non_clinical"))
        or bool(license_metadata.get("non_clinical"))
    )
    checks.append(
        _check(
            "clinical_boundary",
            "临床用途边界",
            "BLOCKER" if clinical and demo_or_nonclinical else "PASS",
            data_requirement={"is_demo": data_product.is_demo},
            model_requirement={
                "is_demo": model_product.is_demo,
                "non_clinical": compatibility.get("non_clinical", True),
            },
            request_value=clinical,
            explanation=(
                "当前申请明确为非临床用途。"
                if not clinical
                else "演示或非临床产品不能用于临床诊断。"
            ),
            remediation="取消临床诊断用途。",
        )
    )
    checks.append(
        _check(
            "hard_isolation",
            "生产级硬隔离",
            "WARNING",
            data_requirement=data_policy.get("hard_isolation", False),
            model_requirement=model_policy.get("hard_isolation", False),
            request_value=False,
            explanation="当前为单机工程原型，hard_isolation=false。",
            remediation="不得将本检查解释为生产级安全认证。",
        )
    )
    checks.append(
        _check(
            "result_reviews",
            "结果出域与技术确认",
            "PASS",
            data_requirement=data_policy.get("requires_egress_review", True),
            model_requirement=license_metadata.get(
                "provider_result_confirmation", True
            ),
            request_value=request["review_requirements"],
            explanation="申请保留医院结果出域审核和模型方技术确认要求。",
        )
    )

    blockers = [item for item in checks if item["result"] == "BLOCKER"]
    warnings = [item for item in checks if item["result"] == "WARNING"]
    overall = "BLOCKER" if blockers else "WARNING" if warnings else "PASS"
    input_document = {
        "schema_version": COMPATIBILITY_SCHEMA,
        "request": request,
        "data_version_id": str(data_version.id),
        "data_snapshot_digest": data_version.snapshot_digest,
        "data_policy_digest": data_version.default_policy_digest,
        "model_version_id": str(model_version.id),
        "model_snapshot_digest": model_version.snapshot_digest,
        "model_policy_digest": model_version.default_policy_digest,
        "registry_digest": model_version.registry_digest,
    }
    report = {
        "schema_version": COMPATIBILITY_SCHEMA,
        "ruleset_version": RULESET_VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "input_digest": canonical_json_digest_v1(input_document),
        "overall": overall,
        "counts": {
            "pass": sum(item["result"] == "PASS" for item in checks),
            "warning": len(warnings),
            "blocker": len(blockers),
        },
        "blockers": [item["code"] for item in blockers],
        "warnings": [item["code"] for item in warnings],
        "checks": checks,
        "data_version_id": str(data_version.id),
        "model_version_id": str(model_version.id),
        "disclaimer": (
            "当前检查基于已登记的产品元数据、Schema 和策略，仅用于申请前规则校验，"
            "不代表临床有效性或生产级安全认证。"
        ),
    }
    request_digest = canonical_json_digest_v1(input_document)
    command = _command(
        actor,
        action="compatibility-check",
        raw_key=raw_key,
        subject_id=application.id,
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="application.compatibility.checked",
        subject_type="application",
        subject_id=application.id,
        request_digest=request_digest,
    )
    if replay is not None:
        stored = action.parameters.get("compatibility")
        if not isinstance(stored, dict):
            raise ApplicationLifecycleError(
                "idempotent compatibility result is missing"
            )
        return stored, replay
    action.parameters = {**action.parameters, "compatibility": report}
    await session.flush()
    event = await _append(
        session,
        command=command,
        space_id=application.space_id,
        event_type="application.compatibility.checked",
        subject_type="application",
        subject_id=application.id,
        evidence={
            "schema_version": "phase5.3/compatibility-checked/v1",
            "request_digest": request_digest,
            "application_id": str(application.id),
            "input_digest": report["input_digest"],
            "ruleset_version": RULESET_VERSION,
            "overall": overall,
            "blockers": report["blockers"],
            "warnings": report["warnings"],
            "state_before": "draft",
            "state_after": "draft",
        },
    )
    return report, event


async def _current_compatibility_input_digest(
    session: AsyncSession,
    application: Application,
    *,
    request: dict[str, Any],
    item: ApplicationItem,
    selection: ApplicationModelSelection,
) -> str:
    data_version = await session.get(DataProductVersion, item.data_product_version_id)
    model_version = await session.get(ModelVersion, selection.model_version_id)
    if data_version is None or model_version is None:
        raise ApplicationLifecycleError("selected product version is unavailable")
    return canonical_json_digest_v1(
        {
            "schema_version": COMPATIBILITY_SCHEMA,
            "request": request,
            "data_version_id": str(data_version.id),
            "data_snapshot_digest": data_version.snapshot_digest,
            "data_policy_digest": data_version.default_policy_digest,
            "model_version_id": str(model_version.id),
            "model_snapshot_digest": model_version.snapshot_digest,
            "model_policy_digest": model_version.default_policy_digest,
            "registry_digest": model_version.registry_digest,
        }
    )


async def submit_application_for_review(
    session: AsyncSession,
    application: Application,
    *,
    actor: DemoActor,
    warnings_acknowledged: bool,
    raw_key: str,
) -> tuple[ApplicationSnapshot, AuditEvent]:
    if application.applicant_organization_id != actor.organization_id:
        raise ApplicationLifecycleError(
            "only the requester organization may submit its draft"
        )
    command = _command(
        actor,
        action="application-submit",
        raw_key=raw_key,
        subject_id=application.id,
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="application.submitted",
        subject_type="application",
        subject_id=application.id,
    )
    if replay is not None:
        if (
            replay.evidence_snapshot.get("warnings_acknowledged")
            is not warnings_acknowledged
        ):
            raise ApplicationLifecycleError(
                "idempotency key is already bound to another request"
            )
        snapshot = await session.scalar(
            select(ApplicationSnapshot).where(
                ApplicationSnapshot.application_id == application.id
            )
        )
        if snapshot is None:
            raise ApplicationLifecycleError("submitted snapshot is missing")
        return snapshot, replay
    if application.status != "draft":
        raise ApplicationLifecycleError(
            "only the requester organization may submit its draft"
        )
    item, action, selection = await _draft_components(session, application.id)
    request = action.parameters.get("request")
    report = action.parameters.get("compatibility")
    if not isinstance(request, dict) or not isinstance(report, dict):
        raise ApplicationLifecycleError(
            "run the server compatibility check before submitting"
        )
    current_digest = await _current_compatibility_input_digest(
        session,
        application,
        request=request,
        item=item,
        selection=selection,
    )
    if report.get("input_digest") != current_digest:
        raise ApplicationLifecycleError(
            "compatibility result is stale; run the check again"
        )
    if report.get("blockers"):
        raise ApplicationLifecycleError(
            "application has compatibility blockers and cannot be submitted"
        )
    if report.get("warnings") and not warnings_acknowledged:
        raise ApplicationLifecycleError(
            "compatibility warnings must be acknowledged before submission"
        )
    action.parameters = {
        **action.parameters,
        "warning_acknowledged": warnings_acknowledged,
    }
    await session.flush()
    snapshot = await submit_application(
        session, application, submitted_by=actor.user_id
    )
    application.status = "prechecking"
    application.row_version += 1
    await session.flush()
    space = await session.get(Space, application.space_id)
    if space is None:
        raise ApplicationLifecycleError("application Space is missing")
    routing_specs = (
        ("application_precheck", 10, space.operator_organization_id),
        ("data_provider_review", 20, application.provider_organization_id),
        (
            "model_provider_review",
            30,
            selection.model_provider_organization_id,
        ),
    )
    for review_type, sequence_no, organization_id in routing_specs:
        routing = {
            "schema_version": "phase5.3/review-route/v1",
            "application_snapshot_id": str(snapshot.id),
            "target_digest": snapshot.snapshot_digest,
            "review_type": review_type,
            "assignee_organization_id": str(organization_id),
            "sequence_no": sequence_no,
        }
        session.add(
            ReviewTask(
                id=uuid5(
                    NAMESPACE_URL,
                    f"medtrust:phase5.3:review:{snapshot.id}:{review_type}",
                ),
                space_id=application.space_id,
                review_type=review_type,
                application_id=application.id,
                application_snapshot_id=snapshot.id,
                target_digest=snapshot.snapshot_digest,
                assignee_organization_id=organization_id,
                task_status="pending",
                sequence_no=sequence_no,
                is_required=True,
                routing_rule_digest=canonical_json_digest_v1(routing),
                created_by=actor.user_id,
            )
        )
    await session.flush()
    event = await _append(
        session,
        command=command,
        space_id=application.space_id,
        event_type="application.submitted",
        subject_type="application",
        subject_id=application.id,
        evidence={
            "schema_version": "phase5.3/application-submitted/v1",
            "request_digest": current_digest,
            "application_snapshot_id": str(snapshot.id),
            "application_snapshot_digest": snapshot.snapshot_digest,
            "data_product_version_id": str(item.data_product_version_id),
            "model_version_id": str(selection.model_version_id),
            "compatibility_input_digest": current_digest,
            "compatibility_overall": report["overall"],
            "warnings_acknowledged": warnings_acknowledged,
            "state_before": "draft",
            "state_after": "prechecking",
        },
    )
    return snapshot, event


async def _clone_returned_application(
    session: AsyncSession,
    source: Application,
    *,
    actor: DemoActor,
    raw_key: str,
) -> Application:
    item = await session.scalar(
        select(ApplicationItem).where(ApplicationItem.application_id == source.id)
    )
    action = await session.scalar(
        select(ApplicationRequestedAction).where(
            ApplicationRequestedAction.application_id == source.id
        )
    )
    selection = await session.scalar(
        select(ApplicationModelSelection).where(
            ApplicationModelSelection.application_id == source.id
        )
    )
    if item is None or action is None or selection is None:
        raise ApplicationLifecycleError("returned application graph is incomplete")
    clone_id = uuid5(
        NAMESPACE_URL, f"medtrust:phase5.3:return-clone:{source.id}:{raw_key}"
    )
    existing = await session.get(Application, clone_id)
    if existing is not None:
        return existing
    request_parameters = deepcopy(action.parameters)
    request_parameters["compatibility"] = None
    request_parameters["remediation"] = {
        "schema_version": "phase5.3/remediation/v1",
        "source_application_id": str(source.id),
        "source_application_number": source.application_number,
    }
    clone = Application(
        id=clone_id,
        space_id=source.space_id,
        application_number=f"APP-{clone_id.hex[:8].upper()}",
        applicant_organization_id=source.applicant_organization_id,
        applicant_user_id=source.applicant_user_id,
        provider_organization_id=source.provider_organization_id,
        purpose=source.purpose,
        legal_or_ethics_basis=source.legal_or_ethics_basis,
        algorithm_name=source.algorithm_name,
        algorithm_version=source.algorithm_version,
        algorithm_digest=source.algorithm_digest,
        requested_duration_seconds=source.requested_duration_seconds,
        requested_run_limit=source.requested_run_limit,
        is_demo=source.is_demo,
        created_by=source.created_by,
    )
    session.add(clone)
    await session.flush()
    session.add_all(
        [
            ApplicationItem(
                application_id=clone.id,
                space_id=clone.space_id,
                provider_organization_id=item.provider_organization_id,
                data_product_id=item.data_product_id,
                data_product_version_id=item.data_product_version_id,
                position_no=item.position_no,
                requested_product_snapshot_digest=item.requested_product_snapshot_digest,
                requested_policy_digest=item.requested_policy_digest,
                requested_scope=deepcopy(item.requested_scope),
            ),
            ApplicationRequestedAction(
                application_id=clone.id,
                action_code=action.action_code,
                parameters=request_parameters,
            ),
            ApplicationRequestedOutputType(
                application_id=clone.id,
                output_type="aggregate_statistics",
                requires_manual_review=False,
            ),
            ApplicationModelSelection(
                application_id=clone.id,
                space_id=clone.space_id,
                model_provider_organization_id=selection.model_provider_organization_id,
                model_product_id=selection.model_product_id,
                model_version_id=selection.model_version_id,
                model_snapshot_digest=selection.model_snapshot_digest,
                requested_model_policy_digest=selection.requested_model_policy_digest,
                registry_digest=selection.registry_digest,
            ),
        ]
    )
    await session.flush()
    create_command = _command(
        actor,
        action="application-return-clone",
        raw_key=f"{raw_key}:clone",
        subject_id=clone.id,
    )
    await _append(
        session,
        command=create_command,
        space_id=clone.space_id,
        event_type="application.created",
        subject_type="application",
        subject_id=clone.id,
        evidence={
            "schema_version": "phase5.3/application-created/v1",
            "request_digest": _request_digest(request_parameters["request"]),
            "application_id": str(clone.id),
            "application_number": clone.application_number,
            "source_application_id": str(source.id),
            "remediation": "clone_and_resubmit",
            "state_after": "draft",
        },
    )
    return clone


async def decide_application_review(
    session: AsyncSession,
    task: ReviewTask,
    *,
    application: Application,
    actor: DemoActor,
    action: str,
    reason_code: str | None,
    comment: str,
    evidence: dict[str, Any],
    raw_key: str,
) -> tuple[ReviewDecision, Application | None, AuditEvent]:
    expected_role = REVIEW_ROLE.get(task.review_type)
    if expected_role is None or actor.role != expected_role:
        raise ApplicationLifecycleError("review task does not belong to this role")
    await require_actor(
        session,
        space_id=application.space_id,
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        role_code=expected_role,
    )
    if (
        task.application_id != application.id
        or task.assignee_organization_id != actor.organization_id
    ):
        raise ApplicationLifecycleError("review task is outside this organization")
    if action not in {"approve", "return", "reject"}:
        raise ApplicationLifecycleError("unsupported review action")
    command = _command(
        actor,
        action=f"application-review:{task.review_type}:{action}",
        raw_key=raw_key,
        subject_id=application.id,
    )
    request_digest = canonical_json_digest_v1(
        {
            "task_id": str(task.id),
            "action": action,
            "reason_code": reason_code,
            "comment": comment,
            "evidence": evidence,
        }
    )
    replay = await _existing_event(
        session,
        command=command,
        event_type="application.review.decided",
        subject_type="review_decision",
        request_digest=request_digest,
    )
    if replay is not None:
        decision_id = replay.evidence_snapshot.get("decision_id")
        decision = (
            None
            if decision_id is None
            else await session.get(ReviewDecision, UUID(decision_id))
        )
        replacement_id = replay.evidence_snapshot.get("replacement_application_id")
        replacement = (
            None
            if replacement_id is None
            else await session.get(Application, UUID(replacement_id))
        )
        if decision is None:
            raise ApplicationLifecycleError("idempotent review decision is missing")
        return decision, replacement, replay

    if task.task_status != "pending":
        raise ApplicationLifecycleError("review task has already been processed")
    if task.review_type != "application_precheck":
        precheck = await session.scalar(
            select(ReviewTask).where(
                ReviewTask.application_id == application.id,
                ReviewTask.review_type == "application_precheck",
                ReviewTask.task_status == "decided",
            )
        )
        precheck_decision = (
            None
            if precheck is None
            else await session.scalar(
                select(ReviewDecision).where(
                    ReviewDecision.review_task_id == precheck.id,
                    ReviewDecision.decision == "approved",
                )
            )
        )
        if precheck_decision is None:
            raise ApplicationLifecycleError(
                "platform precheck must approve the application first"
            )
    if task.review_type == "model_provider_review":
        data_task = await session.scalar(
            select(ReviewTask).where(
                ReviewTask.application_id == application.id,
                ReviewTask.review_type == "data_provider_review",
                ReviewTask.task_status == "decided",
            )
        )
        data_decision = (
            None
            if data_task is None
            else await session.scalar(
                select(ReviewDecision).where(
                    ReviewDecision.review_task_id == data_task.id,
                    ReviewDecision.decision == "approved",
                )
            )
        )
        if data_decision is None:
            raise ApplicationLifecycleError(
                "hospital data-use review must approve the application first"
            )

    claim_review_task(task, user_id=actor.user_id)
    await session.flush()
    decision_value = "approved" if action == "approve" else "rejected"
    remediation = "clone_and_resubmit" if action == "return" else None
    if decision_value == "rejected" and reason_code is None:
        reason_code = "other"
    decision = await submit_review_decision(
        session,
        task,
        decision=decision_value,
        decided_by_user_id=actor.user_id,
        decided_for_organization_id=actor.organization_id,
        reason_code=None if decision_value == "approved" else reason_code,
        comment=comment,
        remediation=remediation,
        evidence=evidence,
    )
    replacement: Application | None = None
    if action in {"return", "reject"}:
        application.status = "rejected"
        application.decided_at = datetime.now(timezone.utc)
        application.decision_summary = (
            f"{task.review_type} returned for supplementation"
            if action == "return"
            else f"{task.review_type} rejected"
        )
        application.row_version += 1
        for downstream in list(
            (
                await session.scalars(
                    select(ReviewTask).where(
                        ReviewTask.application_id == application.id,
                        ReviewTask.task_status.in_(("pending", "claimed")),
                        ReviewTask.id != task.id,
                    )
                )
            ).all()
        ):
            cancel_review_task(downstream, reason="upstream_rejected")
        await session.flush()
        if action == "return":
            replacement = await _clone_returned_application(
                session, application, actor=actor, raw_key=raw_key
            )
    elif task.review_type == "application_precheck":
        application.status = "provider_review"
        application.row_version += 1
        await session.flush()
    elif task.review_type == "model_provider_review":
        required = list(
            (
                await session.scalars(
                    select(ReviewTask).where(
                        ReviewTask.application_id == application.id,
                        ReviewTask.is_required.is_(True),
                    )
                )
            ).all()
        )
        decisions = list(
            (
                await session.scalars(
                    select(ReviewDecision).where(
                        ReviewDecision.review_task_id.in_(
                            [current.id for current in required]
                        )
                    )
                )
            ).all()
        )
        if len(decisions) == len(required) and all(
            current.decision == "approved" for current in decisions
        ):
            application.status = "approved"
            application.decided_at = datetime.now(timezone.utc)
            application.decision_summary = (
                "所有必需审核均已通过，下一步进入数字合约"
            )
            application.row_version += 1
            await session.flush()

    review_event = await _append(
        session,
        command=command,
        space_id=application.space_id,
        event_type="application.review.decided",
        subject_type="review_decision",
        subject_id=decision.id,
        evidence={
            "schema_version": "phase5.3/application-review-decided/v1",
            "request_digest": request_digest,
            "application_id": str(application.id),
            "review_task_id": str(task.id),
            "decision_id": str(decision.id),
            "review_type": task.review_type,
            "action": action,
            "decision": decision.decision,
            "decision_digest": decision.decision_digest,
            "replacement_application_id": (
                str(replacement.id) if replacement is not None else None
            ),
        },
    )
    terminal_type = {
        "return": "application.returned",
        "reject": "application.rejected",
    }.get(action)
    if terminal_type is not None:
        terminal_command = _command(
            actor,
            action=terminal_type,
            raw_key=f"{raw_key}:application",
            subject_id=application.id,
        )
        await _append(
            session,
            command=terminal_command,
            space_id=application.space_id,
            event_type=terminal_type,
            subject_type="application",
            subject_id=application.id,
            evidence={
                "schema_version": f"phase5.3/{terminal_type.replace('.', '-')}/v1",
                "application_id": str(application.id),
                "review_task_id": str(task.id),
                "decision_id": str(decision.id),
                "review_type": task.review_type,
                "replacement_application_id": (
                    str(replacement.id) if replacement is not None else None
                ),
                "state_before": (
                    "prechecking"
                    if task.review_type == "application_precheck"
                    else "provider_review"
                ),
                "state_after": "rejected",
            },
        )
    elif application.status == "approved":
        approved_command = _command(
            actor,
            action="application-approved",
            raw_key=f"{raw_key}:application",
            subject_id=application.id,
        )
        await _append(
            session,
            command=approved_command,
            space_id=application.space_id,
            event_type="application.approved",
            subject_type="application",
            subject_id=application.id,
            evidence={
                "schema_version": "phase5.3/application-approved/v1",
                "application_id": str(application.id),
                "review_task_id": str(task.id),
                "decision_id": str(decision.id),
                "state_before": "provider_review",
                "state_after": "approved",
                "next_step": "digital_contract",
            },
        )
    return decision, replacement, review_event
