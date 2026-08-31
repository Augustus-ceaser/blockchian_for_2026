from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor, load_pathmnist_model_registry
from app.modules.applications.models import (
    Application,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
)
from app.modules.audit.services import (
    AuditCommandContext,
    append_audit_event_with_outbox,
    begin_audited_command,
    digest_idempotency_key,
)
from app.modules.audit.models import AuditEvent
from app.modules.catalog.models import DataProduct, DataProductVersion, DataResource
from app.modules.external_catalog.eligibility import (
    ExternalDataProductEligibilityError,
    ExternalModelProductEligibilityError,
    require_materialized_data_product,
    require_materialized_model_product,
)
from app.modules.compute.models import (
    ComputeJob,
    ComputeRun,
    ExecutionEligibilityInvalidation,
    ExecutionEligibilitySnapshot,
)
from app.modules.compute.services import (
    ComputeInvariantError,
    create_compute_job,
    evaluate_compute_authorization,
    prepare_compute_run,
    reserve_compute_run,
    validate_compute_job,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.contracts.models import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    Policy,
    PolicyExecutionBinding,
)
from app.modules.contracts.security import (
    security_blocker_message,
    validate_contract_security,
)
from app.modules.contracts.services import canonical_document_digest
from app.modules.marketplace.models import (
    ContractModelObject,
    ContractReadinessConfirmation,
    ContractReadinessRevocation,
    ModelProduct,
    ModelVersion,
)
from app.modules.marketplace.services import (
    MarketplaceServiceError,
    confirm_contract_readiness,
)


class ExecutionReadinessError(ValueError):
    pass


def _command(actor: DemoActor, action: str, raw_key: str) -> AuditCommandContext:
    return AuditCommandContext(
        command_id=uuid5(
            NAMESPACE_URL, f"medtrust:phase5.5:{action}:{raw_key}"
        ),
        idempotency_key=digest_idempotency_key(
            f"phase5.5:{action}:{raw_key}"
        ),
        correlation_id=uuid5(
            NAMESPACE_URL, "medtrust:phase5.5:roadshow-correlation"
        ),
        actor_type="user",
        actor_organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )


async def _append_command(
    session: AsyncSession,
    *,
    actor: DemoActor,
    action: str,
    raw_key: str,
    space_id: UUID,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    result: str,
    request_snapshot: dict[str, Any],
    evidence_snapshot: dict[str, Any],
) -> None:
    command = _command(actor, action, raw_key)
    existing, request_digest = await begin_audited_command(
        session,
        space_id=space_id,
        event_type=event_type,
        subject_type=subject_type,
        command=command,
        request_snapshot=request_snapshot,
        expected_subject_id=subject_id,
    )
    if existing is not None:
        return
    await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        result=result,
        evidence_snapshot={
            **evidence_snapshot,
            "command_request_digest": request_digest,
        },
        **command.append_kwargs(),
    )


async def current_readiness(
    session: AsyncSession,
    revision_id: UUID,
    readiness_type: str,
) -> ContractReadinessConfirmation | None:
    return await session.scalar(
        select(ContractReadinessConfirmation)
        .outerjoin(
            ContractReadinessRevocation,
            ContractReadinessRevocation.readiness_confirmation_id
            == ContractReadinessConfirmation.id,
        )
        .where(
            ContractReadinessConfirmation.contract_revision_id == revision_id,
            ContractReadinessConfirmation.readiness_type == readiness_type,
            ContractReadinessRevocation.id.is_(None),
        )
        .order_by(ContractReadinessConfirmation.confirmed_at.desc())
        .limit(1)
    )


