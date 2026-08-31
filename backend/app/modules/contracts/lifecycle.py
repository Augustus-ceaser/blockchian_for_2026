from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor
from app.modules.applications.models import (
    Application,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationSnapshot,
)
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.catalog.models import DataProduct, DataProductVersion
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.contracts.models import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    ContractSignature,
    Policy,
    PolicyConstraint,
    PolicyExecutionBinding,
)
from app.modules.contracts.security import (
    CONTRACT_CANONICAL_V2,
    CONTRACT_SECURITY_PROFILE_V1,
    POLICY_CANONICAL_V2,
    PRODUCTIZED_TERMS_V2,
    build_contract_canonical_document_v2,
    build_policy_digest_document,
    security_blocker_message,
    validate_contract_security,
)
from app.modules.contracts.services import canonical_document_digest, sign_contract_revision
from app.modules.identity.models import Organization
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ContractModelObject,
    ModelProduct,
    ModelVersion,
)
from app.modules.reviews.models import ReviewDecision, ReviewTask
from app.modules.spaces.models import Space


class ContractLifecycleError(ValueError):
    pass


PERMANENT_DENIALS = {
    "arbitrary_files",
    "connector_credentials",
    "model_weights",
    "patient_level_predictions",
    "raw_features",
    "raw_images",
    "source_code",
}


def _command(actor: DemoActor, action: str, subject_id: UUID, raw_key: str) -> AuditCommandContext:
    return AuditCommandContext(
        command_id=uuid5(NAMESPACE_URL, f"medtrust:phase5.4:{action}:{subject_id}:{raw_key}"),
        idempotency_key=digest_idempotency_key(
            f"phase5.4:{action}:{subject_id}:{raw_key}"
        ),
        correlation_id=uuid5(NAMESPACE_URL, f"medtrust:phase5.4:contract:{subject_id}"),
        actor_type="user",
        actor_organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )


async def _append(
    session: AsyncSession,
    *,
    actor: DemoActor,
    action: str,
    raw_key: str,
    space_id: UUID,
    revision_id: UUID,
    event_type: str,
    evidence: dict[str, Any],
) -> AuditEvent:
    command = _command(actor, action, revision_id, raw_key)
    existing = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.idempotency_key == command.idempotency_key,
            AuditEvent.event_type == event_type,
            AuditEvent.subject_type == "contract_revision",
            AuditEvent.subject_id == revision_id,
        )
    )
    if existing is not None:
        return existing
    return await append_audit_event_with_outbox(
        session,
        space_id=space_id,
        event_type=event_type,
        subject_type="contract_revision",
        subject_id=revision_id,
        result="success",
        evidence_snapshot=evidence,
        **command.append_kwargs(),
    )


def _as_set(value: object) -> set[str]:
    return {str(item) for item in value} if isinstance(value, list) else set()


def _review_limit(decisions: list[ReviewDecision], key: str) -> list[int]:
    values: list[int] = []
    for decision in decisions:
        value = decision.evidence.get(key) if isinstance(decision.evidence, dict) else None
        if isinstance(value, int) and value > 0:
            values.append(value)
    return values


