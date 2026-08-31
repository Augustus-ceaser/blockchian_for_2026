from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import NO_VALUE

from app.modules.contracts.models import (
    CONTRACT_PARTY_ROLES,
    CONTRACT_SIGNING_MODES,
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    ContractSignature,
    POLICY_ACTION_CODES,
    POLICY_BINDING_STATUSES,
    Policy,
    PolicyConstraint,
    PolicyExecutionBinding,
)
from app.modules.applications.models import (
    APPLICATION_ACTION_CODES,
    APPLICATION_OUTPUT_TYPES,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
    Application,
    ApplicationSnapshot,
)
from app.modules.catalog.models import DataProduct, DataProductVersion
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.identity.models import (
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    User,
)
from app.modules.reviews.models import ReviewDecision, ReviewTask
from app.modules.reviews.services import canonical_decision_digest
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole
from app.modules.audit.services import (
    AuditCommandContext,
    AuditInvariantError,
    append_audit_event_with_outbox,
    begin_audited_command,
)


class ContractInvariantError(ValueError):
    """Raised when Contract Core data violates the v5 freeze."""


DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTRACT_STABLE_FIELDS = {
    "space_id",
    "application_id",
    "application_snapshot_id",
    "application_snapshot_digest",
    "eligibility_evidence",
    "eligibility_digest",
    "contract_number",
    "created_at",
    "created_by",
    "is_demo",
}
POLICY_TYPE_EFFECT = {
    "permission": "permit",
    "prohibition": "deny",
    "obligation": "require",
}
BINDING_ROLE_CAPABILITY = {
    "compute_executor": ("controlled_compute_execution", "1.0"),
    "egress_controller": ("egress_policy_enforcement", "1.0"),
    "audit_evidence_emitter": ("audit_evidence_emit", "1.0"),
}
BINDING_SPEC_FIELDS = {
    "policy_id",
    "connector_id",
    "execution_role",
    "required_capability_code",
    "required_capability_version",
    "is_required",
}
BINDING_RUNTIME_FIELDS = {
    "deployment_status",
    "deployed_at",
    "acknowledged_at",
    "receipt_digest",
    "rejection_reason",
    "revoked_at",
    "revocation_receipt_digest",
    "revocation_reason",
    "updated_at",
    "row_version",
}
REQUIRED_V1_REVIEW_TYPES = {"application_precheck", "provider_review"}
REQUIRED_PHASE4_REVIEW_TYPES = {
    "application_precheck",
    "data_provider_review",
    "model_provider_review",
}


def canonical_document_digest(document: dict[str, Any]) -> str:
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _require_json_object(value: object, field_name: str) -> None:
    if not isinstance(value, dict):
        raise ContractInvariantError(f"{field_name} must be a JSON object")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ContractInvariantError(f"{field_name} must be canonical JSON") from error


def _require_digest(value: str | None, field_name: str) -> None:
    if not DIGEST_PATTERN.fullmatch(value or ""):
        raise ContractInvariantError(f"{field_name} must be sha256:<64 lowercase hex>")


def _changed_columns(target: object) -> set[str]:
    state = inspect(target)
    return {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }


def _old_value(target: object, attribute_name: str, current: Any) -> Any:
    history = inspect(target).attrs[attribute_name].history
    return history.deleted[0] if history.deleted else current


def _revision_for_child(
    session: Session, child: ContractParty | ContractObject | Policy
) -> ContractRevision | None:
    loaded = inspect(child).attrs.revision.loaded_value
    if loaded is not NO_VALUE:
        return loaded
    if child.contract_revision_id is None:
        return None
    return session.get(ContractRevision, child.contract_revision_id)


def _policy_for_child(
    session: Session, child: PolicyConstraint | PolicyExecutionBinding
) -> Policy | None:
    loaded = inspect(child).attrs.policy.loaded_value
    if loaded is not NO_VALUE:
        return loaded
    if child.policy_id is None:
        return None
    return session.get(Policy, child.policy_id)


def _revision_for_policy_child(
    session: Session, child: PolicyConstraint | PolicyExecutionBinding
) -> ContractRevision | None:
    policy = _policy_for_child(session, child)
    return None if policy is None else _revision_for_child(session, policy)


def _validate_contract(contract: Contract) -> None:
    _require_digest(contract.application_snapshot_digest, "application_snapshot_digest")
    _require_digest(contract.eligibility_digest, "eligibility_digest")
    _require_json_object(contract.eligibility_evidence, "eligibility_evidence")
    if not contract.contract_number:
        raise ContractInvariantError("contract_number is required")
    if contract.row_version is not None and contract.row_version < 1:
        raise ContractInvariantError("row_version must be positive")
    if contract.is_demo is not True:
        raise ContractInvariantError("Contract Core V1 only accepts demo contracts")


def _validate_draft_revision(revision: ContractRevision) -> None:
    if revision.revision_no is None or revision.revision_no <= 0:
        raise ContractInvariantError("revision_no must be positive")
    if revision.signing_mode not in CONTRACT_SIGNING_MODES:
        raise ContractInvariantError("unknown signing mode")
    if not revision.terms_schema_version:
        raise ContractInvariantError("terms_schema_version is required")
    _require_json_object(revision.terms_document, "terms_document")
    _require_digest(revision.terms_digest, "terms_digest")
    if canonical_document_digest(revision.terms_document) != revision.terms_digest:
        raise ContractInvariantError("terms_digest does not match terms_document")
    if revision.effective_until is not None and revision.effective_from is None:
        raise ContractInvariantError("effective_from is required when effective_until is set")
    if (
        revision.effective_from is not None
        and revision.effective_until is not None
        and revision.effective_until <= revision.effective_from
    ):
        raise ContractInvariantError("effective_until must be after effective_from")
    if revision.row_version is not None and revision.row_version < 1:
        raise ContractInvariantError("row_version must be positive")