async def _connector_for_contract(
    session: AsyncSession,
    *,
    space_id: UUID,
    owner_organization_id: UUID,
) -> tuple[Connector, list[ConnectorCapability]]:
    connectors = list(
        (
            await session.scalars(
                select(Connector).where(
                    Connector.space_id == space_id,
                    Connector.owner_organization_id == owner_organization_id,
                    Connector.verification_status == "verified",
                    Connector.runtime_status == "online",
                )
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    for connector in connectors:
        heartbeat = connector.last_heartbeat_at
        if heartbeat is None:
            continue
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        if heartbeat < now - timedelta(minutes=5):
            continue
        capabilities = list(
            (
                await session.scalars(
                    select(ConnectorCapability).where(
                        ConnectorCapability.connector_id == connector.id,
                        ConnectorCapability.status == "verified",
                    )
                )
            ).all()
        )
        if {
            item.capability_code for item in capabilities
        } >= {
            "controlled_compute_execution",
            "egress_policy_enforcement",
            "audit_evidence_emit",
        }:
            return connector, capabilities
    raise ExecutionReadinessError(
        "required verified online Connector capabilities are unavailable"
    )


async def confirm_productized_readiness(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    readiness_type: str,
    actor: DemoActor,
    workspace: Path,
    raw_key: str,
    confirmation_note: str = "provider confirmed locked execution asset readiness",
) -> ContractReadinessConfirmation:
    security = await validate_contract_security(session, revision, stage="execute")
    if security["overall"] != "PASS":
        raise ExecutionReadinessError(security_blocker_message(security))
    existing = await current_readiness(session, revision.id, readiness_type)
    if existing is not None:
        return existing
    historical = await session.scalar(
        select(ContractReadinessConfirmation)
        .join(
            ContractReadinessRevocation,
            ContractReadinessRevocation.readiness_confirmation_id
            == ContractReadinessConfirmation.id,
        )
        .where(
            ContractReadinessConfirmation.contract_revision_id == revision.id,
            ContractReadinessConfirmation.readiness_type == readiness_type,
        )
    )
    if historical is not None:
        raise ExecutionReadinessError(
            "revoked readiness requires a changed target before reconfirmation"
        )
    contract = await session.get(Contract, revision.contract_id)
    if contract is None or revision.status != "active":
        raise ExecutionReadinessError("an active ContractRevision is required")
    data_object = await session.scalar(
        select(ContractObject).where(
            ContractObject.contract_revision_id == revision.id
        )
    )
    model_object = await session.scalar(
        select(ContractModelObject).where(
            ContractModelObject.contract_revision_id == revision.id
        )
    )
    if data_object is None or model_object is None:
        raise ExecutionReadinessError("contract execution objects are incomplete")
    security_reference = {
        "decision_id": security["decision_id"],
        "snapshot_digest": security["snapshot_digest"],
        "profile_version": security["profile_version"],
    }

    if readiness_type == "data_ready":
        try:
            await require_materialized_data_product(
                session, data_object.data_product_version_id
            )
        except ExternalDataProductEligibilityError as exc:
            raise ExecutionReadinessError(str(exc)) from exc
        version = await session.get(
            DataProductVersion, data_object.data_product_version_id
        )
        product = (
            None
            if version is None
            else await session.get(DataProduct, version.data_product_id)
        )
        if (
            version is None
            or product is None
            or version.status != "approved"
            or version.snapshot_digest != data_object.product_snapshot_digest
            or product.provider_organization_id != actor.organization_id
        ):
            raise ExecutionReadinessError(
                "contracted data version is unavailable to this organization"
            )
        connector, capabilities = await _connector_for_contract(
            session,
            space_id=contract.space_id,
            owner_organization_id=actor.organization_id,
        )
        resources = list(
            (
                await session.scalars(
                    select(DataResource).where(
                        DataResource.data_product_version_id == version.id
                    )
                )
            ).all()
        )
        target = {
            "schema_version": "phase5.5/data-readiness-target/v1",
            "contract_revision_id": str(revision.id),
            "data_product_version_id": str(version.id),
            "data_snapshot_digest": version.snapshot_digest,
            "authorized_scope_digest": data_object.authorized_scope_digest,
            "connector_id": str(connector.id),
            "contract_security": security_reference,
            "resource_digests": sorted(
                item.resource_digest for item in resources if item.resource_digest
            ),
        }
        evidence = {
            "schema_version": "phase5.5/data-readiness-evidence/v1",
            "provider_confirmation_note": confirmation_note,
            "quality_checked": bool(version.quality_report),
            "input_read_only": True,
            "raw_download_allowed": False,
            "connector_runtime_status": connector.runtime_status,
            "connector_last_heartbeat_at": connector.last_heartbeat_at.isoformat(),
            "capabilities": sorted(
                item.capability_code for item in capabilities
            ),
            "contract_security_validation": security,
            "hard_isolation": False,
        }
        registry = None
    elif readiness_type == "model_ready":
        try:
            await require_materialized_model_product(
                session, model_object.model_version_id
            )
        except ExternalModelProductEligibilityError as exc:
            raise ExecutionReadinessError(str(exc)) from exc
        version = await session.get(ModelVersion, model_object.model_version_id)
        product = (
            None
            if version is None
            else await session.get(ModelProduct, version.model_product_id)
        )
        if (
            version is None
            or product is None
            or version.status != "approved"
            or version.snapshot_digest != model_object.model_snapshot_digest
            or product.provider_organization_id != actor.organization_id
        ):
            raise ExecutionReadinessError(
                "contracted model version is unavailable to this organization"
            )
        registry = load_pathmnist_model_registry(workspace)
        registration = registry.require_enabled(version.model_digest)
        target = {
            "schema_version": "phase5.5/model-readiness-target/v1",
            "contract_revision_id": str(revision.id),
            "model_version_id": str(version.id),
            "model_snapshot_digest": version.snapshot_digest,
            "model_digest": version.model_digest,
            "registry_digest": version.registry_digest,
            "entrypoint_id": version.entrypoint_id,
            "contract_security": security_reference,
        }
        evidence = {
            "schema_version": "phase5.5/model-readiness-evidence/v1",
            "provider_confirmation_note": confirmation_note,
            "runtime": registration.runtime,
            "cpu_limit": registration.cpu_limit,
            "memory_limit_mb": registration.memory_limit,
            "timeout_seconds": registration.timeout_seconds,
            "network_access": False,
            "dynamic_dependencies": False,
            "model_download_allowed": False,
            "contract_security_validation": security,
            "hard_isolation": False,
        }
    else:
        raise ExecutionReadinessError("unknown provider readiness type")

    try:
        return await confirm_contract_readiness(
            session,
            revision,
            readiness_type=readiness_type,
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            target_snapshot=target,
            evidence_snapshot=evidence,
            command=_command(actor, f"readiness:{readiness_type}", raw_key),
            registry=registry,
        )
    except MarketplaceServiceError as exc:
        raise ExecutionReadinessError(str(exc)) from exc


async def _ensure_execution_bindings(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    operator: DemoActor,
) -> tuple[PolicyExecutionBinding, ...]:
    contract = await session.get(Contract, revision.contract_id)
    data_party = await session.scalar(
        select(ContractParty).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.party_role == "data_provider",
        )
    )
    if contract is None or data_party is None:
        raise ExecutionReadinessError("contract execution parties are incomplete")
    connector, _ = await _connector_for_contract(
        session,
        space_id=contract.space_id,
        owner_organization_id=data_party.organization_id,
    )
    policies = list(
        (
            await session.scalars(
                select(Policy).where(Policy.contract_revision_id == revision.id)
            )
        ).all()
    )
    required = {
        ("execute_controlled_compute", "permit"): (
            "compute_executor",
            "controlled_compute_execution",
        ),
        ("export_artifact", "permit"): (
            "egress_controller",
            "egress_policy_enforcement",
        ),
        ("write_audit_log", "require"): (
            "audit_evidence_emitter",
            "audit_evidence_emit",
        ),
    }
    bindings: list[PolicyExecutionBinding] = []
    now = datetime.now(timezone.utc)
    for policy in policies:
        spec = required.get((policy.action_code, policy.effect))
        if spec is None:
            continue
        execution_role, capability_code = spec
        binding = await session.scalar(
            select(PolicyExecutionBinding).where(
                PolicyExecutionBinding.policy_id == policy.id,
                PolicyExecutionBinding.execution_role == execution_role,
                PolicyExecutionBinding.is_required.is_(True),
            )
        )
        if binding is None:
            raise ExecutionReadinessError(
                f"{execution_role} binding is missing from the frozen contract"
            )
        if (
            binding.connector_id != connector.id
            or binding.required_capability_code != capability_code
            or binding.required_capability_version != "1.0"
        ):
            raise ExecutionReadinessError(
                f"{execution_role} binding no longer matches the current capability"
            )
        if binding.deployment_status == "pending":
            receipt = {
                "schema_version": "phase5.5/binding-acceptance/v1",
                "contract_revision_id": str(revision.id),
                "policy_id": str(policy.id),
                "binding_id": str(binding.id),
                "connector_id": str(binding.connector_id),
                "execution_role": execution_role,
                "capability_code": capability_code,
                "capability_version": "1.0",
            }
            binding.deployment_status = "accepted"
            binding.acknowledged_at = now
            binding.receipt_digest = canonical_document_digest(receipt)
            binding.row_version += 1
            await session.flush()
        if binding.deployment_status != "accepted":
            raise ExecutionReadinessError(
                f"{execution_role} binding is not accepted"
            )
        bindings.append(binding)
    if len(bindings) != 3:
        raise ExecutionReadinessError(
            "contract does not provide the required execution policy set"
        )
    return tuple(bindings)


async def _authorization_inputs(
    session: AsyncSession, revision: ContractRevision
) -> tuple[
    Contract,
    Application,
    ContractParty,
    ContractObject,
    ContractModelObject,
    ModelVersion,
    str,
    list[str],
]:
    contract = await session.get(Contract, revision.contract_id)
    application = (
        None
        if contract is None
        else await session.get(Application, contract.application_id)
    )
    requester = await session.scalar(
        select(ContractParty).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.party_role == "data_requester",
        )
    )
    data_object = await session.scalar(
        select(ContractObject).where(
            ContractObject.contract_revision_id == revision.id
        )
    )
    model_object = await session.scalar(
        select(ContractModelObject).where(
            ContractModelObject.contract_revision_id == revision.id
        )
    )
    model_version = (
        None
        if model_object is None
        else await session.get(ModelVersion, model_object.model_version_id)
    )
    actions = (
        []
        if application is None
        else list(
            (
                await session.scalars(
                    select(ApplicationRequestedAction.action_code).where(
                        ApplicationRequestedAction.application_id == application.id
                    )
                )
            ).all()
        )
    )
    outputs = (
        []
        if application is None
        else sorted(
            (
                await session.scalars(
                    select(ApplicationRequestedOutputType.output_type).where(
                        ApplicationRequestedOutputType.application_id
                        == application.id
                    )
                )
            ).all()
        )
    )
    if (
        contract is None
        or application is None
        or requester is None
        or data_object is None
        or model_object is None
        or model_version is None
        or not actions
        or not outputs
    ):
        raise ExecutionReadinessError(
            "contract authorization inputs are incomplete"
        )
    purpose = (
        "model_validation"
        if "model_validation" in actions
        else sorted(actions)[0]
    )
    return (
        contract,
        application,
        requester,
        data_object,
        model_object,
        model_version,
        purpose,
        outputs,
    )


async def invalidate_eligibility_snapshot(
    session: AsyncSession,
    snapshot: ExecutionEligibilitySnapshot,
    *,
    actor: DemoActor,
    reason_code: str,
    raw_key: str,
) -> ExecutionEligibilityInvalidation:
    existing = await session.scalar(
        select(ExecutionEligibilityInvalidation).where(
            ExecutionEligibilityInvalidation.execution_eligibility_snapshot_id
            == snapshot.id
        )
    )
    if existing is not None:
        return existing
    evidence = {
        "schema_version": "phase5.5/eligibility-invalidated/v1",
        "eligibility_snapshot_id": str(snapshot.id),
        "eligibility_snapshot_digest": snapshot.eligibility_snapshot_digest,
        "reason_code": reason_code,
    }
    row = ExecutionEligibilityInvalidation(
        space_id=snapshot.space_id,
        execution_eligibility_snapshot_id=snapshot.id,
        reason_code=reason_code,
        evidence_snapshot=evidence,
        evidence_digest=canonical_document_digest(evidence),
        invalidated_by=actor.user_id,
    )
    session.add(row)
    await session.flush()
    await _append_command(
        session,
        actor=actor,
        action="eligibility-invalidated",
        raw_key=raw_key,
        space_id=snapshot.space_id,
        event_type="execution.eligibility.invalidated",
        subject_type="execution_eligibility_invalidation",
        subject_id=row.id,
        result="success",
        request_snapshot=evidence,
        evidence_snapshot=evidence,
    )
    return row


async def run_eligibility_check(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    operator: DemoActor,
    raw_key: str,
) -> tuple[ExecutionEligibilitySnapshot | None, dict[str, Any]]:
    if revision.status != "active":
        raise ExecutionReadinessError("eligibility requires an active contract")
    security = await validate_contract_security(session, revision, stage="execute")
    if security["overall"] != "PASS":
        raise ExecutionReadinessError(security_blocker_message(security))
    data_ready = await current_readiness(session, revision.id, "data_ready")
    model_ready = await current_readiness(session, revision.id, "model_ready")
    if data_ready is None or model_ready is None:
        blockers = []
        if data_ready is None:
            blockers.append("data readiness is incomplete")
        if model_ready is None:
            blockers.append("model readiness is incomplete")
        report = {
            "schema_version": "phase5.5/eligibility-check/v1",
            "overall": "BLOCKER",
            "checks": [
                {
                    "code": "provider_readiness",
                    "result": "BLOCKER",
                    "expected": "data_ready and model_ready",
                    "actual": blockers,
                    "source": "contract readiness",
                    "message": "; ".join(blockers),
                }
            ],
            "hard_isolation": False,
        }
        await _append_command(
            session,
            actor=operator,
            action="eligibility-blocked",
            raw_key=raw_key,
            space_id=(await session.get(Contract, revision.contract_id)).space_id,
            event_type="execution.eligibility.blocked",
            subject_type="contract_revision",
            subject_id=revision.id,
            result="denied",
            request_snapshot={
                "schema_version": "phase5.5/eligibility-check-command/v1",
                "contract_revision_id": str(revision.id),
            },
            evidence_snapshot=report,
        )
        return None, report

    await _ensure_execution_bindings(session, revision, operator=operator)
    platform_ready = await current_readiness(
        session, revision.id, "platform_ready"
    )
    if platform_ready is None:
        contract = await session.get(Contract, revision.contract_id)
        target = {
            "schema_version": "phase5.5/platform-readiness-target/v1",
            "contract_revision_id": str(revision.id),
            "revision_content_digest": revision.content_digest,
            "data_readiness_id": str(data_ready.id),
            "model_readiness_id": str(model_ready.id),
            "contract_security": {
                "decision_id": security["decision_id"],
                "snapshot_digest": security["snapshot_digest"],
                "profile_version": security["profile_version"],
            },
        }
        try:
            platform_ready = await confirm_contract_readiness(
                session,
                revision,
                readiness_type="platform_ready",
                organization_id=operator.organization_id,
                user_id=operator.user_id,
                target_snapshot=target,
                evidence_snapshot={
                    "schema_version": "phase5.5/platform-readiness-evidence/v1",
                    "bindings_verified": True,
                    "connector_heartbeat_checked": True,
                    "contract_security_validation": security,
                    "hard_isolation": False,
                },
                command=_command(operator, "readiness:platform_ready", raw_key),
            )
        except MarketplaceServiceError as exc:
            raise ExecutionReadinessError(str(exc)) from exc
        if contract is None:
            raise ExecutionReadinessError("contract is unavailable")

    (
        contract,
        application,
        requester,
        data_object,
        model_object,
        model_version,
        purpose,
        outputs,
    ) = await _authorization_inputs(session, revision)
    try:
        authorization = await evaluate_compute_authorization(
            session,
            revision_id=revision.id,
            party_id=requester.id,
            contract_object_id=data_object.id,
            requester_organization_id=requester.organization_id,
            requester_user_id=application.applicant_user_id,
            purpose_code=purpose,
            algorithm_digest=model_version.model_digest,
            requested_output_types=outputs,
        )
    except ComputeInvariantError as exc:
        report = {
            "schema_version": "phase5.5/eligibility-check/v1",
            "overall": "BLOCKER",
            "checks": [
                {
                    "code": "authorization",
                    "result": "BLOCKER",
                    "expected": "current executable contract graph",
                    "actual": str(exc),
                    "source": "compute authorization service",
                    "message": str(exc),
                }
            ],
            "hard_isolation": False,
        }
        await _append_command(
            session,
            actor=operator,
            action="eligibility-blocked",
            raw_key=raw_key,
            space_id=contract.space_id,
            event_type="execution.eligibility.blocked",
            subject_type="contract_revision",
            subject_id=revision.id,
            result="denied",
            request_snapshot={
                "schema_version": "phase5.5/eligibility-check-command/v1",
                "contract_revision_id": str(revision.id),
            },
            evidence_snapshot=report,
        )
        return None, report

    checks = [
        {
            "code": "contract_security",
            "result": "PASS",
            "expected": "PASS under the controlled-compute usage-policy profile",
            "actual": security["snapshot_digest"],
            "source": "shared contract security validator",
            "message": "the current contract graph passed all security gates",
        },
        {
            "code": "contract_active",
            "result": "PASS",
            "expected": "active and within effective window",
            "actual": revision.status,
            "source": "ContractRevision",
            "message": "contract lifecycle and time window are valid",
        },
        {
            "code": "data_readiness",
            "result": "PASS",
            "expected": str(data_object.data_product_version_id),
            "actual": str(data_ready.id),
            "source": "ContractReadinessConfirmation",
            "message": "hospital data target is locked",
        },
        {
            "code": "model_readiness",
            "result": "PASS",
            "expected": str(model_object.model_version_id),
            "actual": model_version.model_digest,
            "source": "ContractReadinessConfirmation and registry",
            "message": "fixed model asset and entrypoint are available",
        },
        {
            "code": "platform_capabilities",
            "result": "PASS",
            "expected": "three accepted capability bindings",
            "actual": authorization.execution_environment["bindings"],
            "source": "PolicyExecutionBinding and ConnectorCapability",
            "message": "compute, egress and audit capabilities are current",
        },
        {
            "code": "run_count",
            "result": "PASS",
            "expected": authorization.run_limit,
            "actual": authorization.evaluation["currently_reserved"],
            "source": "PolicyConstraint and reservation records",
            "message": "a pre-dispatch slot remains available",
        },
        {
            "code": "hard_isolation",
            "result": "WARNING",
            "expected": True,
            "actual": False,
            "source": "roadshow assurance boundary",
            "message": "hard isolation is not implemented in this engineering demo",
        },
    ]
    check_matrix_digest = canonical_document_digest({"items": checks})
    compatibility = {}
    action = await session.scalar(
        select(ApplicationRequestedAction).where(
            ApplicationRequestedAction.application_id == application.id,
            ApplicationRequestedAction.action_code == purpose,
        )
    )
    if action is not None and isinstance(action.parameters, dict):
        compatibility = action.parameters.get("compatibility") or {}
    stable_authorization_evaluation = {
        key: value
        for key, value in authorization.evaluation.items()
        if key != "evaluated_at"
    }
    eligibility = {
        "schema_version": "phase5.5/execution-eligibility/v1",
        "contract_id": str(contract.id),
        "contract_revision_id": str(revision.id),
        "revision_content_digest": revision.content_digest,
        "contract_security_decision_id": security["decision_id"],
        "contract_security_snapshot_digest": security["snapshot_digest"],
        "contract_security_profile_version": security["profile_version"],
        "application_id": str(application.id),
        "data_product_version_id": str(data_object.data_product_version_id),
        "data_snapshot_digest": data_object.product_snapshot_digest,
        "data_resource_digest": data_ready.target_digest,
        "model_version_id": str(model_version.id),
        "model_snapshot_digest": model_object.model_snapshot_digest,
        "model_digest": model_version.model_digest,
        "entrypoint_id": model_version.entrypoint_id,
        "compatibility_digest": canonical_document_digest(
            compatibility if isinstance(compatibility, dict) else {}
        ),
        "data_readiness_id": str(data_ready.id),
        "model_readiness_id": str(model_ready.id),
        "platform_readiness_id": str(platform_ready.id),
        "authorization_evaluation_digest": canonical_document_digest(
            stable_authorization_evaluation
        ),
        "execution_environment_digest": canonical_document_digest(
            authorization.execution_environment
        ),
        "check_matrix_digest": check_matrix_digest,
        "output_allowlist": outputs,
        "output_denylist": [
            "raw_data",
            "patient_level_result",
            "model_weights",
        ],
        "run_count_limit": authorization.run_limit,
        "network_mode": "deny_by_default",
        "input_mount": "read_only",
        "output_review_required": True,
        "hard_isolation": False,
        "ruleset_version": "phase5.5/eligibility-rules/v1",
    }
    eligibility_digest = canonical_document_digest(eligibility)
    existing = await session.scalar(
        select(ExecutionEligibilitySnapshot).where(
            ExecutionEligibilitySnapshot.eligibility_snapshot_digest
            == eligibility_digest
        )
    )
    if existing is not None:
        invalidation = await session.scalar(
            select(ExecutionEligibilityInvalidation.id).where(
                ExecutionEligibilityInvalidation.execution_eligibility_snapshot_id
                == existing.id
            )
        )
        if invalidation is None:
            return existing, {
                "schema_version": "phase5.5/eligibility-check/v1",
                "overall": "WARNING",
                "checks": checks,
                "snapshot_id": str(existing.id),
                "snapshot_digest": existing.eligibility_snapshot_digest,
                "contract_security_decision_id": security["decision_id"],
                "contract_security_snapshot_digest": security["snapshot_digest"],
                "hard_isolation": False,
            }

    snapshot_created_at = datetime.now(timezone.utc)
    snapshot = ExecutionEligibilitySnapshot(
        space_id=contract.space_id,
        contract_id=contract.id,
        contract_revision_id=revision.id,
        revision_content_digest=revision.content_digest,
        application_id=application.id,
        data_product_version_id=data_object.data_product_version_id,
        model_version_id=model_version.id,
        data_readiness_id=data_ready.id,
        model_readiness_id=model_ready.id,
        platform_readiness_id=platform_ready.id,
        check_matrix=checks,
        check_matrix_digest=check_matrix_digest,
        eligibility_snapshot=eligibility,
        eligibility_snapshot_digest=eligibility_digest,
        execution_environment_snapshot=authorization.execution_environment,
        execution_environment_digest=canonical_document_digest(
            authorization.execution_environment
        ),
        valid_until=revision.effective_until,
        created_at=snapshot_created_at,
        created_by=operator.user_id,
    )
    session.add(snapshot)
    await session.flush()
    await _append_command(
        session,
        actor=operator,
        action="eligibility-passed",
        raw_key=raw_key,
        space_id=contract.space_id,
        event_type="execution.eligibility.passed",
        subject_type="execution_eligibility",
        subject_id=snapshot.id,
        result="success",
        request_snapshot={
            "schema_version": "phase5.5/eligibility-check-command/v1",
            "contract_revision_id": str(revision.id),
            "check_matrix_digest": check_matrix_digest,
        },
        evidence_snapshot={
            "schema_version": "phase5.5/eligibility-passed/v1",
            "contract_revision_id": str(revision.id),
            "eligibility_snapshot_id": str(snapshot.id),
            "eligibility_snapshot_digest": snapshot.eligibility_snapshot_digest,
            "check_matrix_digest": snapshot.check_matrix_digest,
            "contract_security_validation": security,
            "hard_isolation": False,
        },
    )
    return snapshot, {
        "schema_version": "phase5.5/eligibility-check/v1",
        "overall": "WARNING",
        "checks": checks,
        "snapshot_id": str(snapshot.id),
        "snapshot_digest": snapshot.eligibility_snapshot_digest,
        "contract_security_decision_id": security["decision_id"],
        "contract_security_snapshot_digest": security["snapshot_digest"],
        "hard_isolation": False,
    }


async def revoke_productized_readiness(
    session: AsyncSession,
    confirmation: ContractReadinessConfirmation,
    *,
    actor: DemoActor,
    reason_code: str,
    raw_key: str,
) -> ContractReadinessRevocation:
    if confirmation.responsible_organization_id != actor.organization_id:
        raise ExecutionReadinessError(
            "only the responsible provider may revoke readiness"
        )
    existing_job = await session.scalar(
        select(ComputeJob.id).where(
            ComputeJob.contract_revision_id == confirmation.contract_revision_id
        )
    )
    if existing_job is not None:
        raise ExecutionReadinessError(
            "readiness cannot be revoked after ComputeJob creation"
        )
    existing = await session.scalar(
        select(ContractReadinessRevocation).where(
            ContractReadinessRevocation.readiness_confirmation_id
            == confirmation.id
        )
    )
    if existing is not None:
        return existing
    evidence = {
        "schema_version": "phase5.5/readiness-revoked/v1",
        "readiness_confirmation_id": str(confirmation.id),
        "readiness_type": confirmation.readiness_type,
        "target_digest": confirmation.target_digest,
        "reason_code": reason_code,
    }
    row = ContractReadinessRevocation(
        space_id=confirmation.space_id,
        readiness_confirmation_id=confirmation.id,
        responsible_organization_id=actor.organization_id,
        revoked_by_user_id=actor.user_id,
        reason_code=reason_code,
        evidence_snapshot=evidence,
        evidence_digest=canonical_document_digest(evidence),
    )
    session.add(row)
    await session.flush()
    await _append_command(
        session,
        actor=actor,
        action="readiness-revoked",
        raw_key=raw_key,
        space_id=confirmation.space_id,
        event_type="contract.readiness.revoked",
        subject_type="contract_readiness_revocation",
        subject_id=row.id,
        result="success",
        request_snapshot=evidence,
        evidence_snapshot=evidence,
    )
    snapshots = list(
        (
            await session.scalars(
                select(ExecutionEligibilitySnapshot).where(
                    (
                        ExecutionEligibilitySnapshot.data_readiness_id
                        == confirmation.id
                    )
                    | (
                        ExecutionEligibilitySnapshot.model_readiness_id
                        == confirmation.id
                    )
                    | (
                        ExecutionEligibilitySnapshot.platform_readiness_id
                        == confirmation.id
                    )
                )
            )
        ).all()
    )
    for snapshot in snapshots:
        await invalidate_eligibility_snapshot(
            session,
            snapshot,
            actor=actor,
            reason_code="readiness_revoked",
            raw_key=f"{raw_key}:{snapshot.id}",
        )
    return row


async def create_pre_dispatch_job(
    session: AsyncSession,
    snapshot: ExecutionEligibilitySnapshot,
    *,
    requester: DemoActor,
    raw_key: str,
) -> ComputeJob:
    create_command = _command(requester, "compute-job-create", raw_key)
    existing_event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.space_id == snapshot.space_id,
            AuditEvent.idempotency_key == create_command.idempotency_key,
            AuditEvent.event_type == "compute.job.created",
            AuditEvent.subject_type == "compute_job",
        )
    )
    if existing_event is not None:
        replay = await session.get(ComputeJob, existing_event.subject_id)
        if (
            replay is None
            or replay.execution_eligibility_snapshot_id != snapshot.id
            or replay.requester_organization_id != requester.organization_id
            or replay.requester_user_id != requester.user_id
        ):
            raise ExecutionReadinessError(
                "idempotency key maps to another ComputeJob request"
            )
        return replay
    invalidation = await session.scalar(
        select(ExecutionEligibilityInvalidation.id).where(
            ExecutionEligibilityInvalidation.execution_eligibility_snapshot_id
            == snapshot.id
        )
    )
    if invalidation is not None:
        raise ExecutionReadinessError("execution eligibility snapshot is invalidated")
    revision = await session.get(ContractRevision, snapshot.contract_revision_id)
    if revision is None:
        raise ExecutionReadinessError("execution eligibility contract is unavailable")
    security = await validate_contract_security(session, revision, stage="execute")
    if security["overall"] != "PASS":
        raise ExecutionReadinessError(security_blocker_message(security))
    (
        _,
        application,
        requester_party,
        data_object,
        _,
        model_version,
        purpose,
        outputs,
    ) = await _authorization_inputs(session, revision)
    if (
        requester.organization_id != requester_party.organization_id
        or requester.user_id != application.applicant_user_id
    ):
        raise ExecutionReadinessError(
            "only the contracted requester may create the ComputeJob"
        )
    algorithm_spec = {
        "schema_version": "phase5.5/algorithm-spec/v1",
        "algorithm_name": application.algorithm_name,
        "algorithm_version": application.algorithm_version,
        "algorithm_digest": model_version.model_digest,
        "registration_digest": model_version.registry_digest,
        "entrypoint_id": model_version.entrypoint_id,
        "model_version_id": str(model_version.id),
        "model_snapshot_digest": model_version.snapshot_digest,
        "execution_profile": "fixed_registry_controlled_compute",
        "declared_output_types": outputs,
    }
    try:
        return await create_compute_job(
            session,
            revision_id=revision.id,
            party_id=requester_party.id,
            contract_object_id=data_object.id,
            requester_organization_id=requester.organization_id,
            requester_user_id=requester.user_id,
            purpose_code=purpose,
            requested_output_types=outputs,
            algorithm_spec_snapshot=algorithm_spec,
            audit_command=create_command,
            eligibility_snapshot=snapshot,
            slot_audit_command=_command(
                requester, "pre-dispatch-slot-reserve", raw_key
            ),
        )
    except ComputeInvariantError as exc:
        raise ExecutionReadinessError(str(exc)) from exc