def converge_policy(
    *,
    request: dict[str, Any],
    data_policy: dict[str, Any],
    model_policy: dict[str, Any],
    decisions: list[ReviewDecision],
) -> dict[str, Any]:
    execution = request.get("execution", {})
    review = request.get("review_requirements", {})
    requested_outputs = _as_set(execution.get("requested_outputs"))
    data_outputs = _as_set(data_policy.get("allowed_outputs"))
    model_outputs = _as_set(model_policy.get("allowed_outputs"))
    review_outputs = [
        _as_set(item.evidence.get("allowed_outputs"))
        for item in decisions
        if isinstance(item.evidence, dict) and item.evidence.get("allowed_outputs")
    ]
    output_sets = [requested_outputs, data_outputs, model_outputs, *review_outputs]
    allowed_outputs = set.intersection(*output_sets) if all(output_sets) else set()

    forbidden_outputs = (
        PERMANENT_DENIALS
        | _as_set(data_policy.get("prohibited_outputs"))
        | _as_set(model_policy.get("prohibited_outputs"))
    )
    for decision in decisions:
        if isinstance(decision.evidence, dict):
            forbidden_outputs |= _as_set(decision.evidence.get("prohibited_outputs"))
    allowed_outputs -= forbidden_outputs

    run_limits = [
        int(execution.get("run_count", 0)),
        *[
            int(value)
            for value in (
                data_policy.get("max_runs"),
                model_policy.get("max_runs"),
                *_review_limit(decisions, "max_runs"),
            )
            if isinstance(value, int) and value > 0
        ],
    ]
    day_limits = [
        int(execution.get("valid_days", 0)),
        *[
            int(value)
            for value in (
                data_policy.get("valid_days"),
                model_policy.get("valid_days"),
                *_review_limit(decisions, "valid_days"),
            )
            if isinstance(value, int) and value > 0
        ],
    ]
    blockers: list[dict[str, str]] = []
    if not allowed_outputs:
        blockers.append(
            {
                "code": "empty_output_intersection",
                "message": "申请、数据方、模型方与审核条件的输出交集为空",
            }
        )
    if not run_limits or min(run_limits) < 1:
        blockers.append({"code": "invalid_run_limit", "message": "运行次数限制无有效交集"})
    if not day_limits or min(day_limits) < 1:
        blockers.append({"code": "invalid_validity", "message": "有效期限制无有效交集"})

    internet_votes = [
        bool(execution.get("internet_required", False)),
        bool(data_policy.get("network_allowed", False)),
        bool(model_policy.get("network_allowed", False)),
    ]
    final = {
        "run_count": min(run_limits) if run_limits else 0,
        "valid_days": min(day_limits) if day_limits else 0,
        "allowed_outputs": sorted(allowed_outputs),
        "forbidden_outputs": sorted(forbidden_outputs),
        "network_allowed": all(internet_votes),
        "input_read_only": bool(
            data_policy.get("input_read_only", True)
            or model_policy.get("input_read_only", True)
        ),
        "hospital_egress_review": bool(
            review.get("hospital_egress_review", True)
            or data_policy.get("requires_egress_review", False)
            or any(
                isinstance(item.evidence, dict)
                and item.evidence.get("requires_egress_review") is True
                for item in decisions
            )
        ),
        "model_technical_confirmation": bool(
            review.get("model_technical_confirmation", True)
            or model_policy.get("requires_technical_confirmation", False)
            or any(
                isinstance(item.evidence, dict)
                and item.evidence.get("requires_technical_confirmation") is True
                for item in decisions
            )
        ),
    }
    matrix = [
        {
            "constraint": "run_count",
            "request": execution.get("run_count"),
            "data_provider": data_policy.get("max_runs"),
            "model_provider": model_policy.get("max_runs"),
            "platform": "minimum of all approved limits",
            "final": final["run_count"],
        },
        {
            "constraint": "valid_days",
            "request": execution.get("valid_days"),
            "data_provider": data_policy.get("valid_days"),
            "model_provider": model_policy.get("valid_days"),
            "platform": "earliest approved expiry",
            "final": final["valid_days"],
        },
        {
            "constraint": "allowed_outputs",
            "request": sorted(requested_outputs),
            "data_provider": sorted(data_outputs),
            "model_provider": sorted(model_outputs),
            "platform": "intersection minus permanent denials",
            "final": final["allowed_outputs"],
        },
        {
            "constraint": "network",
            "request": bool(execution.get("internet_required", False)),
            "data_provider": bool(data_policy.get("network_allowed", False)),
            "model_provider": bool(model_policy.get("network_allowed", False)),
            "platform": "allowed only when every source allows",
            "final": final["network_allowed"],
        },
    ]
    return {
        "schema_version": "phase5.4/policy-convergence/v1",
        "algorithm": "strictest-policy-v1",
        "final": final,
        "matrix": matrix,
        "blockers": blockers,
    }