def _validate_party(party: ContractParty) -> None:
    if party.party_role not in CONTRACT_PARTY_ROLES:
        raise ContractInvariantError("unknown contract party role")
    if party.signing_order is None or party.signing_order <= 0:
        raise ContractInvariantError("signing_order must be positive")
    if not party.party_name_snapshot:
        raise ContractInvariantError("party_name_snapshot is required")
    _require_json_object(party.identity_snapshot, "identity_snapshot")


def _validate_object(contract_object: ContractObject) -> None:
    if contract_object.position_no is None or contract_object.position_no <= 0:
        raise ContractInvariantError("position_no must be positive")
    _require_digest(contract_object.product_snapshot_digest, "product_snapshot_digest")
    _require_json_object(contract_object.authorized_scope, "authorized_scope")
    _require_digest(contract_object.authorized_scope_digest, "authorized_scope_digest")
    if (
        canonical_document_digest(contract_object.authorized_scope)
        != contract_object.authorized_scope_digest
    ):
        raise ContractInvariantError(
            "authorized_scope_digest does not match authorized_scope"
        )


def _validate_policy(policy: Policy) -> None:
    if not policy.policy_code:
        raise ContractInvariantError("policy_code is required")
    if POLICY_TYPE_EFFECT.get(policy.policy_type) != policy.effect:
        raise ContractInvariantError("policy_type and effect are inconsistent")
    if policy.action_code not in POLICY_ACTION_CODES:
        raise ContractInvariantError("unknown policy action_code")
    if policy.priority is None or policy.priority < 0:
        raise ContractInvariantError("policy priority must be nonnegative")
    if policy.policy_digest is not None:
        _require_digest(policy.policy_digest, "policy_digest")


def _is_sorted_unique_strings(value: object, *, allowed: tuple[str, ...] | None = None) -> bool:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        return False
    if value != sorted(set(value)):
        return False
    return allowed is None or all(item in allowed for item in value)