async def request_controlled_dispatch(
    session: AsyncSession,
    job: ComputeJob,
    *,
    operator: DemoActor,
    raw_key: str,
) -> tuple[ComputeRun, bool]:
    locked_job = await session.scalar(
        select(ComputeJob).where(ComputeJob.id == job.id).with_for_update()
    )
    if locked_job is None:
        raise ExecutionReadinessError("ComputeJob is unavailable")
    existing_run = await session.scalar(
        select(ComputeRun)
        .where(ComputeRun.compute_job_id == locked_job.id)
        .order_by(ComputeRun.attempt_no.desc())
        .limit(1)
    )
    if existing_run is not None:
        return existing_run, True
    if locked_job.status not in {"created", "ready"}:
        raise ExecutionReadinessError("ComputeJob is not dispatchable")
    if locked_job.execution_eligibility_snapshot_id is None:
        raise ExecutionReadinessError("ComputeJob has no execution eligibility snapshot")
    snapshot = await session.get(
        ExecutionEligibilitySnapshot,
        locked_job.execution_eligibility_snapshot_id,
    )
    if (
        snapshot is None
        or snapshot.eligibility_snapshot_digest
        != locked_job.eligibility_snapshot_digest
        or snapshot.contract_revision_id != locked_job.contract_revision_id
    ):
        raise ExecutionReadinessError("ComputeJob eligibility binding is invalid")
    if snapshot.valid_until <= datetime.now(timezone.utc):
        raise ExecutionReadinessError("execution eligibility snapshot has expired")
    invalidation = await session.scalar(
        select(ExecutionEligibilityInvalidation.id).where(
            ExecutionEligibilityInvalidation.execution_eligibility_snapshot_id
            == snapshot.id
        )
    )
    if invalidation is not None:
        raise ExecutionReadinessError("execution eligibility snapshot is invalidated")
    revision = await session.get(ContractRevision, locked_job.contract_revision_id)
    if revision is None or revision.status != "active":
        raise ExecutionReadinessError("Contract revision is not active")
    security = await validate_contract_security(session, revision, stage="execute")
    if security["overall"] != "PASS":
        raise ExecutionReadinessError(security_blocker_message(security))
    for readiness_type, expected_id in (
        ("data_ready", snapshot.data_readiness_id),
        ("model_ready", snapshot.model_readiness_id),
        ("platform_ready", snapshot.platform_readiness_id),
    ):
        current = await current_readiness(session, revision.id, readiness_type)
        if current is None or current.id != expected_id:
            raise ExecutionReadinessError(
                f"{readiness_type} no longer matches the eligibility snapshot"
            )
    try:
        await validate_compute_job(session, locked_job)
        run = await prepare_compute_run(
            session, locked_job, created_by=operator.user_id
        )
        await reserve_compute_run(
            session,
            run,
            audit_command=AuditCommandContext(
                command_id=uuid5(
                    NAMESPACE_URL, f"medtrust:phase5.6:dispatch:{raw_key}"
                ),
                idempotency_key=digest_idempotency_key(
                    f"phase5.6:dispatch:{raw_key}"
                ),
                correlation_id=uuid5(
                    NAMESPACE_URL, "medtrust:phase5.6:roadshow-correlation"
                ),
                actor_type="user",
                actor_organization_id=operator.organization_id,
                actor_user_id=operator.user_id,
            ),
        )
        return run, False
    except ComputeInvariantError as exc:
        raise ExecutionReadinessError(str(exc)) from exc