async def generate_contract(
    session: AsyncSession,
    application: Application,
    *,
    actor: DemoActor,
    raw_key: str,
) -> ContractRevision:
    existing = await session.scalar(
        select(Contract).where(Contract.application_id == application.id)
    )
    if existing is not None:
        revision = await session.scalar(
            select(ContractRevision)
            .where(ContractRevision.contract_id == existing.id)
            .order_by(ContractRevision.revision_no.desc())
        )
        if revision is None:
            raise ContractLifecycleError("existing Contract has no revision")
        return revision
    if application.status != "approved":
        raise ContractLifecycleError("only an approved Application can generate a Contract")

    snapshot = await session.scalar(
        select(ApplicationSnapshot).where(ApplicationSnapshot.application_id == application.id)
    )
    item = await session.scalar(
        select(ApplicationItem).where(ApplicationItem.application_id == application.id)
    )
    action = await session.scalar(
        select(ApplicationRequestedAction).where(
            ApplicationRequestedAction.application_id == application.id
        )
    )
    selection = await session.scalar(
        select(ApplicationModelSelection).where(
            ApplicationModelSelection.application_id == application.id
        )
    )
    if None in (snapshot, item, action, selection):
        raise ContractLifecycleError("approved Application aggregate is incomplete")
    data_version = await session.get(DataProductVersion, item.data_product_version_id)
    model_version = await session.get(ModelVersion, selection.model_version_id)
    data_product = None if data_version is None else await session.get(DataProduct, data_version.data_product_id)
    model_product = None if model_version is None else await session.get(ModelProduct, model_version.model_product_id)
    space = await session.get(Space, application.space_id)
    if None in (data_version, model_version, data_product, model_product, space):
        raise ContractLifecycleError("fixed product versions are unavailable")

    tasks = list(
        (await session.scalars(select(ReviewTask).where(ReviewTask.application_id == application.id))).all()
    )
    decisions = list(
        (
            await session.scalars(
                select(ReviewDecision).where(
                    ReviewDecision.review_task_id.in_([task.id for task in tasks])
                )
            )
        ).all()
    )
    if len(decisions) != len([task for task in tasks if task.is_required]) or any(
        decision.decision != "approved" for decision in decisions
    ):
        raise ContractLifecycleError("all required Application reviews must remain approved")
    request = action.parameters.get("request")
    if not isinstance(request, dict):
        raise ContractLifecycleError("Application request snapshot is missing")
    convergence = converge_policy(
        request=request,
        data_policy=data_version.default_policy_template,
        model_policy=model_version.default_policy_template,
        decisions=decisions,
    )

    contract_id = uuid5(NAMESPACE_URL, f"medtrust:phase5.4:contract:{application.id}")
    revision_id = uuid5(NAMESPACE_URL, f"medtrust:phase5.4:revision:{application.id}:1")
    contract = Contract(
        id=contract_id,
        space_id=application.space_id,
        application_id=application.id,
        application_snapshot_id=snapshot.id,
        application_snapshot_digest=snapshot.snapshot_digest,
        eligibility_evidence={
            "schema_version": "phase5.4/eligibility/v1",
            "application_status": "approved",
            "application_snapshot_id": str(snapshot.id),
            "application_snapshot_digest": snapshot.snapshot_digest,
            "review_decision_digests": sorted(item.decision_digest for item in decisions),
        },
        eligibility_digest="",
        contract_number=f"CON-{str(application.id).replace('-', '')[:8].upper()}",
        created_by=actor.user_id,
        is_demo=True,
    )
    contract.eligibility_digest = canonical_document_digest(contract.eligibility_evidence)
    session.add(contract)
    now = datetime.now(timezone.utc)
    terms = {
        "schema_version": PRODUCTIZED_TERMS_V2,
        "application": {
            "id": str(application.id),
            "number": application.application_number,
            "snapshot_id": str(snapshot.id),
            "snapshot_digest": snapshot.snapshot_digest,
            "request": request,
        },
        "data_product": {
            "id": str(data_product.id),
            "version_id": str(data_version.id),
            "name": data_product.name,
            "version": data_version.version_label,
            "snapshot_digest": data_version.snapshot_digest,
            "policy_digest": data_version.default_policy_digest,
        },
        "model_product": {
            "id": str(model_product.id),
            "version_id": str(model_version.id),
            "name": model_product.name,
            "version": model_version.version_label,
            "snapshot_digest": model_version.snapshot_digest,
            "policy_digest": model_version.default_policy_digest,
            "registry_digest": selection.registry_digest,
        },
        "policy_convergence": convergence,
        "security_profile": {
            "schema_version": "medtrust.contract-security-profile/v1",
            "profile_version": CONTRACT_SECURITY_PROFILE_V1,
            "canonical_schema_version": CONTRACT_CANONICAL_V2,
            "policy_schema_version": POLICY_CANONICAL_V2,
            "conflict_strategy": "deny_overrides",
            "validation_scopes": [
                "contract.confirm",
                "contract.activate",
                "execution.readiness",
            ],
            "access_policy_scope": "catalog_and_application",
            "usage_policy_scope": "contract_and_execution",
        },
        "assurance": {
            "hard_isolation": False,
            "clinical_use": False,
            "ca_or_reliable_electronic_signature": False,
            "confirmation_notice": "当前为平台内结构化确认记录，不等同于CA数字证书、可靠电子签名或线下法律意见。",
        },
        "next_step_after_activation": "waiting_for_data_and_model_readiness",
    }
    revision = ContractRevision(
        id=revision_id,
        contract_id=contract.id,
        revision_no=1,
        name=f"{application.application_number} 数字合约",
        summary="固定申请、数据版本、模型版本、四方主体与最严格策略。",
        terms_schema_version=PRODUCTIZED_TERMS_V2,
        terms_document=terms,
        terms_digest=canonical_document_digest(terms),
        status="draft",
        signing_mode="multi_party",
        effective_from=now,
        effective_until=now + timedelta(days=max(1, convergence["final"]["valid_days"])),
        created_by=actor.user_id,
    )
    session.add(revision)
    await session.flush()

    party_specs = (
        (application.applicant_organization_id, "data_requester", 1),
        (application.provider_organization_id, "data_provider", 2),
        (selection.model_provider_organization_id, "model_provider", 3),
        (space.operator_organization_id, "operator_witness", 4),
    )
    parties: dict[str, ContractParty] = {}
    for organization_id, role, order in party_specs:
        organization = await session.get(Organization, organization_id)
        party = ContractParty(
            id=uuid5(NAMESPACE_URL, f"medtrust:phase5.4:party:{revision.id}:{role}"),
            contract_revision_id=revision.id,
            organization_id=organization_id,
            party_role=role,
            signing_order=order,
            is_required=True,
            party_name_snapshot=organization.display_name if organization else str(organization_id),
            identity_snapshot={
                "schema_version": "phase5.4/party/v1",
                "organization_id": str(organization_id),
                "role": role,
                "is_demo": True,
            },
            created_by=actor.user_id,
        )
        session.add(party)
        parties[role] = party
    await session.flush()

    scope = request.get("data_scope", {})
    contract_object = ContractObject(
        id=uuid5(NAMESPACE_URL, f"medtrust:phase5.4:data-object:{revision.id}"),
        contract_revision_id=revision.id,
        data_product_version_id=data_version.id,
        product_snapshot_digest=data_version.snapshot_digest,
        product_name_snapshot=data_product.name,
        authorized_scope=scope,
        authorized_scope_digest=canonical_document_digest(scope),
        position_no=1,
        created_by=actor.user_id,
    )
    session.add(contract_object)
    model_scope = {
        "model_version_id": str(model_version.id),
        "registry_digest": selection.registry_digest,
        "fixed_version": True,
        "weight_export": False,
    }
    model_object = ContractModelObject(
        id=uuid5(NAMESPACE_URL, f"medtrust:phase5.4:model-object:{revision.id}"),
        contract_revision_id=revision.id,
        model_version_id=model_version.id,
        model_snapshot_digest=model_version.snapshot_digest,
        model_name_snapshot=model_product.name,
        authorized_scope=model_scope,
        authorized_scope_digest=canonical_document_digest(model_scope),
        created_by=actor.user_id,
    )
    session.add(model_object)
    await session.flush()

    policy_specs = (
        ("permit-controlled-compute", "permission", "permit", "execute_controlled_compute", 100),
        ("permit-approved-output", "permission", "permit", "export_artifact", 90),
        ("deny-raw-data", "prohibition", "deny", "export_raw_data", 1000),
        ("deny-reidentify", "prohibition", "deny", "reidentify_subject", 1000),
        ("deny-redistribute", "prohibition", "deny", "redistribute_data", 1000),
        ("require-audit", "obligation", "require", "write_audit_log", 900),
    )
    policies: dict[str, Policy] = {}
    requester_party = parties["data_requester"]
    for code, policy_type, effect, action_code, priority in policy_specs:
        policy = Policy(
            id=uuid5(NAMESPACE_URL, f"medtrust:phase5.4:policy:{revision.id}:{code}"),
            contract_revision_id=revision.id,
            policy_code=code,
            policy_type=policy_type,
            effect=effect,
            subject_contract_party_id=requester_party.id,
            contract_object_id=contract_object.id,
            action_code=action_code,
            priority=priority,
            created_by=actor.user_id,
        )
        session.add(policy)
        policies[code] = policy
    await session.flush()
    final = convergence["final"]
    purpose_code = request.get("profile", {}).get("purpose_code")
    if not isinstance(purpose_code, str) or not purpose_code:
        raise ContractLifecycleError("approved Application purpose is missing")
    constraints = {
        "permit-controlled-compute": [
            ("purpose_code", "in", [purpose_code], None),
            ("algorithm_digest", "eq", model_version.model_digest, None),
            ("run_count", "lte", final["run_count"], "count"),
            ("effective_until", "before", revision.effective_until.isoformat().replace("+00:00", "Z"), None),
            ("environment_mode", "eq", "controlled_compute", None),
        ],
        "permit-approved-output": [
            ("output_type", "in", ["aggregate_statistics"], None),
            ("output_review_required", "eq", True, None),
        ],
        "require-audit": [("audit_level", "gte", "full", None)],
    }
    for code, rows in constraints.items():
        for position, (name, operator, value, unit) in enumerate(rows, 1):
            session.add(
                PolicyConstraint(
                    policy_id=policies[code].id,
                    constraint_name=name,
                    operator=operator,
                    value=value,
                    unit=unit,
                    position_no=position,
                )
            )
    await session.flush()

    connector = await session.scalar(
        select(Connector).where(
            Connector.space_id == application.space_id,
            Connector.owner_organization_id == application.provider_organization_id,
            Connector.verification_status == "verified",
            Connector.runtime_status == "online",
        )
    )
    if connector is None:
        raise ContractLifecycleError(
            "contract execution connector is unavailable"
        )
    capabilities = {
        row.capability_code: row
        for row in (
            await session.scalars(
                select(ConnectorCapability).where(
                    ConnectorCapability.connector_id == connector.id,
                    ConnectorCapability.status == "verified",
                )
            )
        ).all()
    }
    binding_requirements = (
        (
            "permit-controlled-compute",
            "compute_executor",
            "controlled_compute_execution",
        ),
        (
            "permit-approved-output",
            "egress_controller",
            "egress_policy_enforcement",
        ),
        (
            "deny-raw-data",
            "egress_controller",
            "egress_policy_enforcement",
        ),
        (
            "deny-reidentify",
            "compute_executor",
            "controlled_compute_execution",
        ),
        (
            "deny-reidentify",
            "egress_controller",
            "egress_policy_enforcement",
        ),
        (
            "deny-redistribute",
            "egress_controller",
            "egress_policy_enforcement",
        ),
        (
            "require-audit",
            "audit_evidence_emitter",
            "audit_evidence_emit",
        ),
    )
    bindings: list[PolicyExecutionBinding] = []
    for policy_code, execution_role, capability_code in binding_requirements:
        capability = capabilities.get(capability_code)
        if capability is None or capability.capability_version != "1.0":
            raise ContractLifecycleError(
                f"contract execution capability is unavailable: {capability_code}"
            )
        receipt = {
            "schema_version": "phase5.5/contract-binding/v1",
            "contract_revision_id": str(revision.id),
            "policy_id": str(policies[policy_code].id),
            "connector_id": str(connector.id),
            "execution_role": execution_role,
            "capability_code": capability_code,
            "capability_version": capability.capability_version,
        }
        binding = PolicyExecutionBinding(
            id=uuid5(
                NAMESPACE_URL,
                (
                    "medtrust:phase5.5:binding:"
                    f"{revision.id}:{policies[policy_code].id}:{execution_role}"
                ),
            ),
            policy_id=policies[policy_code].id,
            connector_id=connector.id,
            execution_role=execution_role,
            required_capability_code=capability_code,
            required_capability_version=capability.capability_version,
            is_required=True,
            deployment_status="accepted",
            deployed_at=now,
            acknowledged_at=now,
            receipt_digest=canonical_document_digest(receipt),
        )
        session.add(binding)
        bindings.append(binding)
    await session.flush()

    for policy in policies.values():
        await session.refresh(policy, attribute_names=["constraints"])
        policy.policy_digest = canonical_document_digest(
            build_policy_digest_document(policy, schema_version=POLICY_CANONICAL_V2)
        )
    await session.flush()

    revision.handoff_guard_evidence = {
        "schema_version": "medtrust.contract-handoff/v2",
        "phase": "contract_confirmation_only",
        "static_execution_bindings_frozen": True,
        "runtime_authorization_deferred": True,
    }
    revision.handoff_guard_digest = canonical_document_digest(revision.handoff_guard_evidence)
    content = build_contract_canonical_document_v2(
        contract_id=str(contract.id),
        revision_no=revision.revision_no,
        signing_mode=revision.signing_mode,
        supersedes_revision_id=(
            str(revision.supersedes_revision_id)
            if revision.supersedes_revision_id
            else None
        ),
        effective_from=revision.effective_from.isoformat(),
        effective_until=revision.effective_until.isoformat(),
        terms_digest=revision.terms_digest,
        eligibility_digest=contract.eligibility_digest,
        handoff_guard_digest=revision.handoff_guard_digest,
        parties=[
            {
                "id": str(item.id),
                "organization_id": str(item.organization_id),
                "role": item.party_role,
                "signing_order": item.signing_order,
                "required": item.is_required,
                "identity_snapshot_digest": canonical_document_digest(
                    item.identity_snapshot
                ),
            }
            for item in parties.values()
        ],
        data_objects=[
            {
                "id": str(contract_object.id),
                "data_product_version_id": str(contract_object.data_product_version_id),
                "product_snapshot_digest": contract_object.product_snapshot_digest,
                "authorized_scope_digest": contract_object.authorized_scope_digest,
            }
        ],
        model_object={
            "id": str(model_object.id),
            "model_version_id": str(model_object.model_version_id),
            "model_snapshot_digest": model_object.model_snapshot_digest,
            "authorized_scope_digest": model_object.authorized_scope_digest,
        },
        policies=[
            {"id": str(item.id), "digest": item.policy_digest}
            for item in policies.values()
        ],
        binding_specs=[
            {
                "policy_id": str(item.policy_id),
                "connector_id": str(item.connector_id),
                "execution_role": item.execution_role,
                "required_capability_code": item.required_capability_code,
                "required_capability_version": item.required_capability_version,
                "is_required": item.is_required,
            }
            for item in bindings
        ],
    )
    revision.content_digest = canonical_document_digest(content)
    revision.status = "proposed"
    revision.proposed_at = now
    revision.row_version += 1
    revision._proposal_validated = True
    await session.flush()

    await _append(
        session,
        actor=actor,
        action="draft-generated",
        raw_key=raw_key,
        space_id=application.space_id,
        revision_id=revision.id,
        event_type="contract.draft.generated",
        evidence={
            "schema_version": "phase5.4/contract-draft-generated/v1",
            "contract_id": str(contract.id),
            "contract_number": contract.contract_number,
            "application_id": str(application.id),
            "revision_id": str(revision.id),
            "revision_no": 1,
            "content_digest": revision.content_digest,
        },
    )
    await _append(
        session,
        actor=actor,
        action="policy-converged",
        raw_key=f"{raw_key}:policy",
        space_id=application.space_id,
        revision_id=revision.id,
        event_type="contract.policy.converged",
        evidence={
            "schema_version": "phase5.4/policy-converged/v1",
            "contract_id": str(contract.id),
            "content_digest": revision.content_digest,
            "policy_convergence_digest": canonical_json_digest_v1(convergence),
            "blockers": convergence["blockers"],
        },
    )
    return revision