def _is_rfc3339_utc(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _validate_constraint(constraint: PolicyConstraint) -> None:
    name = constraint.constraint_name
    operator = constraint.operator
    value = constraint.value
    unit = constraint.unit
    valid = False
    if name == "purpose_code":
        valid = operator == "in" and unit is None and _is_sorted_unique_strings(
            value, allowed=APPLICATION_ACTION_CODES
        )
    elif name == "algorithm_digest":
        valid = operator == "eq" and unit is None and bool(
            DIGEST_PATTERN.fullmatch(value if isinstance(value, str) else "")
        )
    elif name == "environment_mode":
        valid = operator == "eq" and unit is None and value == "controlled_compute"
    elif name == "run_count":
        valid = operator == "lte" and unit == "count" and type(value) is int and value > 0
    elif name == "effective_until":
        valid = operator == "before" and unit is None and _is_rfc3339_utc(value)
    elif name == "output_type":
        valid = operator == "in" and unit is None and _is_sorted_unique_strings(
            value, allowed=APPLICATION_OUTPUT_TYPES
        )
    elif name == "output_review_required":
        valid = operator == "eq" and unit is None and value is True
    elif name == "retention_seconds":
        valid = (
            operator == "lte"
            and unit == "seconds"
            and type(value) is int
            and value >= 0
        )
    elif name == "region":
        valid = operator == "in" and unit is None and _is_sorted_unique_strings(value)
    elif name == "network_zone":
        valid = operator == "eq" and unit is None and isinstance(value, str) and bool(value)
    elif name == "audit_level":
        valid = operator == "gte" and unit is None and value == "full"
    if not valid:
        raise ContractInvariantError("invalid V1 policy constraint")
    if constraint.position_no is None or constraint.position_no <= 0:
        raise ContractInvariantError("constraint position_no must be positive")


def _validate_binding_shape(binding: PolicyExecutionBinding) -> None:
    required = BINDING_ROLE_CAPABILITY.get(binding.execution_role)
    actual = (binding.required_capability_code, binding.required_capability_version)
    if required != actual:
        raise ContractInvariantError("execution role requires an exact capability 1.0")
    if binding.deployment_status not in POLICY_BINDING_STATUSES:
        raise ContractInvariantError("unknown binding deployment_status")
    if binding.row_version is not None and binding.row_version < 1:
        raise ContractInvariantError("binding row_version must be positive")
    status = binding.deployment_status
    if status == "pending":
        valid = all(
            value is None
            for value in (
                binding.acknowledged_at,
                binding.receipt_digest,
                binding.rejection_reason,
                binding.revoked_at,
                binding.revocation_receipt_digest,
                binding.revocation_reason,
            )
        )
    elif status == "accepted":
        valid = (
            binding.acknowledged_at is not None
            and binding.receipt_digest is not None
            and binding.rejection_reason is None
            and binding.revoked_at is None
            and binding.revocation_receipt_digest is None
            and binding.revocation_reason is None
        )
    elif status == "rejected":
        valid = (
            binding.acknowledged_at is not None
            and bool(binding.rejection_reason)
            and binding.receipt_digest is None
            and binding.revoked_at is None
            and binding.revocation_receipt_digest is None
            and binding.revocation_reason is None
        )
    else:
        valid = (
            binding.acknowledged_at is not None
            and binding.receipt_digest is not None
            and binding.rejection_reason is None
            and binding.revoked_at is not None
            and binding.revocation_receipt_digest is not None
            and bool(binding.revocation_reason)
        )
    if not valid:
        raise ContractInvariantError("binding fields do not match deployment_status")
    for field_name in ("receipt_digest", "revocation_receipt_digest"):
        value = getattr(binding, field_name)
        if value is not None:
            _require_digest(value, field_name)


def _capability_parameters_satisfy_v1(
    capability_code: str, parameters: object
) -> bool:
    if not isinstance(parameters, dict):
        return False
    if capability_code == "controlled_compute_execution":
        return (
            isinstance(parameters.get("environment_modes"), list)
            and "controlled_compute" in parameters["environment_modes"]
            and parameters.get("algorithm_digest_enforced") is True
            and parameters.get("run_count_enforced") is True
            and parameters.get("effective_window_enforced") is True
        )
    if capability_code == "egress_policy_enforcement":
        return (
            parameters.get("raw_export_denied") is True
            and parameters.get("artifact_review_gate") is True
            and parameters.get("output_type_filter") is True
        )
    if capability_code == "audit_evidence_emit":
        return (
            isinstance(parameters.get("audit_levels"), list)
            and "full" in parameters["audit_levels"]
            and parameters.get("digest_algorithm") == "sha256"
            and parameters.get("failure_mode") == "fail_closed"
        )
    return False


def validate_policy_constraint_v1(constraint: PolicyConstraint) -> None:
    """Public V1 profile guard shared by contract lifecycle entry points."""

    _validate_constraint(constraint)


def capability_parameters_satisfy_v1(
    capability_code: str, parameters: object
) -> bool:
    """Public capability profile check shared by activation and readiness."""

    return _capability_parameters_satisfy_v1(capability_code, parameters)


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _validate_signature(signature: ContractSignature) -> None:
    if signature.signature_type != "demo":
        raise ContractInvariantError("V1 only accepts demo contract signatures")
    if signature.verification_status != "verified" or signature.verified_at is None:
        raise ContractInvariantError("demo signature must be verified")
    if not signature.signature_value_ref:
        raise ContractInvariantError("signature_value_ref is required")
    _require_digest(signature.signed_content_digest, "signed_content_digest")
    _require_digest(signature.signature_digest, "signature_digest")
    _require_json_object(signature.authority_snapshot, "authority_snapshot")
    expected = {
        "schema_version": "1.0",
        "is_demo": True,
        "organization_id": str(signature.signer_organization_id),
        "user_id": str(signature.signer_user_id),
        "organization_member_id": signature.authority_snapshot.get(
            "organization_member_id"
        ),
        "membership_status": "active",
        "authority_code": "demo_contract_signer",
        "scope": {
            "contract_revision_id": str(signature.contract_revision_id),
            "contract_party_id": str(signature.contract_party_id),
        },
    }
    if signature.authority_snapshot != expected:
        raise ContractInvariantError("authority_snapshot does not match signer authority")
    digest_document = {
        "schema_version": "1.0",
        "contract_revision_id": str(signature.contract_revision_id),
        "contract_party_id": str(signature.contract_party_id),
        "signer_organization_id": str(signature.signer_organization_id),
        "signer_user_id": str(signature.signer_user_id),
        "signature_type": signature.signature_type,
        "signature_value_ref": signature.signature_value_ref,
        "signed_content_digest": signature.signed_content_digest,
        "authority_snapshot": signature.authority_snapshot,
        "verification_status": signature.verification_status,
        "signed_at": _canonical_timestamp(signature.signed_at),
        "verified_at": _canonical_timestamp(signature.verified_at),
    }
    if canonical_document_digest(digest_document) != signature.signature_digest:
        raise ContractInvariantError("signature_digest does not match signature evidence")


def _revision_for_signature(
    session: Session, signature: ContractSignature
) -> ContractRevision | None:
    loaded = inspect(signature).attrs.revision.loaded_value
    if loaded is not NO_VALUE:
        return loaded
    if signature.contract_revision_id is None:
        return None
    return session.get(ContractRevision, signature.contract_revision_id)


def _require_draft_parent(
    session: Session, child: ContractParty | ContractObject | Policy
) -> None:
    revision = _revision_for_child(session, child)
    if revision is None or revision.status != "draft":
        raise ContractInvariantError("Contract Core children can only change in draft")


def _require_draft_policy_parent(
    session: Session, child: PolicyConstraint | PolicyExecutionBinding
) -> None:
    revision = _revision_for_policy_child(session, child)
    phase55_binding_setup = (
        isinstance(child, PolicyExecutionBinding)
        and revision is not None
        and revision.status == "active"
        and revision.terms_schema_version == "phase5.4/structured-contract/v1"
        and revision.handoff_guard_evidence is not None
        and revision.handoff_guard_evidence.get(
            "execution_bindings_deferred_to_phase5.5"
        )
        is True
    )
    if revision is None or (revision.status != "draft" and not phase55_binding_setup):
        raise ContractInvariantError("Policy children can only change in draft")


@event.listens_for(Session, "before_flush")
def guard_contract_core_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.new:
        if isinstance(target, Contract):
            _validate_contract(target)
        elif isinstance(target, ContractRevision):
            if target.status not in (None, "draft"):
                raise ContractInvariantError("new contract revision must start as draft")
            _validate_draft_revision(target)
        elif isinstance(target, ContractParty):
            _require_draft_parent(session, target)
            _validate_party(target)
        elif isinstance(target, ContractObject):
            _require_draft_parent(session, target)
            _validate_object(target)
        elif isinstance(target, Policy):
            _require_draft_parent(session, target)
            _validate_policy(target)
        elif isinstance(target, PolicyConstraint):
            _require_draft_policy_parent(session, target)
            _validate_constraint(target)
        elif isinstance(target, PolicyExecutionBinding):
            _require_draft_policy_parent(session, target)
            _validate_binding_shape(target)
        elif isinstance(target, ContractSignature):
            revision = _revision_for_signature(session, target)
            if revision is None or revision.status != "proposed":
                raise ContractInvariantError(
                    "contract signatures can only be appended to proposed revisions"
                )
            _validate_signature(target)

    for target in session.dirty:
        if isinstance(target, Contract):
            changed = _changed_columns(target)
            if changed & CONTRACT_STABLE_FIELDS:
                raise ContractInvariantError("contract source evidence is immutable")
            _validate_contract(target)
        elif isinstance(target, ContractRevision):
            changed = _changed_columns(target)
            if not changed:
                continue
            old_status = _old_value(target, "status", target.status)
            if old_status == "proposed" and target.status == "signed":
                if not getattr(target, "_signing_validated", False):
                    raise ContractInvariantError(
                        "signed transition requires the Contract Signature service"
                    )
                if changed - {"status", "signed_at", "row_version"}:
                    raise ContractInvariantError(
                        "signing cannot change revision content"
                    )
                if target.signed_at is None:
                    raise ContractInvariantError("signed revision requires signed_at")
            elif old_status == "signed" and target.status == "active":
                if not getattr(target, "_activation_validated", False):
                    raise ContractInvariantError(
                        "active transition requires the Contract activation service"
                    )
                if changed - {"status", "activated_at", "row_version"}:
                    raise ContractInvariantError(
                        "activation cannot change revision content"
                    )
                if target.activated_at is None:
                    raise ContractInvariantError("active revision requires activated_at")
            elif old_status != "draft":
                raise ContractInvariantError("proposed or terminal revision is immutable")
            elif target.status == "draft":
                _validate_draft_revision(target)
            elif target.status == "withdrawn":
                if changed - {"status", "ended_at", "row_version"}:
                    raise ContractInvariantError(
                        "withdrawing a draft cannot change revision content"
                    )
                if target.ended_at is None:
                    raise ContractInvariantError("withdrawn revision requires ended_at")
            elif target.status == "proposed":
                if not getattr(target, "_proposal_validated", False):
                    raise ContractInvariantError(
                        "proposal requires the Contract Policy and Binding proposal service"
                    )
                allowed = {
                    "status",
                    "handoff_guard_evidence",
                    "handoff_guard_digest",
                    "content_digest",
                    "proposed_at",
                    "row_version",
                }
                if changed - allowed:
                    raise ContractInvariantError(
                        "proposal cannot change unrelated revision fields"
                    )
            else:
                raise ContractInvariantError(
                    "proposal is unavailable until Policy and Binding are implemented"
                )
        elif isinstance(target, ContractParty):
            _require_draft_parent(session, target)
            _validate_party(target)
        elif isinstance(target, ContractObject):
            _require_draft_parent(session, target)
            _validate_object(target)
        elif isinstance(target, Policy):
            _require_draft_parent(session, target)
            _validate_policy(target)
        elif isinstance(target, PolicyConstraint):
            _require_draft_policy_parent(session, target)
            _validate_constraint(target)
        elif isinstance(target, PolicyExecutionBinding):
            revision = _revision_for_policy_child(session, target)
            if revision is None:
                raise ContractInvariantError("binding requires a ContractRevision")
            changed = _changed_columns(target)
            if revision.status == "draft":
                _validate_binding_shape(target)
            else:
                if changed & BINDING_SPEC_FIELDS or changed - BINDING_RUNTIME_FIELDS:
                    raise ContractInvariantError("binding specification is immutable")
                old_status = _old_value(
                    target, "deployment_status", target.deployment_status
                )
                legal = {
                    "pending": {"accepted", "rejected"},
                    "accepted": {"revoked"},
                    "rejected": set(),
                    "revoked": set(),
                }
                if target.deployment_status != old_status and target.deployment_status not in legal.get(old_status, set()):
                    raise ContractInvariantError("illegal binding deployment transition")
                if target.deployment_status == old_status:
                    allowed_same_state = (
                        {"deployed_at", "updated_at", "row_version"}
                        if old_status == "pending"
                        else {"updated_at"}
                    )
                    if changed - allowed_same_state:
                        raise ContractInvariantError(
                            "binding evidence is immutable within a state"
                        )
                else:
                    transition_fields = {
                        ("pending", "accepted"): {
                            "deployment_status",
                            "acknowledged_at",
                            "receipt_digest",
                            "updated_at",
                            "row_version",
                        },
                        ("pending", "rejected"): {
                            "deployment_status",
                            "acknowledged_at",
                            "rejection_reason",
                            "updated_at",
                            "row_version",
                        },
                        ("accepted", "revoked"): {
                            "deployment_status",
                            "revoked_at",
                            "revocation_receipt_digest",
                            "revocation_reason",
                            "updated_at",
                            "row_version",
                        },
                    }[(old_status, target.deployment_status)]
                    if changed - transition_fields:
                        raise ContractInvariantError(
                            "binding transition changes unrelated evidence"
                        )
                    old_row_version = _old_value(
                        target, "row_version", target.row_version
                    )
                    if target.row_version != old_row_version + 1:
                        raise ContractInvariantError(
                            "binding transition must increment row_version"
                        )
                _validate_binding_shape(target)
        elif isinstance(target, ContractSignature):
            raise ContractInvariantError("contract signature is append-only")

    for target in session.deleted:
        if isinstance(target, Contract):
            raise ContractInvariantError("contract series cannot be deleted")
        if isinstance(target, ContractRevision) and target.status != "draft":
            raise ContractInvariantError("only an unproposed draft revision can be deleted")
        if isinstance(target, (ContractParty, ContractObject, Policy)):
            _require_draft_parent(session, target)
        if isinstance(target, (PolicyConstraint, PolicyExecutionBinding)):
            _require_draft_policy_parent(session, target)
        if isinstance(target, ContractSignature):
            raise ContractInvariantError("contract signature is append-only")


def withdraw_draft_revision(
    revision: ContractRevision, *, ended_at: datetime | None = None
) -> None:
    if revision.status != "draft":
        raise ContractInvariantError("only a draft revision can be withdrawn in B1")
    revision.status = "withdrawn"
    revision.ended_at = ended_at or datetime.now(timezone.utc)
    revision.row_version += 1


def _policy_digest_document(policy: Policy) -> dict[str, Any]:
    constraints = sorted(policy.constraints, key=lambda item: item.position_no)
    return {
        "schema_version": "1.0",
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


async def propose_contract_revision(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    proposed_at: datetime | None = None,
) -> None:
    """Freeze a complete D1 policy package as proposed; it grants no access."""

    if revision.status != "draft":
        raise ContractInvariantError("only a draft revision can be proposed")
    await session.flush()
    contract = await session.get(Contract, revision.contract_id)
    if contract is None:
        raise ContractInvariantError("revision contract is missing")

    parties = list(
        (
            await session.scalars(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    objects = list(
        (
            await session.scalars(
                select(ContractObject).where(
                    ContractObject.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    policies = list(
        (
            await session.scalars(
                select(Policy).where(Policy.contract_revision_id == revision.id)
            )
        ).all()
    )
    if not any(item.party_role in {"provider", "data_provider"} for item in parties):
        raise ContractInvariantError("proposal requires a provider party")
    consumers = [
        item for item in parties if item.party_role in {"consumer", "data_requester"}
    ]
    if not consumers or not objects:
        raise ContractInvariantError("proposal requires consumer parties and objects")

    requested_actions = set(
        (
            await session.scalars(
                select(ApplicationRequestedAction.action_code).where(
                    ApplicationRequestedAction.application_id == contract.application_id
                )
            )
        ).all()
    )
    requested_outputs = set(
        (
            await session.scalars(
                select(ApplicationRequestedOutputType.output_type).where(
                    ApplicationRequestedOutputType.application_id
                    == contract.application_id
                )
            )
        ).all()
    )
    required_set = {
        ("permission", "permit", "execute_controlled_compute"),
        ("prohibition", "deny", "export_raw_data"),
        ("prohibition", "deny", "reidentify_subject"),
        ("prohibition", "deny", "redistribute_data"),
        ("obligation", "require", "write_audit_log"),
    }
    policy_digests: list[str] = []
    binding_specs: list[dict[str, Any]] = []
    allowed_owner_orgs = {
        item.organization_id
        for item in parties
        if item.party_role
        in {
            "provider",
            "data_provider",
            "model_provider",
            "service_provider",
            "operator_witness",
        }
    }

    for consumer in consumers:
        for contract_object in objects:
            actual = {
                (item.policy_type, item.effect, item.action_code)
                for item in policies
                if item.subject_contract_party_id == consumer.id
                and item.contract_object_id == contract_object.id
            }
            if not required_set <= actual:
                raise ContractInvariantError("proposal is missing the minimum deny/audit policy set")

    for policy in policies:
        await session.refresh(policy, attribute_names=["constraints", "execution_bindings"])
        has_output_constraint = False
        for constraint in policy.constraints:
            _validate_constraint(constraint)
            if constraint.constraint_name == "purpose_code" and not set(
                constraint.value
            ) <= requested_actions:
                raise ContractInvariantError("policy purpose expands the application")
            if constraint.constraint_name == "output_type" and not set(
                constraint.value
            ) <= requested_outputs:
                raise ContractInvariantError("policy output expands the application")
            if constraint.constraint_name == "output_type":
                has_output_constraint = True
        if policy.action_code == "export_artifact" and policy.effect == "permit" and not has_output_constraint:
            raise ContractInvariantError(
                "artifact export requires an approved output_type constraint"
            )
        policy.policy_digest = canonical_document_digest(_policy_digest_document(policy))
        policy_digests.append(policy.policy_digest)

        required_roles: set[str] = set()
        if policy.action_code == "execute_controlled_compute" and policy.effect == "permit":
            required_roles.add("compute_executor")
        if policy.action_code in {"export_artifact", "export_raw_data", "redistribute_data"}:
            required_roles.add("egress_controller")
        if policy.action_code == "reidentify_subject":
            required_roles.update({"compute_executor", "egress_controller"})
        if policy.action_code == "write_audit_log" and policy.effect == "require":
            required_roles.add("audit_evidence_emitter")
        if policy.action_code in {"retain_intermediate", "delete_intermediate"} and policy.effect == "require":
            required_roles.add("compute_executor")
        bindings_by_role = {
            item.execution_role: item
            for item in policy.execution_bindings
            if item.is_required
        }
        if not required_roles <= bindings_by_role.keys():
            raise ContractInvariantError("proposal is missing a required execution binding")
        for role in required_roles:
            binding = bindings_by_role[role]
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
                or connector.owner_organization_id not in allowed_owner_orgs
                or connector.verification_status != "verified"
            ):
                raise ContractInvariantError("binding connector is not eligible")
            if (
                capability is None
                or capability.status != "verified"
                or capability.verified_at is None
                or not _capability_parameters_satisfy_v1(
                    binding.required_capability_code, capability.parameters
                )
            ):
                raise ContractInvariantError("required connector capability is not verified")
            binding_specs.append(
                {
                    "policy_id": str(policy.id),
                    "connector_id": str(binding.connector_id),
                    "execution_role": binding.execution_role,
                    "required_capability_code": binding.required_capability_code,
                    "required_capability_version": binding.required_capability_version,
                    "is_required": binding.is_required,
                }
            )

    # Persist Policy digests while the parent is still draft. The following
    # flush changes only the Revision lifecycle fields.
    await session.flush()
    handoff = {
        "schema_version": "1.0",
        "eligibility_digest": contract.eligibility_digest,
        "policy_digests": sorted(policy_digests),
        "binding_specs": sorted(
            binding_specs,
            key=lambda item: (
                item["policy_id"],
                item["execution_role"],
                item["connector_id"],
            ),
        ),
    }
    from app.db.base import Base

    phase4_loaded = (
        session.bind is not None and session.bind.dialect.name == "postgresql"
    ) or f"medtrust.contract_model_objects" in Base.metadata.tables
    model_object = None
    if phase4_loaded:
        from app.modules.marketplace.models import ContractModelObject

        model_object = await session.scalar(
            select(ContractModelObject).where(
                ContractModelObject.contract_revision_id == revision.id
            )
        )
    content = {
        "schema_version": "1.0",
        "contract_id": str(contract.id),
        "revision_no": revision.revision_no,
        "terms_digest": revision.terms_digest,
        "handoff_guard_digest": canonical_document_digest(handoff),
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
                for item in objects
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
        "policy_digests": sorted(policy_digests),
        "binding_specs": handoff["binding_specs"],
    }
    revision.handoff_guard_evidence = handoff
    revision.handoff_guard_digest = content["handoff_guard_digest"]
    revision.content_digest = canonical_document_digest(content)
    revision.status = "proposed"
    revision.proposed_at = proposed_at or datetime.now(timezone.utc)
    revision.row_version += 1
    revision._proposal_validated = True
    await session.flush()


async def sign_contract_revision(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    contract_party_id: Any,
    signer_organization_id: Any,
    signer_user_id: Any,
    signature_value_ref: str,
    signed_at: datetime | None = None,
) -> ContractSignature:
    """Append one demo signature and atomically close signing when complete."""

    if revision.status != "proposed":
        raise ContractInvariantError("only a proposed revision can be signed")
    _require_digest(revision.content_digest, "content_digest")
    await session.flush()

    party = await session.get(ContractParty, contract_party_id)
    if (
        party is None
        or party.contract_revision_id != revision.id
        or party.organization_id != signer_organization_id
    ):
        raise ContractInvariantError("signer organization does not represent this party")
    organization = await session.get(Organization, signer_organization_id)
    user = await session.get(User, signer_user_id)
    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == signer_organization_id,
            OrganizationMember.user_id == signer_user_id,
        )
    )
    now = signed_at or datetime.now(timezone.utc)
    member_valid_from = _as_utc(membership.valid_from) if membership else None
    member_valid_until = _as_utc(membership.valid_until) if membership else None
    if organization is None or organization.status != "active":
        raise ContractInvariantError("signer organization is not active")
    if user is None or user.status != "active":
        raise ContractInvariantError("signer user is not active")
    if (
        membership is None
        or membership.status != "active"
        or (member_valid_from is not None and member_valid_from > now)
        or (member_valid_until is not None and member_valid_until <= now)
    ):
        raise ContractInvariantError("signer is not an active organization member")
    signer_role = await session.get(
        OrganizationMemberRole, (membership.id, "contract_signer")
    )
    if signer_role is None:
        raise ContractInvariantError("signer lacks the contract_signer authority")

    authority_snapshot = {
        "schema_version": "1.0",
        "is_demo": True,
        "organization_id": str(signer_organization_id),
        "user_id": str(signer_user_id),
        "organization_member_id": str(membership.id),
        "membership_status": "active",
        "authority_code": "demo_contract_signer",
        "scope": {
            "contract_revision_id": str(revision.id),
            "contract_party_id": str(party.id),
        },
    }
    digest_document = {
        "schema_version": "1.0",
        "contract_revision_id": str(revision.id),
        "contract_party_id": str(party.id),
        "signer_organization_id": str(signer_organization_id),
        "signer_user_id": str(signer_user_id),
        "signature_type": "demo",
        "signature_value_ref": signature_value_ref,
        "signed_content_digest": revision.content_digest,
        "authority_snapshot": authority_snapshot,
        "verification_status": "verified",
        "signed_at": _canonical_timestamp(now),
        "verified_at": _canonical_timestamp(now),
    }
    signature = ContractSignature(
        contract_revision_id=revision.id,
        contract_party_id=party.id,
        signer_organization_id=signer_organization_id,
        signer_user_id=signer_user_id,
        signature_type="demo",
        signature_value_ref=signature_value_ref,
        signed_content_digest=revision.content_digest,
        authority_snapshot=authority_snapshot,
        verification_status="verified",
        signature_digest=canonical_document_digest(digest_document),
        signed_at=now,
        verified_at=now,
        created_at=now,
    )
    session.add(signature)
    await session.flush()

    required_party_ids = set(
        (
            await session.scalars(
                select(ContractParty.id).where(
                    ContractParty.contract_revision_id == revision.id,
                    ContractParty.is_required.is_(True),
                )
            )
        ).all()
    )
    signed_party_ids = set(
        (
            await session.scalars(
                select(ContractSignature.contract_party_id).where(
                    ContractSignature.contract_revision_id == revision.id,
                    ContractSignature.signed_content_digest == revision.content_digest,
                    ContractSignature.verification_status == "verified",
                )
            )
        ).all()
    )
    if required_party_ids and required_party_ids <= signed_party_ids:
        revision.status = "signed"
        revision.signed_at = now
        revision.row_version += 1
        revision._signing_validated = True
        await session.flush()
    return signature


def _review_plan_document(
    *,
    space_id: Any,
    application_id: Any,
    snapshot: ApplicationSnapshot,
    tasks: list[ReviewTask],
) -> dict[str, Any]:
    requirements = sorted(
        [
            {
                "review_type": task.review_type,
                "sequence_no": task.sequence_no,
                "is_required": task.is_required,
                "assignee_organization_id": str(task.assignee_organization_id),
                "routing_rule_digest": task.routing_rule_digest,
            }
            for task in tasks
        ],
        key=lambda item: (
            item["sequence_no"],
            item["review_type"],
            item["assignee_organization_id"],
            item["routing_rule_digest"],
        ),
    )
    return {
        "schema_version": "1.0",
        "orchestration_algorithm": "review-orchestration-v1",
        "route_config_version": "demo-v1",
        "space_id": str(space_id),
        "application_id": str(application_id),
        "application_snapshot_id": str(snapshot.id),
        "target_digest": snapshot.snapshot_digest,
        "requirements": requirements,
    }


async def build_contract_eligibility_evidence(
    session: AsyncSession,
    *,
    application: Application,
    snapshot: ApplicationSnapshot,
) -> dict[str, Any]:
    if application.status != "approved" or snapshot.application_id != application.id:
        raise ContractInvariantError("application is not approved for Contract eligibility")
    tasks = list(
        (
            await session.scalars(
                select(ReviewTask).where(
                    ReviewTask.application_snapshot_id == snapshot.id
                )
            )
        ).all()
    )
    required_tasks = [task for task in tasks if task.is_required]
    from app.db.base import Base

    phase4_loaded = (
        session.bind is not None and session.bind.dialect.name == "postgresql"
    ) or f"medtrust.application_model_selections" in Base.metadata.tables
    has_model_selection = False
    if phase4_loaded:
        from app.modules.marketplace.models import ApplicationModelSelection

        has_model_selection = (
            await session.scalar(
                select(ApplicationModelSelection.id).where(
                    ApplicationModelSelection.application_id == application.id
                )
            )
            is not None
        )
    required_review_types = (
        REQUIRED_PHASE4_REVIEW_TYPES if has_model_selection else REQUIRED_V1_REVIEW_TYPES
    )
    if not required_review_types <= {
        task.review_type for task in required_tasks
    }:
        raise ContractInvariantError("V1 review plan is missing mandatory reviews")
    plan_digest = canonical_document_digest(
        _review_plan_document(
            space_id=application.space_id,
            application_id=application.id,
            snapshot=snapshot,
            tasks=tasks,
        )
    )

    decisions = {
        decision.review_task_id: decision
        for decision in (
            await session.scalars(
                select(ReviewDecision).where(
                    ReviewDecision.review_task_id.in_(
                        [task.id for task in required_tasks]
                    )
                )
            )
        ).all()
    }
    actual_decisions: list[dict[str, Any]] = []
    for task in required_tasks:
        decision = decisions.get(task.id)
        if (
            task.task_status != "decided"
            or decision is None
            or decision.decision != "approved"
            or decision.target_digest != snapshot.snapshot_digest
        ):
            raise ContractInvariantError("a required review is not currently approved")
        decision_time = decision.decided_at
        if decision_time.tzinfo is None:
            # SQLite drops timezone metadata in fast tests; persisted PostgreSQL
            # timestamps remain timezone-aware. Treat a naive test value as UTC.
            decision_time = decision_time.replace(tzinfo=timezone.utc)
        expected_decision_digest = canonical_decision_digest(
            task=task,
            decision=decision.decision,
            reason_code=decision.reason_code,
            comment=decision.comment,
            remediation=decision.remediation,
            evidence=decision.evidence,
            decided_by_user_id=decision.decided_by_user_id,
            decided_for_organization_id=decision.decided_for_organization_id,
            decided_at=decision_time,
        )
        if decision.decision_digest != expected_decision_digest:
            raise ContractInvariantError("review decision digest is invalid")
        actual_decisions.append(
            {
                "review_task_id": str(task.id),
                "review_type": task.review_type,
                "sequence_no": task.sequence_no,
                "assignee_organization_id": str(task.assignee_organization_id),
                "target_digest": task.target_digest,
                "decision": decision.decision,
                "decision_digest": decision.decision_digest,
            }
        )
    actual_decisions.sort(
        key=lambda item: (
            item["sequence_no"], item["review_type"], item["review_task_id"]
        )
    )
    return {
        "schema_version": "1.0",
        "eligibility_algorithm": "contract-eligibility-v1",
        "orchestration_algorithm": "review-orchestration-v1",
        "space_id": str(application.space_id),
        "application_id": str(application.id),
        "application_snapshot_id": str(snapshot.id),
        "snapshot_digest": snapshot.snapshot_digest,
        "application_status": "approved",
        "review_plan_digest": plan_digest,
        "required_decisions": actual_decisions,
        "outcome": "approved_for_contract",
    }


async def _validate_current_review_eligibility(
    session: AsyncSession,
    *,
    contract: Contract,
    application: Application,
    snapshot: ApplicationSnapshot,
) -> None:
    evidence = contract.eligibility_evidence
    _require_json_object(evidence, "eligibility_evidence")
    if canonical_document_digest(evidence) != contract.eligibility_digest:
        raise ContractInvariantError("eligibility evidence digest is invalid")
    current = await build_contract_eligibility_evidence(
        session, application=application, snapshot=snapshot
    )
    if evidence != current:
        raise ContractInvariantError("eligibility evidence no longer matches Review facts")


async def activate_contract_revision(
    session: AsyncSession,
    revision: ContractRevision,
    *,
    activated_at: datetime | None = None,
    audit_command: AuditCommandContext | None = None,
) -> None:
    """Activate a signed revision only after rechecking every current V1 guard."""

    contract = await session.get(Contract, revision.contract_id)
    if contract is None:
        raise ContractInvariantError("revision contract is missing")
    request_snapshot = {
        "schema_version": "contract-activation-command/v1",
        "contract_id": str(contract.id),
        "contract_revision_id": str(revision.id),
        "revision_content_digest": revision.content_digest,
        "requested_activated_at": (
            _as_utc(activated_at).isoformat() if activated_at is not None else None
        ),
    }
    command_request_digest: str | None = None
    if audit_command is not None:
        existing, command_request_digest = await begin_audited_command(
            session,
            space_id=contract.space_id,
            event_type="contract.revision.activated",
            subject_type="contract_revision",
            command=audit_command,
            request_snapshot=request_snapshot,
            expected_subject_id=revision.id,
        )
        if existing is not None:
            await session.refresh(revision)
            if revision.status != "active":
                raise AuditInvariantError("activation event exists but revision is not active")
            return
    if revision.status != "signed":
        raise ContractInvariantError("only a signed revision can be activated")
    now = _as_utc(activated_at or datetime.now(timezone.utc))
    effective_from = _as_utc(revision.effective_from)
    effective_until = _as_utc(revision.effective_until)
    if effective_from is not None and now < effective_from:
        raise ContractInvariantError("revision effective window has not started")
    if effective_until is not None and now >= effective_until:
        raise ContractInvariantError("revision effective window has ended")
    await session.flush()
    application = await session.get(Application, contract.application_id)
    snapshot = await session.get(ApplicationSnapshot, contract.application_snapshot_id)
    space = await session.get(Space, contract.space_id)
    if (
        application is None
        or application.status != "approved"
        or application.space_id != contract.space_id
        or snapshot is None
        or snapshot.application_id != application.id
        or snapshot.snapshot_digest != contract.application_snapshot_digest
    ):
        raise ContractInvariantError("application approval evidence is not current")
    if space is None or space.status != "active":
        raise ContractInvariantError("contract space is not active")
    await _validate_current_review_eligibility(
        session, contract=contract, application=application, snapshot=snapshot
    )

    parties = list(
        (
            await session.scalars(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    required_party_ids = {party.id for party in parties if party.is_required}
    signatures = list(
        (
            await session.scalars(
                select(ContractSignature).where(
                    ContractSignature.contract_revision_id == revision.id,
                    ContractSignature.signed_content_digest == revision.content_digest,
                )
            )
        ).all()
    )
    if required_party_ids - {signature.contract_party_id for signature in signatures}:
        raise ContractInvariantError("required contract signatures are incomplete")

    for party in parties:
        organization = await session.get(Organization, party.organization_id)
        participant = await session.scalar(
            select(SpaceParticipant).where(
                SpaceParticipant.space_id == contract.space_id,
                SpaceParticipant.organization_id == party.organization_id,
            )
        )
        expected_roles = {
            "provider": {"provider"},
            "data_provider": {"data_provider"},
            "model_provider": {"model_provider"},
            "consumer": {"consumer"},
            "data_requester": {"data_requester"},
            "service_provider": {"service_provider"},
            "operator_witness": {"operator", "space_operator"},
        }[party.party_role]
        participant_role = None
        if participant is not None:
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
            raise ContractInvariantError("a contract party is not currently admitted")

    objects = list(
        (
            await session.scalars(
                select(ContractObject).where(
                    ContractObject.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    for contract_object in objects:
        version = await session.get(
            DataProductVersion, contract_object.data_product_version_id
        )
        product = (
            None
            if version is None
            else await session.get(DataProduct, version.data_product_id)
        )
        if (
            version is None
            or version.status != "approved"
            or version.snapshot_digest != contract_object.product_snapshot_digest
            or product is None
            or product.lifecycle_status != "active"
        ):
            raise ContractInvariantError("a contracted data product version is unavailable")

    from app.db.base import Base

    phase4_loaded = (
        session.bind is not None and session.bind.dialect.name == "postgresql"
    ) or f"medtrust.contract_model_objects" in Base.metadata.tables
    model_object = None
    if phase4_loaded:
        from app.modules.marketplace.models import ContractModelObject

        model_object = await session.scalar(
            select(ContractModelObject).where(
                ContractModelObject.contract_revision_id == revision.id
            )
        )
    if model_object is not None:
        from app.modules.marketplace.models import ModelProduct, ModelVersion
        model_version = await session.get(ModelVersion, model_object.model_version_id)
        model_product = (
            None
            if model_version is None
            else await session.get(ModelProduct, model_version.model_product_id)
        )
        if (
            model_version is None
            or model_version.status != "approved"
            or model_version.snapshot_digest != model_object.model_snapshot_digest
            or model_product is None
            or model_product.lifecycle_status != "active"
        ):
            raise ContractInvariantError("contracted model version is unavailable")

    policies = list(
        (
            await session.scalars(
                select(Policy).where(Policy.contract_revision_id == revision.id)
            )
        ).all()
    )
    if not policies:
        raise ContractInvariantError("active revision requires policies")
    for policy in policies:
        await session.refresh(policy, attribute_names=["constraints", "execution_bindings"])
        if policy.policy_digest != canonical_document_digest(
            _policy_digest_document(policy)
        ):
            raise ContractInvariantError("policy digest is invalid")
        for binding in policy.execution_bindings:
            if not binding.is_required:
                continue
            if binding.deployment_status != "accepted":
                raise ContractInvariantError("required policy binding is not accepted")
            _validate_binding_shape(binding)
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
                or connector.runtime_status != "online"
                or connector.last_heartbeat_at is None
                or capability is None
                or capability.status != "verified"
                or capability.verified_at is None
                or not _capability_parameters_satisfy_v1(
                    binding.required_capability_code, capability.parameters
                )
            ):
                raise ContractInvariantError(
                    "required connector capability is not currently executable"
                )

    other_live = await session.scalar(
        select(ContractRevision.id).where(
            ContractRevision.contract_id == revision.contract_id,
            ContractRevision.id != revision.id,
            ContractRevision.status.in_(("active", "suspended")),
        )
    )
    if other_live is not None:
        raise ContractInvariantError("another revision is already active or suspended")
    if audit_command is None or command_request_digest is None:
        raise AuditInvariantError("contract activation requires AuditCommandContext")
    revision.status = "active"
    revision.activated_at = now
    revision.row_version += 1
    revision._activation_validated = True
    await session.flush()
    try:
        await append_audit_event_with_outbox(
            session,
            space_id=contract.space_id,
            event_type="contract.revision.activated",
            subject_type="contract_revision",
            subject_id=revision.id,
            result="success",
            evidence_snapshot={
                "schema_version": "contract-revision-activated-evidence/v1",
                "command_request_digest": command_request_digest,
                "contract_id": str(contract.id),
                "contract_revision_id": str(revision.id),
                "revision_content_digest": revision.content_digest,
                "application_snapshot_digest": contract.application_snapshot_digest,
                "eligibility_digest": contract.eligibility_digest,
                "activated_at": _as_utc(revision.activated_at).isoformat(),
            },
            **audit_command.append_kwargs(),
        )
    except Exception:
        await session.rollback()
        raise
