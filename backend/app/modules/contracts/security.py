from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.applications.models import (
    Application,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
    ApplicationSnapshot,
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
    PolicyExecutionBinding,
)
from app.modules.contracts.services import (
    canonical_document_digest,
    capability_parameters_satisfy_v1,
    validate_policy_constraint_v1,
)
from app.modules.identity.models import Organization
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole


CONTRACT_SECURITY_VALIDATION_V1 = "medtrust.contract-security-validation/v1"
CONTRACT_SECURITY_PROFILE_V1 = "medtrust.controlled-compute-usage-policy/v1"
CONTRACT_CANONICAL_V2 = "medtrust.contract-canonical/v2"
POLICY_CANONICAL_V2 = "medtrust.contract-policy/v2"
PRODUCTIZED_TERMS_V1 = "phase5.4/structured-contract/v1"
PRODUCTIZED_TERMS_V2 = "phase5.4/structured-contract/v2"

CHECK_ORDER = (
    "terms_integrity",
    "party_authority",
    "asset_integrity",
    "policy_integrity",
    "content_integrity",
    "effective_window",
    "signature_binding",
    "execution_binding",
)
REQUIRED_POLICY_RULES = {
    ("permission", "permit", "execute_controlled_compute"),
    ("permission", "permit", "export_artifact"),
    ("prohibition", "deny", "export_raw_data"),
    ("prohibition", "deny", "reidentify_subject"),
    ("prohibition", "deny", "redistribute_data"),
    ("obligation", "require", "write_audit_log"),
}
REQUIRED_CAPABILITIES = {
    "controlled_compute_execution",
    "egress_policy_enforcement",
    "audit_evidence_emit",
}
PARTY_ROLES = {
    "data_requester": {"data_requester"},
    "data_provider": {"data_provider"},
    "model_provider": {"model_provider"},
    "operator_witness": {"operator", "space_operator"},
    "provider": {"provider"},
    "consumer": {"consumer"},
    "service_provider": {"service_provider"},
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _check(
    code: str,
    result: str,
    message: str,
    *,
    expected: Any = None,
    actual: Any = None,
    source: str | None = None,
) -> dict[str, Any]:
    row = {"code": code, "result": result, "message": message}
    if expected is not None:
        row["expected"] = expected
    if actual is not None:
        row["actual"] = actual
    if source is not None:
        row["source"] = source
    return row


def build_policy_digest_document(
    policy: Any, *, schema_version: str = POLICY_CANONICAL_V2
) -> dict[str, Any]:
    constraints = sorted(policy.constraints, key=lambda item: item.position_no)
    if schema_version == PRODUCTIZED_TERMS_V1:
        return {
            "policy_code": policy.policy_code,
            "type": policy.policy_type,
            "effect": policy.effect,
            "action": policy.action_code,
            "priority": policy.priority,
            "constraints": [
                {
                    "name": item.constraint_name,
                    "operator": item.operator,
                    "value": item.value,
                    "unit": item.unit,
                    "position": item.position_no,
                }
                for item in constraints
            ],
        }
    document_schema = "1.0" if schema_version == "1.0" else POLICY_CANONICAL_V2
    return {
        "schema_version": document_schema,
        "policy_code": policy.policy_code,
        "policy_type": policy.policy_type,
        "effect": policy.effect,
        "subject_contract_party_id": str(policy.subject_contract_party_id),
        "contract_object_id": str(policy.contract_object_id),
        "action_code": policy.action_code,
        "priority": policy.priority,
        "constraints": [
            {
                "position_no": item.position_no,
                "constraint_name": item.constraint_name,
                "operator": item.operator,
                "value": item.value,
                "unit": item.unit,
            }
            for item in constraints
        ],
    }


def build_contract_canonical_document_v2(
    *,
    contract_id: str,
    revision_no: int,
    signing_mode: str,
    supersedes_revision_id: str | None,
    effective_from: str | None,
    effective_until: str | None,
    terms_digest: str,
    eligibility_digest: str,
    handoff_guard_digest: str,
    parties: list[dict[str, Any]],
    data_objects: list[dict[str, Any]],
    model_object: dict[str, Any] | None,
    policies: list[dict[str, Any]],
    binding_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_CANONICAL_V2,
        "contract_id": contract_id,
        "revision_no": revision_no,
        "signing_mode": signing_mode,
        "supersedes_revision_id": supersedes_revision_id,
        "effective_from": effective_from,
        "effective_until": effective_until,
        "terms_digest": terms_digest,
        "eligibility_digest": eligibility_digest,
        "handoff_guard_digest": handoff_guard_digest,
        "parties": sorted(
            [dict(item) for item in parties],
            key=lambda item: (item["role"], item["id"]),
        ),
        "data_objects": sorted(
            [dict(item) for item in data_objects], key=lambda item: item["id"]
        ),
        "model_object": None if model_object is None else dict(model_object),
        "policies": sorted(
            [dict(item) for item in policies], key=lambda item: item["id"]
        ),
        "binding_specs": sorted(
            [dict(item) for item in binding_specs],
            key=lambda item: (
                item["policy_id"],
                item["execution_role"],
                item["connector_id"],
            ),
        ),
    }


def build_contract_security_decision(
    *,
    stage: str,
    revision_id: str,
    content_digest: str | None,
    summary: dict[str, Any],
    checks: Iterable[dict[str, Any]],
    checked_at: datetime,
) -> dict[str, Any]:
    ordered = sorted(
        [dict(item) for item in checks],
        key=lambda item: (
            CHECK_ORDER.index(item["code"])
            if item["code"] in CHECK_ORDER
            else len(CHECK_ORDER),
            item["code"],
        ),
    )
    results = {item["result"] for item in ordered}
    overall = "BLOCKER" if "BLOCKER" in results else (
        "PENDING" if "PENDING" in results else "PASS"
    )
    snapshot = {
        "schema_version": CONTRACT_SECURITY_VALIDATION_V1,
        "profile_version": CONTRACT_SECURITY_PROFILE_V1,
        "stage": stage,
        "revision_id": revision_id,
        "content_digest": content_digest,
        "overall": overall,
        "summary": summary,
        "checks": ordered,
    }
    snapshot_digest = canonical_document_digest(snapshot)
    return {
        **snapshot,
        "decision_id": str(
            uuid5(NAMESPACE_URL, f"medtrust:contract-security:{snapshot_digest}")
        ),
        "snapshot_digest": snapshot_digest,
        "checked_at": _as_utc(checked_at).isoformat(),
    }


def _legacy_productized_document(
    *,
    contract: Contract,
    revision: ContractRevision,
    parties: list[ContractParty],
    data_objects: list[ContractObject],
    model_object: Any | None,
    policies: list[Policy],
) -> dict[str, Any]:
    return {
        "schema_version": "phase5.4/contract-canonical/v1",
        "contract_id": str(contract.id),
        "revision_no": revision.revision_no,
        "terms_digest": revision.terms_digest,
        "eligibility_digest": contract.eligibility_digest,
        "parties": sorted(
            [
                {"organization_id": str(item.organization_id), "role": item.party_role}
                for item in parties
            ],
            key=lambda item: item["role"],
        ),
        "data_snapshot_digest": (
            data_objects[0].product_snapshot_digest if data_objects else None
        ),
        "model_snapshot_digest": (
            model_object.model_snapshot_digest if model_object is not None else None
        ),
        "policy_digests": sorted(item.policy_digest for item in policies),
        "handoff_guard_digest": revision.handoff_guard_digest,
    }


def _core_v1_document(
    *,
    contract: Contract,
    revision: ContractRevision,
    parties: list[ContractParty],
    data_objects: list[ContractObject],
    model_object: Any | None,
    policies: list[Policy],
    binding_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "contract_id": str(contract.id),
        "revision_no": revision.revision_no,
        "terms_digest": revision.terms_digest,
        "handoff_guard_digest": revision.handoff_guard_digest,
        "parties": sorted(
            [
                {
                    "id": str(item.id),
                    "organization_id": str(item.organization_id),
                    "role": item.party_role,
                }
                for item in parties
            ],
            key=lambda item: (item["role"], item["id"]),
        ),
        "objects": sorted(
            [
                {
                    "id": str(item.id),
                    "product_snapshot_digest": item.product_snapshot_digest,
                    "authorized_scope_digest": item.authorized_scope_digest,
                }
                for item in data_objects
            ],
            key=lambda item: item["id"],
        ),
        "model_object": (
            {
                "id": str(model_object.id),
                "model_version_id": str(model_object.model_version_id),
                "model_snapshot_digest": model_object.model_snapshot_digest,
                "authorized_scope_digest": model_object.authorized_scope_digest,
            }
            if model_object is not None
            else None
        ),
        "policy_digests": sorted(item.policy_digest for item in policies),
        "binding_specs": sorted(
            binding_specs,
            key=lambda item: (
                item["policy_id"],
                item["execution_role"],
                item["connector_id"],
            ),
        ),
    }


def _binding_spec(binding: PolicyExecutionBinding) -> dict[str, Any]:
    return {
        "policy_id": str(binding.policy_id),
        "connector_id": str(binding.connector_id),
        "execution_role": binding.execution_role,
        "required_capability_code": binding.required_capability_code,
        "required_capability_version": binding.required_capability_version,
        "is_required": binding.is_required,
    }


def _binding_receipt_digests(
    binding: PolicyExecutionBinding, *, revision_id: str
) -> set[str]:
    base = {
        "contract_revision_id": revision_id,
        "policy_id": str(binding.policy_id),
        "connector_id": str(binding.connector_id),
        "execution_role": binding.execution_role,
        "capability_code": binding.required_capability_code,
        "capability_version": binding.required_capability_version,
    }
    return {
        canonical_document_digest(
            {"schema_version": "phase5.5/contract-binding/v1", **base}
        ),
        canonical_document_digest(
            {
                "schema_version": "phase5.5/binding-acceptance/v1",
                "contract_revision_id": revision_id,
                "policy_id": str(binding.policy_id),
                "binding_id": str(binding.id),
                "connector_id": str(binding.connector_id),
                "execution_role": binding.execution_role,
                "capability_code": binding.required_capability_code,
                "capability_version": binding.required_capability_version,
            }
        ),
    }


def _v2_document(
    *,
    contract: Contract,
    revision: ContractRevision,
    parties: list[ContractParty],
    data_objects: list[ContractObject],
    model_object: Any | None,
    policies: list[Policy],
    binding_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    return build_contract_canonical_document_v2(
        contract_id=str(contract.id),
        revision_no=revision.revision_no,
        signing_mode=revision.signing_mode,
        supersedes_revision_id=(
            str(revision.supersedes_revision_id)
            if revision.supersedes_revision_id
            else None
        ),
        effective_from=(
            _as_utc(revision.effective_from).isoformat()
            if revision.effective_from
            else None
        ),
        effective_until=(
            _as_utc(revision.effective_until).isoformat()
            if revision.effective_until
            else None
        ),
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
            for item in parties
        ],
        data_objects=[
            {
                "id": str(item.id),
                "data_product_version_id": str(item.data_product_version_id),
                "product_snapshot_digest": item.product_snapshot_digest,
                "authorized_scope_digest": item.authorized_scope_digest,
            }
            for item in data_objects
        ],
        model_object=(
            {
                "id": str(model_object.id),
                "model_version_id": str(model_object.model_version_id),
                "model_snapshot_digest": model_object.model_snapshot_digest,
                "authorized_scope_digest": model_object.authorized_scope_digest,
            }
            if model_object is not None
            else None
        ),
        policies=[
            {"id": str(item.id), "digest": item.policy_digest} for item in policies
        ],
        binding_specs=binding_specs,
    )


async def validate_contract_security(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    stage: str = "display",
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    from app.modules.marketplace.models import (
        ContractModelObject,
        ModelProduct,
        ModelVersion,
    )

    now = _as_utc(checked_at or datetime.now(timezone.utc))
    contract = await session.get(Contract, revision.contract_id)
    if contract is None:
        return build_contract_security_decision(
            stage=stage,
            revision_id=str(revision.id),
            content_digest=revision.content_digest,
            summary={},
            checks=[_check("terms_integrity", "BLOCKER", "合约主记录不存在")],
            checked_at=now,
        )

    application = await session.get(Application, contract.application_id)
    snapshot = await session.get(ApplicationSnapshot, contract.application_snapshot_id)
    space = await session.get(Space, contract.space_id)
    parties = list(
        (
            await session.scalars(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    signatures = list(
        (
            await session.scalars(
                select(ContractSignature).where(
                    ContractSignature.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    data_objects = list(
        (
            await session.scalars(
                select(ContractObject).where(
                    ContractObject.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    model_object = await session.scalar(
        select(ContractModelObject).where(
            ContractModelObject.contract_revision_id == revision.id
        )
    )
    policies = list(
        (
            await session.scalars(
                select(Policy).where(Policy.contract_revision_id == revision.id)
            )
        ).all()
    )
    bindings: list[PolicyExecutionBinding] = []
    for policy in policies:
        await session.refresh(policy, attribute_names=["constraints", "execution_bindings"])
        bindings.extend(policy.execution_bindings)
    binding_specs = [_binding_spec(item) for item in bindings if item.is_required]
    checks: list[dict[str, Any]] = []

    terms_valid = False
    computed_terms_digest: str | None = None
    try:
        if isinstance(revision.terms_document, dict):
            computed_terms_digest = canonical_document_digest(
                revision.terms_document
            )
        terms_valid = (
            isinstance(revision.terms_document, dict)
            and computed_terms_digest == revision.terms_digest
            and revision.terms_schema_version
            in {PRODUCTIZED_TERMS_V1, PRODUCTIZED_TERMS_V2, "1.0"}
        )
    except (TypeError, ValueError):
        terms_valid = False
    checks.append(
        _check(
            "terms_integrity",
            "PASS" if terms_valid else "BLOCKER",
            "冻结条款摘要一致" if terms_valid else "冻结条款摘要或 schema 不一致",
            expected=revision.terms_digest,
            actual=computed_terms_digest,
            source="ContractRevision.terms_document",
        )
    )

    expected_party_roles = {
        "data_requester",
        "data_provider",
        "model_provider",
        "operator_witness",
    }
    party_errors: list[str] = []
    if revision.terms_schema_version.startswith("phase5.4/") and not expected_party_roles <= {
        item.party_role for item in parties if item.is_required
    }:
        party_errors.append("缺少必需合约主体")
    for party in parties:
        organization = await session.get(Organization, party.organization_id)
        participant = await session.scalar(
            select(SpaceParticipant).where(
                SpaceParticipant.space_id == contract.space_id,
                SpaceParticipant.organization_id == party.organization_id,
            )
        )
        expected_roles = PARTY_ROLES.get(party.party_role, set())
        participant_role = None
        if participant is not None and expected_roles:
            participant_role = await session.scalar(
                select(SpaceParticipantRole).where(
                    SpaceParticipantRole.space_participant_id == participant.id,
                    SpaceParticipantRole.role_code.in_(expected_roles),
                )
            )
        if (
            organization is None
            or organization.status != "active"
            or participant is None
            or participant.admission_status != "admitted"
            or participant_role is None
        ):
            party_errors.append(f"主体 {party.party_role} 当前无有效准入角色")
    checks.append(
        _check(
            "party_authority",
            "PASS" if not party_errors else "BLOCKER",
            "四方组织与空间角色仍有效" if not party_errors else "；".join(party_errors),
            expected="active organization, admitted participant and matching role",
            actual={"required_parties": len([item for item in parties if item.is_required])},
            source="Organization and SpaceParticipantRole",
        )
    )

    asset_errors: list[str] = []
    eligibility_valid = False
    try:
        eligibility_valid = (
            canonical_document_digest(contract.eligibility_evidence)
            == contract.eligibility_digest
        )
    except (TypeError, ValueError):
        eligibility_valid = False
    if (
        application is None
        or application.status != "approved"
        or application.space_id != contract.space_id
        or snapshot is None
        or snapshot.application_id != contract.application_id
        or snapshot.snapshot_digest != contract.application_snapshot_digest
        or not eligibility_valid
        or space is None
        or space.status != "active"
    ):
        asset_errors.append("申请、快照、资格证据或空间状态已失效")
    terms_data = revision.terms_document.get("data_product", {})
    for item in data_objects:
        version = await session.get(DataProductVersion, item.data_product_version_id)
        product = None if version is None else await session.get(DataProduct, version.data_product_id)
        if (
            canonical_document_digest(item.authorized_scope)
            != item.authorized_scope_digest
            or version is None
            or version.status != "approved"
            or version.snapshot_digest != item.product_snapshot_digest
            or product is None
            or product.lifecycle_status != "active"
        ):
            asset_errors.append("固定数据版本或授权范围摘要已失效")
        if terms_data and (
            str(item.data_product_version_id) != str(terms_data.get("version_id"))
            or item.product_snapshot_digest != terms_data.get("snapshot_digest")
        ):
            asset_errors.append("数据对象与冻结条款不一致")
    if not data_objects:
        asset_errors.append("缺少固定数据对象")
    terms_model = revision.terms_document.get("model_product", {})
    model_version = None
    if model_object is None and revision.terms_schema_version.startswith("phase5.4/"):
        asset_errors.append("缺少固定模型对象")
    elif model_object is not None:
        model_version = await session.get(ModelVersion, model_object.model_version_id)
        model_product = (
            None
            if model_version is None
            else await session.get(ModelProduct, model_version.model_product_id)
        )
        if (
            canonical_document_digest(model_object.authorized_scope)
            != model_object.authorized_scope_digest
            or model_version is None
            or model_version.status != "approved"
            or model_version.snapshot_digest != model_object.model_snapshot_digest
            or model_product is None
            or model_product.lifecycle_status != "active"
        ):
            asset_errors.append("固定模型版本或授权范围摘要已失效")
        if terms_model and (
            str(model_object.model_version_id) != str(terms_model.get("version_id"))
            or model_object.model_snapshot_digest != terms_model.get("snapshot_digest")
        ):
            asset_errors.append("模型对象与冻结条款不一致")
    checks.append(
        _check(
            "asset_integrity",
            "PASS" if not asset_errors else "BLOCKER",
            "申请、数据、模型和授权范围仍与冻结版本一致"
            if not asset_errors
            else "；".join(dict.fromkeys(asset_errors)),
            source="ApplicationSnapshot, ContractObject and version registries",
        )
    )

    policy_errors: list[str] = []
    actual_rules = {
        (item.policy_type, item.effect, item.action_code) for item in policies
    }
    if not REQUIRED_POLICY_RULES <= actual_rules:
        policy_errors.append("缺少最低许可、禁止或审计规则")
    policy_schema = (
        PRODUCTIZED_TERMS_V1
        if revision.terms_schema_version == PRODUCTIZED_TERMS_V1
        else (POLICY_CANONICAL_V2 if revision.terms_schema_version == PRODUCTIZED_TERMS_V2 else "1.0")
    )
    for policy in policies:
        try:
            for constraint in policy.constraints:
                validate_policy_constraint_v1(constraint)
            expected_digest = canonical_document_digest(
                build_policy_digest_document(policy, schema_version=policy_schema)
            )
        except (TypeError, ValueError) as exc:
            policy_errors.append(f"策略 {policy.policy_code} 约束无效: {exc}")
            continue
        if policy.policy_digest != expected_digest:
            policy_errors.append(f"策略 {policy.policy_code} 摘要不一致")
    convergence = revision.terms_document.get("policy_convergence", {})
    if convergence.get("blockers"):
        policy_errors.append("策略收敛仍存在阻断项")
    converged_policy = (
        convergence.get("final", {}) if isinstance(convergence, dict) else {}
    )
    request = revision.terms_document.get("application", {}).get("request", {})
    profile = request.get("profile", {}) if isinstance(request, dict) else {}
    purpose_code = profile.get("purpose_code") or revision.terms_document.get("purpose")
    purpose_action = None
    if application is not None and purpose_code:
        purpose_action = await session.scalar(
            select(ApplicationRequestedAction).where(
                ApplicationRequestedAction.application_id == application.id,
                ApplicationRequestedAction.action_code == purpose_code,
            )
        )
    if revision.terms_schema_version.startswith("phase5.4/") and (
        not purpose_code or purpose_action is None
    ):
        policy_errors.append("冻结用途与获批申请用途不一致")
    if revision.terms_schema_version == PRODUCTIZED_TERMS_V2:
        controlled_policies = [
            item
            for item in policies
            if item.effect == "permit"
            and item.action_code == "execute_controlled_compute"
        ]
        controlled_policy = (
            controlled_policies[0] if len(controlled_policies) == 1 else None
        )
        purpose_constraints = [
            item
            for item in (controlled_policy.constraints if controlled_policy else [])
            if item.constraint_name == "purpose_code"
        ]
        purpose_values = (
            purpose_constraints[0].value
            if len(purpose_constraints) == 1
            and isinstance(purpose_constraints[0].value, list)
            else []
        )
        if (
            len(purpose_constraints) != 1
            or purpose_code not in purpose_values
        ):
            policy_errors.append("v2 使用策略未显式绑定获批用途")
        algorithm_constraints = [
            item
            for item in (controlled_policy.constraints if controlled_policy else [])
            if item.constraint_name == "algorithm_digest"
        ]
        if (
            len(algorithm_constraints) != 1
            or model_version is None
            or algorithm_constraints[0].operator != "eq"
            or algorithm_constraints[0].value != model_version.model_digest
        ):
            policy_errors.append("v2 使用策略未显式绑定固定模型摘要")
        environment_constraints = [
            item
            for item in (controlled_policy.constraints if controlled_policy else [])
            if item.constraint_name == "environment_mode"
        ]
        if (
            len(environment_constraints) != 1
            or environment_constraints[0].operator != "eq"
            or environment_constraints[0].value != "controlled_compute"
        ):
            policy_errors.append("v2 使用策略未锁定受控计算环境")
        run_constraints = [
            item
            for item in (controlled_policy.constraints if controlled_policy else [])
            if item.constraint_name == "run_count"
        ]
        if (
            len(run_constraints) != 1
            or run_constraints[0].operator != "lte"
            or run_constraints[0].value != converged_policy.get("run_count")
        ):
            policy_errors.append("v2 使用策略未锁定运行次数")
        effective_constraints = [
            item
            for item in (controlled_policy.constraints if controlled_policy else [])
            if item.constraint_name == "effective_until"
        ]
        expected_effective_until = _as_utc(revision.effective_until)
        expected_effective_value = (
            expected_effective_until.isoformat().replace("+00:00", "Z")
            if expected_effective_until
            else None
        )
        if (
            len(effective_constraints) != 1
            or effective_constraints[0].operator != "before"
            or effective_constraints[0].value != expected_effective_value
        ):
            policy_errors.append("v2 使用策略未绑定合约有效期")
    approved_outputs = set()
    if application is not None:
        approved_outputs = set(
            (
                await session.scalars(
                    select(ApplicationRequestedOutputType.output_type).where(
                        ApplicationRequestedOutputType.application_id == application.id
                    )
                )
            ).all()
        )
    output_constraints = [
        item
        for policy in policies
        if policy.action_code == "export_artifact" and policy.effect == "permit"
        for item in policy.constraints
        if item.constraint_name == "output_type"
    ]
    allowed_outputs = set(converged_policy.get("allowed_outputs", []))
    requested_output_files = set(
        request.get("execution", {}).get("requested_outputs", [])
        if isinstance(request.get("execution", {}), dict)
        else []
    )
    constrained_outputs = (
        set(output_constraints[0].value)
        if len(output_constraints) == 1
        and isinstance(output_constraints[0].value, list)
        and all(isinstance(item, str) for item in output_constraints[0].value)
        else set()
    )
    if (
        len(output_constraints) != 1
        or constrained_outputs != approved_outputs
        or not allowed_outputs
        or not allowed_outputs <= requested_output_files
    ):
        policy_errors.append("允许输出未被获批申请范围约束")
    output_review_constraints = [
        item
        for policy in policies
        if policy.action_code == "export_artifact" and policy.effect == "permit"
        for item in policy.constraints
        if item.constraint_name == "output_review_required"
    ]
    if revision.terms_schema_version == PRODUCTIZED_TERMS_V2 and (
        len(output_review_constraints) != 1
        or output_review_constraints[0].operator != "eq"
        or output_review_constraints[0].value is not True
    ):
        policy_errors.append("v2 输出策略未强制结果审核")
    checks.append(
        _check(
            "policy_integrity",
            "PASS" if not policy_errors else "BLOCKER",
            "使用策略完整、可解释并采用禁止优先"
            if not policy_errors
            else "；".join(dict.fromkeys(policy_errors)),
            expected="minimum rules, valid profile and matching policy digests",
            actual={"policy_count": len(policies), "conflict_strategy": "deny_overrides"},
            source="Policy and PolicyConstraint",
        )
    )

    content_valid = False
    handoff_valid = False
    try:
        handoff_valid = (
            canonical_document_digest(revision.handoff_guard_evidence)
            == revision.handoff_guard_digest
        )
        if revision.terms_schema_version == PRODUCTIZED_TERMS_V1:
            content = _legacy_productized_document(
                contract=contract,
                revision=revision,
                parties=parties,
                data_objects=data_objects,
                model_object=model_object,
                policies=policies,
            )
        elif revision.terms_schema_version == PRODUCTIZED_TERMS_V2:
            content = _v2_document(
                contract=contract,
                revision=revision,
                parties=parties,
                data_objects=data_objects,
                model_object=model_object,
                policies=policies,
                binding_specs=binding_specs,
            )
        else:
            frozen_binding_specs = revision.handoff_guard_evidence.get(
                "binding_specs", binding_specs
            )
            content = _core_v1_document(
                contract=contract,
                revision=revision,
                parties=parties,
                data_objects=data_objects,
                model_object=model_object,
                policies=policies,
                binding_specs=frozen_binding_specs,
            )
        computed_content_digest = canonical_document_digest(content)
        content_valid = handoff_valid and computed_content_digest == revision.content_digest
    except (TypeError, ValueError):
        computed_content_digest = None
    if revision.terms_schema_version == PRODUCTIZED_TERMS_V1 and content_valid:
        content_result = "PENDING"
        content_message = "历史 productized v1 未绑定完整主体、范围与执行绑定；新执行应使用 v2 合约"
    elif revision.terms_schema_version == "1.0" and handoff_valid and not content_valid:
        content_result = "PENDING"
        content_message = "历史 v1 canonical 无法完整重放；签名仍绑定已存内容摘要"
    else:
        content_result = "PASS" if content_valid else "BLOCKER"
        content_message = (
            "主体、对象、策略和静态执行绑定均受当前摘要保护"
            if content_valid
            else "合约内容摘要或交接摘要不一致"
        )
    checks.append(
        _check(
            "content_integrity",
            content_result,
            content_message,
            expected=revision.content_digest,
            actual=computed_content_digest,
            source="schema-versioned canonical contract document",
        )
    )

    effective_from = _as_utc(revision.effective_from)
    effective_until = _as_utc(revision.effective_until)
    legacy_open_window = (
        revision.terms_schema_version == "1.0"
        and effective_from is None
        and effective_until is None
        and revision.status in {"signed", "active", "suspended"}
    )
    if legacy_open_window:
        window_state = "legacy_open_window"
        window_result = "PENDING"
        window_message = "历史 v1 合约未冻结有效期；新执行应使用 v2 限期合约"
    elif (
        effective_from is None
        or effective_until is None
        or effective_until <= effective_from
    ):
        window_state = "invalid_window"
        window_result = "BLOCKER"
        window_message = "合约有效期结构不完整或结束时间不晚于开始时间"
    elif now >= effective_until:
        window_state = "expired"
        window_result = "BLOCKER"
        window_message = "合约已经过期"
    elif now < effective_from:
        window_state = "before_start"
        window_result = "PENDING" if stage in {"display", "confirm"} else "BLOCKER"
        window_message = (
            "合约有效期结构正确，将在约定时间生效"
            if window_result == "PENDING"
            else "合约尚未到生效时间"
        )
    else:
        window_state = "within_window"
        window_result = "PASS"
        window_message = "合约处于有效时间窗"
    checks.append(
        _check(
            "effective_window",
            window_result,
            window_message,
            expected={
                "from": effective_from.isoformat() if effective_from else None,
                "until": effective_until.isoformat() if effective_until else None,
            },
            actual=window_state,
            source="ContractRevision effective window",
        )
    )

    required_ids = {item.id for item in parties if item.is_required}
    signed_ids = {
        item.contract_party_id
        for item in signatures
        if item.verification_status == "verified"
        and item.signed_content_digest == revision.content_digest
    }
    invalid_signatures = [
        item
        for item in signatures
        if item.verification_status != "verified"
        or item.signed_content_digest != revision.content_digest
    ]
    missing_ids = required_ids - signed_ids
    if invalid_signatures:
        signature_result = "BLOCKER"
        signature_message = "存在未验证或未绑定当前摘要的确认记录"
    elif missing_ids:
        signature_result = "BLOCKER" if stage in {"activate", "execute"} else "PENDING"
        signature_message = f"仍有 {len(missing_ids)} 个必需主体待确认"
    else:
        signature_result = "PASS"
        signature_message = "全部必需主体均已确认当前内容摘要"
    checks.append(
        _check(
            "signature_binding",
            signature_result,
            signature_message,
            expected=len(required_ids),
            actual=len(signed_ids),
            source="ContractSignature",
        )
    )

    binding_errors: list[str] = []
    runtime_pending: list[str] = []
    capability_codes: set[str] = set()
    for binding in bindings:
        if not binding.is_required:
            continue
        expected_receipts = _binding_receipt_digests(
            binding, revision_id=str(revision.id)
        )
        if binding.deployment_status != "accepted" or not binding.receipt_digest:
            binding_errors.append(f"绑定 {binding.execution_role} 未被接受")
            continue
        if binding.receipt_digest not in expected_receipts:
            binding_errors.append(f"绑定 {binding.execution_role} 回执摘要不一致")
            continue
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
            or connector.space_id != contract.space_id
            or connector.verification_status != "verified"
            or capability is None
            or capability.status != "verified"
            or capability.verified_at is None
            or not capability_parameters_satisfy_v1(
                binding.required_capability_code, capability.parameters
            )
        ):
            binding_errors.append(f"能力 {binding.required_capability_code} 未通过验证")
            continue
        capability_codes.add(binding.required_capability_code)
        heartbeat = _as_utc(connector.last_heartbeat_at)
        if (
            connector.runtime_status != "online"
            or heartbeat is None
            or heartbeat < now - timedelta(minutes=5)
        ):
            runtime_pending.append(f"Connector {connector.name} 当前不在线")
    missing_capabilities = REQUIRED_CAPABILITIES - capability_codes
    if missing_capabilities:
        binding_errors.append("缺少执行、出域或审计能力绑定")
    if binding_errors:
        binding_result = "BLOCKER"
        binding_message = "；".join(dict.fromkeys(binding_errors))
    elif runtime_pending:
        if stage == "execute":
            binding_result = "BLOCKER"
            binding_message = "；".join(dict.fromkeys(runtime_pending))
        else:
            binding_result = "PASS"
            binding_message = "静态执行绑定摘要一致；Connector 在线状态将在执行门禁重新核验"
    else:
        binding_result = "PASS"
        binding_message = "平台控制面登记的执行、出域和审计绑定摘要一致"
    checks.append(
        _check(
            "execution_binding",
            binding_result,
            binding_message,
            expected=sorted(REQUIRED_CAPABILITIES),
            actual=sorted(capability_codes),
            source="PolicyExecutionBinding and ConnectorCapability",
        )
    )

    final_policy = convergence.get("final", {}) if isinstance(convergence, dict) else {}
    summary = {
        "purpose_code": purpose_code,
        "run_count": final_policy.get("run_count"),
        "effective_until": effective_until.isoformat() if effective_until else None,
        "allowed_outputs": final_policy.get("allowed_outputs", []),
        "network_allowed": bool(final_policy.get("network_allowed", False)),
        "output_review_required": bool(final_policy.get("hospital_egress_review", True)),
        "prohibited_actions": sorted(
            item.action_code
            for item in policies
            if item.policy_type == "prohibition" and item.effect == "deny"
        ),
        "identity_assurance": "platform_session_and_admitted_organization",
        "confirmation_type": "internal_structured_confirmation",
        "access_policy_scope": "catalog_and_application",
        "usage_policy_scope": "contract_and_execution",
        "binding_assurance": "platform_control_plane_digest",
        "canonical_schema_version": (
            CONTRACT_CANONICAL_V2
            if revision.terms_schema_version == PRODUCTIZED_TERMS_V2
            else revision.terms_schema_version
        ),
    }
    return build_contract_security_decision(
        stage=stage,
        revision_id=str(revision.id),
        content_digest=revision.content_digest,
        summary=summary,
        checks=checks,
        checked_at=now,
    )


def security_blocker_message(report: dict[str, Any]) -> str:
    blockers = [
        item["message"]
        for item in report.get("checks", [])
        if item.get("result") == "BLOCKER"
    ]
    return "；".join(blockers) if blockers else "安全合约验证尚未通过"