async def confirm_contract(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    actor: DemoActor,
    raw_key: str,
    acknowledged_digest: str,
    declaration_accepted: bool,
) -> ContractSignature:
    if not declaration_accepted:
        raise ContractLifecycleError("internal confirmation declaration must be accepted")
    if revision.content_digest != acknowledged_digest:
        raise ContractLifecycleError("Contract digest does not match the current version")
    security = await validate_contract_security(session, revision, stage="confirm")
    if security["overall"] == "BLOCKER":
        raise ContractLifecycleError(security_blocker_message(security))
    party = await session.scalar(
        select(ContractParty).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.organization_id == actor.organization_id,
        )
    )
    if party is None:
        raise ContractLifecycleError("actor organization is not a Contract party")
    if party.party_role == "operator_witness":
        outstanding = await session.scalar(
            select(ContractParty.id)
            .where(
                ContractParty.contract_revision_id == revision.id,
                ContractParty.is_required.is_(True),
                ContractParty.party_role != "operator_witness",
                ~ContractParty.id.in_(
                    select(ContractSignature.contract_party_id).where(
                        ContractSignature.contract_revision_id == revision.id,
                        ContractSignature.signed_content_digest == revision.content_digest,
                    )
                ),
            )
            .limit(1)
        )
        if outstanding is not None:
            raise ContractLifecycleError("platform confirmation must be last")
    existing = await session.scalar(
        select(ContractSignature).where(
            ContractSignature.contract_revision_id == revision.id,
            ContractSignature.contract_party_id == party.id,
        )
    )
    if existing is not None:
        if existing.signed_content_digest != acknowledged_digest:
            raise ContractLifecycleError("existing confirmation belongs to another digest")
        return existing
    signature = await sign_contract_revision(
        session,
        revision,
        contract_party_id=party.id,
        signer_organization_id=actor.organization_id,
        signer_user_id=actor.user_id,
        signature_value_ref=f"internal-confirmation://phase5.4/{revision.id}/{party.id}",
    )
    contract = await session.get(Contract, revision.contract_id)
    await _append(
        session,
        actor=actor,
        action=f"confirm:{party.party_role}",
        raw_key=raw_key,
        space_id=contract.space_id,
        revision_id=revision.id,
        event_type="contract.revision.signed",
        evidence={
            "schema_version": "phase5.4/internal-confirmation/v1",
            "contract_id": str(contract.id),
            "contract_party_id": str(party.id),
            "party_role": party.party_role,
            "contract_revision_id": str(revision.id),
            "signed_content_digest": acknowledged_digest,
            "security_validation": security,
            "ca_backed": False,
            "legally_reliable_electronic_signature": False,
        },
    )
    return signature


async def activate_productized_contract(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    actor: DemoActor,
    raw_key: str,
) -> ContractRevision:
    if revision.status == "active":
        return revision
    if revision.status != "signed":
        raise ContractLifecycleError("all four required parties must confirm before activation")
    security = await validate_contract_security(session, revision, stage="activate")
    if security["overall"] != "PASS":
        raise ContractLifecycleError(security_blocker_message(security))
    contract = await session.get(Contract, revision.contract_id)
    application = None if contract is None else await session.get(Application, contract.application_id)
    if contract is None or application is None or application.status != "approved":
        raise ContractLifecycleError("source Application is no longer approved")
    convergence = revision.terms_document.get("policy_convergence", {})
    if convergence.get("blockers"):
        raise ContractLifecycleError("Contract policy has unresolved blockers")
    now = datetime.now(timezone.utc)
    if revision.effective_from and now < revision.effective_from:
        raise ContractLifecycleError("Contract effective window has not started")
    if revision.effective_until and now >= revision.effective_until:
        raise ContractLifecycleError("Contract has expired")
    signatures = list(
        (
            await session.scalars(
                select(ContractSignature).where(
                    ContractSignature.contract_revision_id == revision.id,
                    ContractSignature.signed_content_digest == revision.content_digest,
                    ContractSignature.verification_status == "verified",
                )
            )
        ).all()
    )
    required = set(
        (
            await session.scalars(
                select(ContractParty.id).where(
                    ContractParty.contract_revision_id == revision.id,
                    ContractParty.is_required.is_(True),
                )
            )
        ).all()
    )
    if required != {item.contract_party_id for item in signatures}:
        raise ContractLifecycleError("required confirmations are incomplete or inconsistent")
    revision.status = "active"
    revision.activated_at = now
    revision.row_version += 1
    revision._activation_validated = True
    await session.flush()
    await _append(
        session,
        actor=actor,
        action="activate",
        raw_key=raw_key,
        space_id=contract.space_id,
        revision_id=revision.id,
        event_type="contract.revision.activated",
        evidence={
            "schema_version": "phase5.4/contract-activated/v1",
            "contract_id": str(contract.id),
            "application_id": str(application.id),
            "contract_revision_id": str(revision.id),
            "content_digest": revision.content_digest,
            "security_decision_id": security["decision_id"],
            "security_snapshot_digest": security["snapshot_digest"],
            "security_profile_version": security["profile_version"],
            "security_validation": security,
            "activated_at": now.isoformat(),
            "next_step": "waiting_for_data_and_model_readiness",
            "compute_job_created": False,
        },
    )
    return revision
