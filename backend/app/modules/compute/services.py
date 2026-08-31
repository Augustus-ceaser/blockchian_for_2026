from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from uuid import UUID

from sqlalchemy import event, func, inspect, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.applications.models import (
    Application,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
)
from app.modules.catalog.models import DataProduct, DataProductVersion
from app.modules.external_catalog.eligibility import (
    ExternalDataProductEligibilityError,
    ExternalModelProductEligibilityError,
    require_materialized_data_product,
    require_materialized_model_product,
)
from app.modules.compute.models import (
    ARTIFACT_RELEASE_STATUSES,
    ARTIFACT_REVIEW_DECISIONS,
    ARTIFACT_REVIEW_STATUSES,
    COMPUTE_OUTPUT_TYPES,
    Artifact,
    ArtifactReview,
    ComputeJob,
    ComputeRun,
    ExecutionEligibilityInvalidation,
    ExecutionEligibilitySnapshot,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.contracts.models import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    Policy,
    PolicyConstraint,
    PolicyExecutionBinding,
)
from app.modules.contracts.services import canonical_document_digest
from app.modules.identity.models import Organization, OrganizationMember, User
from app.modules.marketplace.models import ContractModelObject, ModelProduct, ModelVersion
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole
from app.modules.audit.services import (
    AuditCommandContext,
    AuditInvariantError,
    append_audit_event_with_outbox,
    begin_audited_command,
)

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
HEARTBEAT_MAX_AGE = timedelta(minutes=5)
RUN_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "interrupted",
    "cancelled",
    "timed_out",
}
ARTIFACT_STABLE_FIELDS = {
    "space_id",
    "compute_job_id",
    "compute_run_id",
    "artifact_no",
    "artifact_type",
    "content_digest",
    "storage_reference",
    "size_bytes",
    "classification_level",
    "output_policy_evaluation",
    "output_policy_evaluation_digest",
    "retention_until",
    "created_at",
}
ARTIFACT_REVIEW_STABLE_FIELDS = {
    "space_id",
    "artifact_id",
    "target_content_digest",
    "responsible_organization_id",
    "routing_rule_digest",
    "created_at",
}
JOB_TERMINAL_STATUSES = {
    "succeeded",
    "denied",
    "failed",
    "interrupted",
    "cancelled",
}


class ComputeInvariantError(ValueError):
    """Raised when Compute metadata violates the v7 freeze."""


class AuditEvidenceUnavailable(ComputeInvariantError):
    """Raised while reliable transactional Audit/outbox is unavailable."""


async def _append_command_event_or_rollback(
    session: AsyncSession, **kwargs: Any
) -> None:
    try:
        await append_audit_event_with_outbox(session, **kwargs)
    except Exception:
        await session.rollback()
        raise


@dataclass(frozen=True)
class AuthorizationContext:
    contract: Contract
    revision: ContractRevision
    party: ContractParty
    contract_object: ContractObject
    quota_policy: Policy
    run_count_constraint: PolicyConstraint
    run_limit: int
    compute_binding: PolicyExecutionBinding
    egress_binding: PolicyExecutionBinding
    audit_binding: PolicyExecutionBinding
    evaluation: dict[str, Any]
    execution_environment: dict[str, Any]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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


def _require_digest(value: str | None, name: str) -> None:
    if not DIGEST_PATTERN.fullmatch(value or ""):
        raise ComputeInvariantError(f"{name} must be sha256:<64 lowercase hex>")


def _require_json_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComputeInvariantError(f"{name} must be a JSON object")
    return value


def _validate_job_shape(job: ComputeJob) -> None:
    if job.status not in {
        "created", "validating", "ready", "running", "stopping", "succeeded",
        "denied", "failed", "interrupted", "cancelled",
    }:
        raise ComputeInvariantError("invalid ComputeJob status")
    if not isinstance(job.requested_output_types, list) or not job.requested_output_types:
        raise ComputeInvariantError("requested_output_types must be a non-empty list")
    if len(set(job.requested_output_types)) != len(job.requested_output_types):
        raise ComputeInvariantError("requested_output_types must be unique")
    if any(item not in COMPUTE_OUTPUT_TYPES for item in job.requested_output_types):
        raise ComputeInvariantError("requested_output_types contains an unknown value")
    if job.requested_output_types != sorted(job.requested_output_types):
        raise ComputeInvariantError("requested_output_types must use canonical order")
    for name in (
        "revision_content_digest",
        "algorithm_spec_digest",
        "compute_input_digest",
        "creation_authorization_evaluation_digest",
        "creation_request_digest",
    ):
        _require_digest(getattr(job, name), name)
    eligibility_fields = (
        job.execution_eligibility_snapshot_id,
        job.eligibility_snapshot_digest,
        job.quota_policy_id,
        job.run_count_constraint_id,
        job.run_limit_snapshot,
        job.pre_dispatch_slot_ordinal,
        job.pre_dispatch_slot_digest,
        job.pre_dispatch_reserved_at,
    )
    if any(value is not None for value in eligibility_fields):
        if any(value is None for value in eligibility_fields):
            raise ComputeInvariantError(
                "pre-dispatch ComputeJob reservation evidence is incomplete"
            )
        _require_digest(job.eligibility_snapshot_digest, "eligibility_snapshot_digest")
        _require_digest(job.pre_dispatch_slot_digest, "pre_dispatch_slot_digest")
        if job.run_limit_snapshot <= 0 or job.pre_dispatch_slot_ordinal <= 0:
            raise ComputeInvariantError("pre-dispatch quota values must be positive")
    if canonical_document_digest(_require_json_object(job.algorithm_spec_snapshot, "algorithm_spec_snapshot")) != job.algorithm_spec_digest:
        raise ComputeInvariantError("algorithm_spec_digest does not match its snapshot")
    if canonical_document_digest(_require_json_object(job.compute_input_snapshot, "compute_input_snapshot")) != job.compute_input_digest:
        raise ComputeInvariantError("compute_input_digest does not match its snapshot")
    if canonical_document_digest(
        _require_json_object(
            job.creation_authorization_evaluation,
            "creation_authorization_evaluation",
        )
    ) != job.creation_authorization_evaluation_digest:
        raise ComputeInvariantError(
            "creation_authorization_evaluation_digest does not match its evidence"
        )


def _validate_eligibility_snapshot(snapshot: ExecutionEligibilitySnapshot) -> None:
    for name in (
        "revision_content_digest",
        "check_matrix_digest",
        "eligibility_snapshot_digest",
        "execution_environment_digest",
    ):
        _require_digest(getattr(snapshot, name), name)
    if canonical_document_digest(
        {"items": snapshot.check_matrix}
    ) != snapshot.check_matrix_digest:
        raise ComputeInvariantError("check_matrix_digest does not match its evidence")
    if canonical_document_digest(
        _require_json_object(
            snapshot.eligibility_snapshot, "eligibility_snapshot"
        )
    ) != snapshot.eligibility_snapshot_digest:
        raise ComputeInvariantError(
            "eligibility_snapshot_digest does not match its evidence"
        )
    if canonical_document_digest(
        _require_json_object(
            snapshot.execution_environment_snapshot,
            "execution_environment_snapshot",
        )
    ) != snapshot.execution_environment_digest:
        raise ComputeInvariantError(
            "execution_environment_digest does not match its evidence"
        )
    if _as_utc(snapshot.valid_until) <= _as_utc(snapshot.created_at):
        raise ComputeInvariantError("eligibility valid_until must follow created_at")


def _validate_eligibility_invalidation(
    invalidation: ExecutionEligibilityInvalidation,
) -> None:
    _require_digest(invalidation.evidence_digest, "evidence_digest")
    if canonical_document_digest(
        _require_json_object(invalidation.evidence_snapshot, "evidence_snapshot")
    ) != invalidation.evidence_digest:
        raise ComputeInvariantError(
            "eligibility invalidation digest does not match its evidence"
        )


def _validate_prepared_run(run: ComputeRun) -> None:
    if run.status != "prepared":
        raise ComputeInvariantError("new ComputeRun must start as prepared")
    if run.attempt_no <= 0:
        raise ComputeInvariantError("attempt_no must be positive")
    reservation_fields = (
        "quota_policy_id",
        "run_count_constraint_id",
        "run_limit_snapshot",
        "reservation_ordinal",
        "quota_scope_digest",
        "quota_reservation_digest",
        "quota_consumed_at",
        "start_authorization_evaluation",
        "start_authorization_evaluation_digest",
        "compute_binding_id",
        "egress_binding_id",
        "audit_binding_id",
        "execution_environment_snapshot",
        "execution_environment_digest",
        "reserved_at",
    )
    if any(getattr(run, name) is not None for name in reservation_fields):
        raise ComputeInvariantError("prepared Run cannot contain reservation evidence")


def _validate_storage_reference(value: str) -> None:
    lowered = value.lower()
    if (
        not value
        or "://" in value
        or "?" in value
        or "\\" in value
        or value.startswith("/")
        or ".." in value.split("/")
        or re.match(r"^[a-zA-Z]:/", value)
        or any(
            marker in lowered
            for marker in ("x-amz-", "signature=", "token=", "secret", "access_key")
        )
    ):
        raise ComputeInvariantError(
            "storage_reference must be an opaque isolated object reference"
        )


def _validate_artifact_shape(artifact: Artifact) -> None:
    if artifact.artifact_type not in COMPUTE_OUTPUT_TYPES:
        raise ComputeInvariantError("unknown Artifact type")
    if artifact.release_status not in ARTIFACT_RELEASE_STATUSES:
        raise ComputeInvariantError("invalid Artifact release status")
    if artifact.artifact_no <= 0:
        raise ComputeInvariantError("artifact_no must be positive")
    if artifact.size_bytes < 0:
        raise ComputeInvariantError("Artifact size must be non-negative")
    if not artifact.classification_level:
        raise ComputeInvariantError("classification_level is required")
    _require_digest(artifact.content_digest, "content_digest")
    _require_digest(
        artifact.output_policy_evaluation_digest,
        "output_policy_evaluation_digest",
    )
    evaluation = _require_json_object(
        artifact.output_policy_evaluation,
        "output_policy_evaluation",
    )
    if canonical_document_digest(evaluation) != artifact.output_policy_evaluation_digest:
        raise ComputeInvariantError(
            "output_policy_evaluation_digest does not match its evidence"
        )
    _validate_storage_reference(artifact.storage_reference)
    if artifact.release_status == "quarantined" and any(
        value is not None
        for value in (
            artifact.release_evidence,
            artifact.release_evidence_digest,
            artifact.released_at,
            artifact.revoked_at,
            artifact.destroyed_at,
        )
    ):
        raise ComputeInvariantError("new quarantined Artifact cannot contain release evidence")


def _validate_artifact_review_shape(review: ArtifactReview) -> None:
    if review.status not in ARTIFACT_REVIEW_STATUSES:
        raise ComputeInvariantError("invalid ArtifactReview status")
    _require_digest(review.target_content_digest, "target_content_digest")
    _require_digest(review.routing_rule_digest, "routing_rule_digest")
    if review.status == "pending":
        valid = (
            review.claimed_by_user_id is None
            and review.claimed_at is None
            and review.decision is None
            and review.reason_code is None
            and review.decision_evidence is None
            and review.decision_digest is None
            and review.decided_at is None
            and review.cancelled_at is None
        )
    elif review.status == "claimed":
        valid = (
            review.claimed_by_user_id is not None
            and review.claimed_at is not None
            and review.decision is None
            and review.reason_code is None
            and review.decision_evidence is None
            and review.decision_digest is None
            and review.decided_at is None
            and review.cancelled_at is None
        )
    elif review.status == "decided":
        valid = (
            review.claimed_by_user_id is not None
            and review.claimed_at is not None
            and review.decision in ARTIFACT_REVIEW_DECISIONS
            and bool(review.reason_code)
            and isinstance(review.decision_evidence, dict)
            and review.decision_digest is not None
            and review.decided_at is not None
            and review.cancelled_at is None
        )
        if valid:
            _require_digest(review.decision_digest, "decision_digest")
    elif review.status == "cancelled":
        valid = (
            review.decision is None
            and review.decision_evidence is None
            and review.decision_digest is None
            and review.decided_at is None
            and review.cancelled_at is not None
        )
    else:
        valid = False
    if not valid:
        raise ComputeInvariantError(f"invalid ArtifactReview shape for {review.status}")


JOB_STABLE_FIELDS = {
    "space_id",
    "contract_id",
    "contract_revision_id",
    "revision_content_digest",
    "requester_contract_party_id",
    "requester_organization_id",
    "requester_user_id",
    "contract_object_id",
    "purpose_code",
    "requested_output_types",
    "algorithm_spec_snapshot",
    "algorithm_spec_digest",
    "compute_input_snapshot",
    "compute_input_digest",
    "creation_authorization_evaluation",
    "creation_authorization_evaluation_digest",
    "creation_request_digest",
    "execution_eligibility_snapshot_id",
    "eligibility_snapshot_digest",
    "quota_policy_id",
    "run_count_constraint_id",
    "run_limit_snapshot",
    "pre_dispatch_slot_ordinal",
    "pre_dispatch_slot_digest",
    "pre_dispatch_reserved_at",
    "created_at",
    "created_by",
}
RUN_STABLE_FIELDS = {
    "space_id",
    "compute_job_id",
    "contract_id",
    "contract_revision_id",
    "requester_contract_party_id",
    "contract_object_id",
    "attempt_no",
    "prepared_at",
    "created_by",
}
RUN_RESERVATION_FIELDS = {
    "quota_policy_id",
    "run_count_constraint_id",
    "run_limit_snapshot",
    "reservation_ordinal",
    "quota_scope_digest",
    "quota_reservation_digest",
    "quota_consumed_at",
    "start_authorization_evaluation",
    "start_authorization_evaluation_digest",
    "compute_binding_id",
    "egress_binding_id",
    "audit_binding_id",
    "execution_environment_snapshot",
    "execution_environment_digest",
    "reserved_at",
}


@event.listens_for(Session, "before_flush")
def guard_compute_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.deleted:
        if isinstance(
            target,
            (ExecutionEligibilitySnapshot, ExecutionEligibilityInvalidation),
        ):
            raise ComputeInvariantError("execution eligibility evidence cannot be deleted")
        if isinstance(target, ComputeJob):
            raise ComputeInvariantError("ComputeJob cannot be deleted")
        if isinstance(target, ComputeRun):
            raise ComputeInvariantError("ComputeRun cannot be deleted")
        if isinstance(target, Artifact):
            raise ComputeInvariantError("Artifact cannot be deleted")
        if isinstance(target, ArtifactReview):
            raise ComputeInvariantError("ArtifactReview cannot be deleted")

    for target in session.new:
        if isinstance(target, ExecutionEligibilitySnapshot):
            _validate_eligibility_snapshot(target)
        elif isinstance(target, ExecutionEligibilityInvalidation):
            _validate_eligibility_invalidation(target)
        elif isinstance(target, ComputeJob):
            if target.status not in (None, "created"):
                raise ComputeInvariantError("new ComputeJob must start as created")
            _validate_job_shape(target)
        elif isinstance(target, ComputeRun):
            _validate_prepared_run(target)
        elif isinstance(target, Artifact):
            if target.release_status not in (None, "quarantined"):
                raise ComputeInvariantError("new Artifact must start as quarantined")
            _validate_artifact_shape(target)
        elif isinstance(target, ArtifactReview):
            if target.status not in (None, "pending"):
                raise ComputeInvariantError("new ArtifactReview must start as pending")
            _validate_artifact_review_shape(target)

    for target in session.dirty:
        if isinstance(
            target,
            (ExecutionEligibilitySnapshot, ExecutionEligibilityInvalidation),
        ):
            if _changed_columns(target):
                raise ComputeInvariantError(
                    "execution eligibility evidence is append-only"
                )
        elif isinstance(target, ComputeJob):
            changed = _changed_columns(target)
            if not changed:
                continue
            if changed & JOB_STABLE_FIELDS:
                raise ComputeInvariantError("ComputeJob intent and evidence are immutable")
            old_status = _old_value(target, "status", target.status)
            if old_status in JOB_TERMINAL_STATUSES:
                raise ComputeInvariantError("terminal ComputeJob is immutable")
            legal = {
                "created": {"validating", "cancelled"},
                "validating": {"ready", "denied", "failed"},
                "ready": {"validating", "running", "cancelled"},
                "running": {"stopping", "succeeded", "failed", "interrupted"},
                "stopping": {"cancelled", "failed", "interrupted"},
            }
            if target.status != old_status and target.status not in legal.get(old_status, set()):
                raise ComputeInvariantError("illegal ComputeJob status transition")
            if target.status != old_status and not getattr(target, "_transition_validated", False):
                raise ComputeInvariantError("ComputeJob status requires a domain service")
            _validate_job_shape(target)
        elif isinstance(target, ComputeRun):
            changed = _changed_columns(target)
            if not changed:
                continue
            old_status = _old_value(target, "status", target.status)
            if old_status in RUN_TERMINAL_STATUSES:
                raise ComputeInvariantError("terminal ComputeRun is immutable")
            if changed & RUN_STABLE_FIELDS:
                raise ComputeInvariantError("ComputeRun attempt identity is immutable")
            if old_status != "prepared" and changed & RUN_RESERVATION_FIELDS:
                raise ComputeInvariantError("ComputeRun reservation evidence is immutable")
            legal = {
                "prepared": {"reserved", "cancelled"},
                "reserved": {"dispatched", "failed", "interrupted"},
                "dispatched": {"running", "failed", "interrupted", "timed_out"},
                "running": {"succeeded", "failed", "interrupted", "cancelled", "timed_out"},
            }
            if target.status != old_status and target.status not in legal.get(old_status, set()):
                raise ComputeInvariantError("illegal ComputeRun status transition")
            if target.status != old_status and not getattr(target, "_transition_validated", False):
                raise ComputeInvariantError("ComputeRun status requires a domain service")
            if old_status == "prepared" and target.status == "reserved":
                if not getattr(target, "_reservation_validated", False):
                    raise ComputeInvariantError("Run reservation requires authorization service")
                required = RUN_RESERVATION_FIELDS - {
                    "reservation_ordinal",
                    "quota_consumed_at",
                    "reserved_at",
                }
                if any(getattr(target, name) is None for name in required):
                    raise ComputeInvariantError("reserved Run requires complete reservation evidence")
        elif isinstance(target, Artifact):
            changed = _changed_columns(target)
            if not changed:
                continue
            if changed & ARTIFACT_STABLE_FIELDS:
                raise ComputeInvariantError("Artifact identity and policy evidence are immutable")
            old_status = _old_value(target, "release_status", target.release_status)
            legal = {
                "quarantined": {"released", "destroyed"},
                "released": {"revoked"},
                "revoked": {"destroyed"},
                "destroyed": set(),
            }
            if target.release_status != old_status and target.release_status not in legal.get(old_status, set()):
                raise ComputeInvariantError("illegal Artifact release transition")
            if target.release_status != old_status and not getattr(
                target, "_transition_validated", False
            ):
                raise ComputeInvariantError("Artifact status requires a domain service")
            _validate_artifact_shape(target)
        elif isinstance(target, ArtifactReview):
            changed = _changed_columns(target)
            if not changed:
                continue
            if changed & ARTIFACT_REVIEW_STABLE_FIELDS:
                raise ComputeInvariantError("ArtifactReview target and routing are immutable")
            old_status = _old_value(target, "status", target.status)
            if old_status in {"decided", "cancelled"}:
                raise ComputeInvariantError("terminal ArtifactReview is immutable")
            legal = {"pending": {"claimed", "cancelled"}, "claimed": {"decided", "cancelled"}}
            if target.status != old_status and target.status not in legal.get(old_status, set()):
                raise ComputeInvariantError("illegal ArtifactReview status transition")
            if target.status != old_status and not getattr(
                target, "_transition_validated", False
            ):
                raise ComputeInvariantError("ArtifactReview status requires a domain service")
            _validate_artifact_review_shape(target)


def _constraint_allows(constraint: PolicyConstraint, value: str) -> bool:
    if constraint.operator == "eq":
        return constraint.value == value
    if constraint.operator == "in" and isinstance(constraint.value, list):
        return value in constraint.value
    return False


async def _one_constraint(
    session: AsyncSession,
    policy_id: UUID,
    name: str,
) -> PolicyConstraint:
    constraints = list(
        (
            await session.scalars(
                select(PolicyConstraint).where(
                    PolicyConstraint.policy_id == policy_id,
                    PolicyConstraint.constraint_name == name,
                )
            )
        ).all()
    )
    if len(constraints) != 1:
        raise ComputeInvariantError(f"policy requires exactly one {name} constraint")
    return constraints[0]


async def _optional_constraint(
    session: AsyncSession,
    policy_id: UUID,
    name: str,
) -> PolicyConstraint | None:
    constraints = list(
        (
            await session.scalars(
                select(PolicyConstraint).where(
                    PolicyConstraint.policy_id == policy_id,
                    PolicyConstraint.constraint_name == name,
                )
            )
        ).all()
    )
    if len(constraints) > 1:
        raise ComputeInvariantError(f"Policy requires at most one {name} constraint")
    return constraints[0] if constraints else None


async def _one_binding(
    session: AsyncSession,
    policy_id: UUID,
    role: str,
) -> PolicyExecutionBinding:
    bindings = list(
        (
            await session.scalars(
                select(PolicyExecutionBinding).where(
                    PolicyExecutionBinding.policy_id == policy_id,
                    PolicyExecutionBinding.execution_role == role,
                    PolicyExecutionBinding.is_required.is_(True),
                )
            )
        ).all()
    )
    if len(bindings) != 1:
        raise ComputeInvariantError(f"policy requires one {role} binding")
    binding = bindings[0]
    expected = {
        "compute_executor": "controlled_compute_execution",
        "egress_controller": "egress_policy_enforcement",
        "audit_evidence_emitter": "audit_evidence_emit",
    }[role]
    if (
        binding.deployment_status != "accepted"
        or binding.required_capability_code != expected
        or binding.required_capability_version != "1.0"
    ):
        raise ComputeInvariantError(f"{role} binding is not currently executable")
    return binding


async def _validate_binding_current(
    session: AsyncSession,
    binding: PolicyExecutionBinding,
    *,
    space_id: UUID,
    now: datetime,
) -> tuple[Connector, ConnectorCapability]:
    connector = await session.get(Connector, binding.connector_id)
    capability = await session.get(
        ConnectorCapability,
        (
            binding.connector_id,
            binding.required_capability_code,
            binding.required_capability_version,
        ),
    )
    heartbeat = None if connector is None else _as_utc(connector.last_heartbeat_at)
    if (
        connector is None
        or connector.space_id != space_id
        or connector.verification_status != "verified"
        or connector.runtime_status != "online"
        or heartbeat is None
        or heartbeat < now - HEARTBEAT_MAX_AGE
        or capability is None
        or capability.status != "verified"
        or capability.verified_at is None
    ):
        raise ComputeInvariantError("required Connector capability is unavailable")
    return connector, capability


async def evaluate_compute_authorization(
    session: AsyncSession,
    *,
    revision_id: UUID,
    party_id: UUID,
    contract_object_id: UUID,
    requester_organization_id: UUID,
    requester_user_id: UUID,
    purpose_code: str,
    algorithm_digest: str,
    requested_output_types: list[str],
    evaluated_at: datetime | None = None,
    exclude_run_id: UUID | None = None,
    exclude_job_id: UUID | None = None,
) -> AuthorizationContext:
    now = _as_utc(evaluated_at or datetime.now(timezone.utc))
    assert now is not None
    _require_digest(algorithm_digest, "algorithm_digest")
    outputs = sorted(set(requested_output_types))
    if outputs != requested_output_types or not outputs:
        raise ComputeInvariantError("requested outputs must be non-empty canonical values")
    if any(output not in COMPUTE_OUTPUT_TYPES for output in outputs):
        raise ComputeInvariantError("requested output type is unsupported")

    revision = await session.get(ContractRevision, revision_id)
    if revision is None or revision.status != "active":
        raise ComputeInvariantError("ContractRevision is not active")
    effective_from = _as_utc(revision.effective_from)
    effective_until = _as_utc(revision.effective_until)
    if effective_from is not None and now < effective_from:
        raise ComputeInvariantError("ContractRevision effective window has not started")
    if effective_until is not None and now >= effective_until:
        raise ComputeInvariantError("ContractRevision effective window has ended")

    contract = await session.get(Contract, revision.contract_id)
    party = await session.get(ContractParty, party_id)
    contract_object = await session.get(ContractObject, contract_object_id)
    if contract is None or party is None or contract_object is None:
        raise ComputeInvariantError("Contract execution scope is incomplete")
    # Published commercial bundles require checkout even before an order exists;
    # only contracts with no explicit offer or fixed service-market price-plan
    # match retain the legacy execution path. Existing orders always require a
    # paid, ready execution entitlement.
    from app.modules.commerce.gating import (
        CommercialExecutionBlocked,
        require_paid_execution_entitlement,
    )

    try:
        commercial_entitlement = await require_paid_execution_entitlement(
            session, contract_id=contract.id
        )
    except CommercialExecutionBlocked as exc:
        raise ComputeInvariantError(str(exc)) from exc
    if (
        party.contract_revision_id != revision.id
        or party.organization_id != requester_organization_id
        or party.party_role not in {"consumer", "data_requester"}
        or contract_object.contract_revision_id != revision.id
    ):
        raise ComputeInvariantError("Party or ContractObject is outside the Revision")

    space = await session.get(Space, contract.space_id)
    organization = await session.get(Organization, requester_organization_id)
    user = await session.get(User, requester_user_id)
    member = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == requester_organization_id,
            OrganizationMember.user_id == requester_user_id,
        )
    )
    participant = await session.scalar(
        select(SpaceParticipant).where(
            SpaceParticipant.space_id == contract.space_id,
            SpaceParticipant.organization_id == requester_organization_id,
        )
    )
    member_from = None if member is None else _as_utc(member.valid_from)
    member_until = None if member is None else _as_utc(member.valid_until)
    if (
        space is None
        or space.status != "active"
        or organization is None
        or organization.status != "active"
        or user is None
        or user.status != "active"
        or member is None
        or member.status != "active"
        or (member_from is not None and now < member_from)
        or (member_until is not None and now >= member_until)
        or participant is None
        or participant.admission_status != "admitted"
    ):
        raise ComputeInvariantError("requester is not currently admitted")

    try:
        await require_materialized_data_product(
            session, contract_object.data_product_version_id
        )
    except ExternalDataProductEligibilityError as exc:
        raise ComputeInvariantError(str(exc)) from exc
    version = await session.get(DataProductVersion, contract_object.data_product_version_id)
    product = None if version is None else await session.get(DataProduct, version.data_product_id)
    if (
        version is None
        or version.status != "approved"
        or version.snapshot_digest != contract_object.product_snapshot_digest
        or product is None
        or product.lifecycle_status != "active"
    ):
        raise ComputeInvariantError("contracted DataProductVersion is unavailable")

    application = await session.get(Application, contract.application_id)
    if application is None or application.status != "approved":
        raise ComputeInvariantError("source Application is not approved")
    if application.algorithm_digest != algorithm_digest:
        raise ComputeInvariantError("algorithm digest is outside the approved Application")
    requested_actions = set(
        (
            await session.scalars(
                select(ApplicationRequestedAction.action_code).where(
                    ApplicationRequestedAction.application_id == application.id
                )
            )
        ).all()
    )
    approved_outputs = set(
        (
            await session.scalars(
                select(ApplicationRequestedOutputType.output_type).where(
                    ApplicationRequestedOutputType.application_id == application.id
                )
            )
        ).all()
    )
    if purpose_code not in requested_actions or not set(outputs).issubset(approved_outputs):
        raise ComputeInvariantError("Job request exceeds the approved Application")

    policies = list(
        (
            await session.scalars(
                select(Policy).where(
                    Policy.contract_revision_id == revision.id,
                    Policy.subject_contract_party_id == party.id,
                    Policy.contract_object_id == contract_object.id,
                )
            )
        ).all()
    )
    if any(
        policy.effect == "deny" and policy.action_code == "execute_controlled_compute"
        for policy in policies
    ):
        raise ComputeInvariantError("Policy deny blocks controlled compute")
    permits = [
        policy
        for policy in policies
        if policy.effect == "permit" and policy.action_code == "execute_controlled_compute"
    ]
    if len(permits) != 1:
        raise ComputeInvariantError("ambiguous_permit_policy")
    quota_policy = permits[0]
    if quota_policy.policy_digest is None:
        raise ComputeInvariantError("governing Policy digest is missing")

    purpose_constraint = await _optional_constraint(
        session, quota_policy.id, "purpose_code"
    )
    algorithm_constraint = await _optional_constraint(
        session, quota_policy.id, "algorithm_digest"
    )
    environment_constraint = await _one_constraint(session, quota_policy.id, "environment_mode")
    run_constraint = await _one_constraint(session, quota_policy.id, "run_count")
    structured_phase54 = (
        revision.terms_schema_version == "phase5.4/structured-contract/v1"
    )
    if purpose_constraint is not None:
        if not _constraint_allows(purpose_constraint, purpose_code):
            raise ComputeInvariantError("purpose is outside Contract Policy")
    elif not structured_phase54 or purpose_code not in requested_actions:
        raise ComputeInvariantError("purpose is outside Contract Policy")
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
    model_product = (
        None
        if model_version is None
        else await session.get(ModelProduct, model_version.model_product_id)
    )
    requires_current_registry_model = (
        revision.terms_schema_version == "phase5.4/structured-contract/v2"
        or model_object is not None
    )
    if model_object is not None:
        try:
            await require_materialized_model_product(
                session, model_object.model_version_id
            )
        except ExternalModelProductEligibilityError as exc:
            raise ComputeInvariantError(str(exc)) from exc
    if requires_current_registry_model and (
        model_object is None
        or model_version is None
        or model_product is None
        or model_version.status != "approved"
        or model_product.lifecycle_status != "active"
        or model_version.snapshot_digest != model_object.model_snapshot_digest
        or model_version.model_digest != algorithm_digest
        or application.algorithm_digest != algorithm_digest
    ):
        raise ComputeInvariantError("contracted ModelVersion is unavailable")
    if algorithm_constraint is not None:
        if not _constraint_allows(algorithm_constraint, algorithm_digest):
            raise ComputeInvariantError("algorithm is outside Contract Policy")
    elif not structured_phase54:
        raise ComputeInvariantError("algorithm is outside Contract Policy")
    if not _constraint_allows(environment_constraint, "controlled_compute"):
        raise ComputeInvariantError("controlled compute environment is not permitted")
    if (
        run_constraint.operator != "lte"
        or run_constraint.unit != "count"
        or isinstance(run_constraint.value, bool)
        or not isinstance(run_constraint.value, int)
        or run_constraint.value <= 0
    ):
        raise ComputeInvariantError("run_count constraint is invalid")
    run_limit = run_constraint.value

    export_policies = [
        policy
        for policy in policies
        if policy.effect == "permit" and policy.action_code == "export_artifact"
    ]
    eligible_export: list[Policy] = []
    for policy in export_policies:
        constraint = await _one_constraint(session, policy.id, "output_type")
        if all(_constraint_allows(constraint, output) for output in outputs):
            eligible_export.append(policy)
    if len(eligible_export) != 1:
        raise ComputeInvariantError("requested outputs require one governing export Policy")
    if any(
        policy.effect == "deny" and policy.action_code == "export_artifact"
        for policy in policies
    ):
        raise ComputeInvariantError("Policy deny blocks Artifact output")
    export_policy = eligible_export[0]

    audit_policies = [
        policy
        for policy in policies
        if policy.effect == "require" and policy.action_code == "write_audit_log"
    ]
    if len(audit_policies) != 1:
        raise ComputeInvariantError("controlled compute requires one Audit obligation")
    audit_policy = audit_policies[0]

    compute_binding = await _one_binding(session, quota_policy.id, "compute_executor")
    egress_binding = await _one_binding(session, export_policy.id, "egress_controller")
    audit_binding = await _one_binding(session, audit_policy.id, "audit_evidence_emitter")
    binding_rows = []
    for binding in (compute_binding, egress_binding, audit_binding):
        connector, capability = await _validate_binding_current(
            session, binding, space_id=contract.space_id, now=now
        )
        binding_rows.append(
            {
                "binding_id": str(binding.id),
                "policy_id": str(binding.policy_id),
                "execution_role": binding.execution_role,
                "connector_id": str(connector.id),
                "capability_code": capability.capability_code,
                "capability_version": capability.capability_version,
                "capability_verified_at": _as_utc(capability.verified_at).isoformat(),
                "binding_receipt_digest": binding.receipt_digest,
            }
        )
    binding_rows.sort(key=lambda item: (item["execution_role"], item["policy_id"], item["binding_id"]))

    used_runs_query = (
        select(func.count(ComputeRun.id))
        .join(ComputeJob, ComputeJob.id == ComputeRun.compute_job_id)
        .where(
            ComputeRun.contract_revision_id == revision.id,
            ComputeRun.quota_policy_id == quota_policy.id,
            ComputeRun.requester_contract_party_id == party.id,
            ComputeRun.contract_object_id == contract_object.id,
            ComputeRun.reservation_ordinal.is_not(None),
            ComputeJob.pre_dispatch_slot_ordinal.is_(None),
        )
    )
    if exclude_run_id is not None:
        used_runs_query = used_runs_query.where(ComputeRun.id != exclude_run_id)
    used_runs = await session.scalar(used_runs_query)
    used_jobs_query = select(func.count(ComputeJob.id)).where(
        ComputeJob.contract_revision_id == revision.id,
        ComputeJob.quota_policy_id == quota_policy.id,
        ComputeJob.requester_contract_party_id == party.id,
        ComputeJob.contract_object_id == contract_object.id,
        ComputeJob.pre_dispatch_slot_ordinal.is_not(None),
    )
    if exclude_job_id is not None:
        used_jobs_query = used_jobs_query.where(ComputeJob.id != exclude_job_id)
    used_jobs = await session.scalar(used_jobs_query)
    used_total = int(used_runs or 0) + int(used_jobs or 0)
    if used_total >= run_limit:
        raise ComputeInvariantError("run_count quota is exhausted")

    evaluation = {
        "schema_version": "contract-use-evaluation/v1",
        "decision": "permit",
        "evaluated_at": now.isoformat(),
        "space_id": str(contract.space_id),
        "contract_id": str(contract.id),
        "contract_revision_id": str(revision.id),
        "revision_content_digest": revision.content_digest,
        "requester_contract_party_id": str(party.id),
        "requester_organization_id": str(requester_organization_id),
        "requester_user_id": str(requester_user_id),
        "contract_object_id": str(contract_object.id),
        "purpose_code": purpose_code,
        "algorithm_digest": algorithm_digest,
        "requested_output_types": outputs,
        "quota_policy_id": str(quota_policy.id),
        "run_count_constraint_id": str(run_constraint.id),
        "run_limit": run_limit,
        "currently_reserved": used_total,
        "run_reservations": int(used_runs or 0),
        "pre_dispatch_job_slots": int(used_jobs or 0),
        "matched_policy_digests": sorted(
            [quota_policy.policy_digest, export_policy.policy_digest, audit_policy.policy_digest]
        ),
        "binding_ids": sorted(str(binding.id) for binding in (compute_binding, egress_binding, audit_binding)),
        "output_review_required": True,
        "commercial_entitlement": commercial_entitlement,
    }
    environment = {
        "schema_version": "execution-environment/v1",
        "environment_mode": "controlled_compute",
        "bindings": binding_rows,
        "network_mode": "deny_by_default",
        "raw_export": False,
    }
    return AuthorizationContext(
        contract=contract,
        revision=revision,
        party=party,
        contract_object=contract_object,
        quota_policy=quota_policy,
        run_count_constraint=run_constraint,
        run_limit=run_limit,
        compute_binding=compute_binding,
        egress_binding=egress_binding,
        audit_binding=audit_binding,
        evaluation=evaluation,
        execution_environment=environment,
    )


async def create_compute_job(
    session: AsyncSession,
    *,
    revision_id: UUID,
    party_id: UUID,
    contract_object_id: UUID,
    requester_organization_id: UUID,
    requester_user_id: UUID,
    purpose_code: str,
    requested_output_types: list[str],
    algorithm_spec_snapshot: dict[str, Any],
    audit_command: AuditCommandContext | None = None,
    eligibility_snapshot: ExecutionEligibilitySnapshot | None = None,
    slot_audit_command: AuditCommandContext | None = None,
) -> ComputeJob:
    algorithm = _require_json_object(algorithm_spec_snapshot, "algorithm_spec_snapshot")
    algorithm_digest = algorithm.get("algorithm_digest")
    if not isinstance(algorithm_digest, str):
        raise ComputeInvariantError("algorithm_spec_snapshot requires algorithm_digest")
    outputs = sorted(set(requested_output_types))
    context = await evaluate_compute_authorization(
        session,
        revision_id=revision_id,
        party_id=party_id,
        contract_object_id=contract_object_id,
        requester_organization_id=requester_organization_id,
        requester_user_id=requester_user_id,
        purpose_code=purpose_code,
        algorithm_digest=algorithm_digest,
        requested_output_types=outputs,
    )
    input_snapshot = {
        "schema_version": "compute-input/v1",
        "contract_object_id": str(context.contract_object.id),
        "data_product_version_id": str(context.contract_object.data_product_version_id),
        "product_snapshot_digest": context.contract_object.product_snapshot_digest,
        "authorized_scope_digest": context.contract_object.authorized_scope_digest,
    }
    slot_ordinal = None
    slot_digest = None
    slot_reserved_at = None
    if eligibility_snapshot is not None:
        invalidation = await session.scalar(
            select(ExecutionEligibilityInvalidation.id).where(
                ExecutionEligibilityInvalidation.execution_eligibility_snapshot_id
                == eligibility_snapshot.id
            )
        )
        if (
            invalidation is not None
            or eligibility_snapshot.space_id != context.contract.space_id
            or eligibility_snapshot.contract_revision_id != context.revision.id
            or eligibility_snapshot.revision_content_digest
            != context.revision.content_digest
            or eligibility_snapshot.data_product_version_id
            != context.contract_object.data_product_version_id
        ):
            raise ComputeInvariantError(
                "execution eligibility snapshot is invalid or outside the Job scope"
            )
        if _as_utc(eligibility_snapshot.valid_until) <= datetime.now(timezone.utc):
            raise ComputeInvariantError("execution eligibility snapshot has expired")
        if slot_audit_command is None:
            raise AuditEvidenceUnavailable(
                "pre-dispatch slot reservation requires AuditCommandContext"
            )
        locked_constraint = await session.scalar(
            select(PolicyConstraint)
            .where(PolicyConstraint.id == context.run_count_constraint.id)
            .with_for_update()
        )
        if locked_constraint is None:
            raise ComputeInvariantError("run_count constraint is unavailable")
        used_runs = await session.scalar(
            select(func.count(ComputeRun.id))
            .join(ComputeJob, ComputeJob.id == ComputeRun.compute_job_id)
            .where(
                ComputeRun.contract_revision_id == context.revision.id,
                ComputeRun.quota_policy_id == context.quota_policy.id,
                ComputeRun.requester_contract_party_id == context.party.id,
                ComputeRun.contract_object_id == context.contract_object.id,
                ComputeRun.reservation_ordinal.is_not(None),
                ComputeJob.pre_dispatch_slot_ordinal.is_(None),
            )
        )
        used_jobs = await session.scalar(
            select(func.count(ComputeJob.id)).where(
                ComputeJob.contract_revision_id == context.revision.id,
                ComputeJob.quota_policy_id == context.quota_policy.id,
                ComputeJob.requester_contract_party_id == context.party.id,
                ComputeJob.contract_object_id == context.contract_object.id,
                ComputeJob.pre_dispatch_slot_ordinal.is_not(None),
            )
        )
        if int(used_runs or 0) + int(used_jobs or 0) >= context.run_limit:
            raise ComputeInvariantError("run_count quota is exhausted")
        slot_ordinal = int(used_jobs or 0) + 1
        slot_reserved_at = datetime.now(timezone.utc)
        slot_digest = canonical_document_digest(
            {
                "schema_version": "pre-dispatch-job-slot/v1",
                "contract_revision_id": str(context.revision.id),
                "quota_policy_id": str(context.quota_policy.id),
                "run_count_constraint_id": str(context.run_count_constraint.id),
                "requester_contract_party_id": str(context.party.id),
                "contract_object_id": str(context.contract_object.id),
                "slot_ordinal": slot_ordinal,
                "run_limit": context.run_limit,
                "eligibility_snapshot_digest": (
                    eligibility_snapshot.eligibility_snapshot_digest
                ),
            }
        )
    algorithm_spec_digest = canonical_document_digest(algorithm)
    input_digest = canonical_document_digest(input_snapshot)
    evaluation_digest = canonical_document_digest(context.evaluation)
    request_document = {
        "schema_version": "compute-job-request/v1",
        "space_id": str(context.contract.space_id),
        "contract_revision_id": str(context.revision.id),
        "requester_contract_party_id": str(context.party.id),
        "contract_object_id": str(context.contract_object.id),
        "purpose_code": purpose_code,
        "requested_output_types": outputs,
        "algorithm_spec_digest": algorithm_spec_digest,
        "compute_input_digest": input_digest,
        "eligibility_snapshot_digest": (
            eligibility_snapshot.eligibility_snapshot_digest
            if eligibility_snapshot is not None
            else None
        ),
    }
    if audit_command is None:
        raise AuditEvidenceUnavailable("compute Job creation requires AuditCommandContext")
    existing, command_request_digest = await begin_audited_command(
        session,
        space_id=context.contract.space_id,
        event_type="compute.job.created",
        subject_type="compute_job",
        command=audit_command,
        request_snapshot=request_document,
    )
    if existing is not None:
        replay = await session.get(ComputeJob, existing.subject_id)
        if replay is None or replay.creation_request_digest != canonical_document_digest(
            request_document
        ):
            raise AuditInvariantError("ComputeJob replay subject is unavailable")
        return replay
    job = ComputeJob(
        space_id=context.contract.space_id,
        contract_id=context.contract.id,
        contract_revision_id=context.revision.id,
        revision_content_digest=context.revision.content_digest,
        requester_contract_party_id=context.party.id,
        requester_organization_id=requester_organization_id,
        requester_user_id=requester_user_id,
        contract_object_id=context.contract_object.id,
        purpose_code=purpose_code,
        requested_output_types=outputs,
        algorithm_spec_snapshot=algorithm,
        algorithm_spec_digest=algorithm_spec_digest,
        compute_input_snapshot=input_snapshot,
        compute_input_digest=input_digest,
        creation_authorization_evaluation=context.evaluation,
        creation_authorization_evaluation_digest=evaluation_digest,
        creation_request_digest=canonical_document_digest(request_document),
        execution_eligibility_snapshot_id=(
            eligibility_snapshot.id if eligibility_snapshot is not None else None
        ),
        eligibility_snapshot_digest=(
            eligibility_snapshot.eligibility_snapshot_digest
            if eligibility_snapshot is not None
            else None
        ),
        quota_policy_id=(
            context.quota_policy.id if eligibility_snapshot is not None else None
        ),
        run_count_constraint_id=(
            context.run_count_constraint.id if eligibility_snapshot is not None else None
        ),
        run_limit_snapshot=(
            context.run_limit if eligibility_snapshot is not None else None
        ),
        pre_dispatch_slot_ordinal=slot_ordinal,
        pre_dispatch_slot_digest=slot_digest,
        pre_dispatch_reserved_at=slot_reserved_at,
        status="created",
        created_by=requester_user_id,
    )
    session.add(job)
    await session.flush()
    await _append_command_event_or_rollback(
        session,
        space_id=job.space_id,
        event_type="compute.job.created",
        subject_type="compute_job",
        subject_id=job.id,
        result="success",
        evidence_snapshot={
            "schema_version": "compute-job-created-evidence/v1",
            "command_request_digest": command_request_digest,
            "contract_revision_id": str(job.contract_revision_id),
            "contract_object_id": str(job.contract_object_id),
            "requester_contract_party_id": str(job.requester_contract_party_id),
            "purpose_code": job.purpose_code,
            "algorithm_spec_digest": job.algorithm_spec_digest,
            "compute_input_digest": job.compute_input_digest,
            "authorization_evaluation_digest": (
                job.creation_authorization_evaluation_digest
            ),
            "eligibility_snapshot_id": (
                str(job.execution_eligibility_snapshot_id)
                if job.execution_eligibility_snapshot_id
                else None
            ),
            "eligibility_snapshot_digest": job.eligibility_snapshot_digest,
            "pre_dispatch_slot_ordinal": job.pre_dispatch_slot_ordinal,
            "pre_dispatch_slot_digest": job.pre_dispatch_slot_digest,
        },
        **audit_command.append_kwargs(),
    )
    if eligibility_snapshot is not None and slot_audit_command is not None:
        slot_request = {
            "schema_version": "pre-dispatch-job-slot-command/v1",
            "compute_job_id": str(job.id),
            "eligibility_snapshot_id": str(eligibility_snapshot.id),
            "slot_digest": job.pre_dispatch_slot_digest,
        }
        existing_slot, slot_request_digest = await begin_audited_command(
            session,
            space_id=job.space_id,
            event_type="compute.job.pre_dispatch_slot_reserved",
            subject_type="compute_job",
            command=slot_audit_command,
            request_snapshot=slot_request,
            expected_subject_id=job.id,
        )
        if existing_slot is None:
            await _append_command_event_or_rollback(
                session,
                space_id=job.space_id,
                event_type="compute.job.pre_dispatch_slot_reserved",
                subject_type="compute_job",
                subject_id=job.id,
                result="success",
                evidence_snapshot={
                    "schema_version": "pre-dispatch-job-slot-reserved/v1",
                    "command_request_digest": slot_request_digest,
                    "compute_job_id": str(job.id),
                    "run_count_constraint_id": str(job.run_count_constraint_id),
                    "run_limit": job.run_limit_snapshot,
                    "slot_ordinal": job.pre_dispatch_slot_ordinal,
                    "slot_digest": job.pre_dispatch_slot_digest,
                    "compute_run_created": False,
                },
                **slot_audit_command.append_kwargs(),
            )
    return job


async def validate_compute_job(session: AsyncSession, job: ComputeJob) -> None:
    if job.status not in {"created", "ready"}:
        raise ComputeInvariantError("only created or ready Job can be validated")
    job._transition_validated = True
    job.status = "validating"
    job.row_version += 1
    await session.flush()
    await evaluate_compute_authorization(
        session,
        revision_id=job.contract_revision_id,
        party_id=job.requester_contract_party_id,
        contract_object_id=job.contract_object_id,
        requester_organization_id=job.requester_organization_id,
        requester_user_id=job.requester_user_id,
        purpose_code=job.purpose_code,
        algorithm_digest=job.algorithm_spec_snapshot["algorithm_digest"],
        requested_output_types=job.requested_output_types,
        exclude_job_id=job.id,
    )
    job._transition_validated = True
    job.status = "ready"
    job.validated_at = datetime.now(timezone.utc)
    job.row_version += 1
    await session.flush()


async def prepare_compute_run(
    session: AsyncSession,
    job: ComputeJob,
    *,
    created_by: UUID,
) -> ComputeRun:
    locked_job = await session.scalar(
        select(ComputeJob).where(ComputeJob.id == job.id).with_for_update()
    )
    if locked_job is None or locked_job.status != "ready":
        raise ComputeInvariantError("only a ready Job can prepare a Run")
    nonterminal = await session.scalar(
        select(ComputeRun.id).where(
            ComputeRun.compute_job_id == job.id,
            ComputeRun.status.in_(("prepared", "reserved", "dispatched", "running")),
        )
    )
    if nonterminal is not None:
        raise ComputeInvariantError("Job already has a nonterminal Run")
    max_attempt = await session.scalar(
        select(func.max(ComputeRun.attempt_no)).where(ComputeRun.compute_job_id == job.id)
    )
    run = ComputeRun(
        space_id=job.space_id,
        compute_job_id=job.id,
        contract_id=job.contract_id,
        contract_revision_id=job.contract_revision_id,
        requester_contract_party_id=job.requester_contract_party_id,
        contract_object_id=job.contract_object_id,
        attempt_no=int(max_attempt or 0) + 1,
        status="prepared",
        created_by=created_by,
    )
    session.add(run)
    await session.flush()
    return run


async def cancel_prepared_run(run: ComputeRun) -> None:
    if run.status != "prepared":
        raise ComputeInvariantError("only a prepared Run can be cancelled")
    run._transition_validated = True
    run.status = "cancelled"
    run.finished_at = datetime.now(timezone.utc)
    run.row_version += 1


async def reserve_compute_run(
    session: AsyncSession,
    run: ComputeRun,
    *,
    audit_command: AuditCommandContext | None = None,
) -> None:
    """Atomically reserve quota and append its Audit/outbox command fact."""

    if session.bind is None or session.bind.dialect.name != "postgresql":
        raise AuditEvidenceUnavailable("AuditEvidenceUnavailable")
    job = await session.get(ComputeJob, run.compute_job_id)
    if job is None:
        raise ComputeInvariantError("Run Job does not exist")
    if audit_command is None:
        raise AuditEvidenceUnavailable("AuditEvidenceUnavailable")
    request_document = {
        "schema_version": "compute-run-reservation-command/v1",
        "compute_run_id": str(run.id),
        "compute_job_id": str(job.id),
        "contract_revision_id": str(job.contract_revision_id),
        "contract_object_id": str(job.contract_object_id),
        "attempt_no": run.attempt_no,
    }
    existing, command_request_digest = await begin_audited_command(
        session,
        space_id=job.space_id,
        event_type="compute.run.reserved",
        subject_type="compute_run",
        command=audit_command,
        request_snapshot=request_document,
        expected_subject_id=run.id,
    )
    if existing is not None:
        await session.refresh(run)
        if run.status != "reserved":
            raise AuditInvariantError("reservation event exists but Run is not reserved")
        return
    if run.status != "prepared":
        raise ComputeInvariantError("only a prepared Run can be reserved")
    if job.status != "ready":
        raise ComputeInvariantError("Run Job is not ready")
    context = await evaluate_compute_authorization(
        session,
        revision_id=job.contract_revision_id,
        party_id=job.requester_contract_party_id,
        contract_object_id=job.contract_object_id,
        requester_organization_id=job.requester_organization_id,
        requester_user_id=job.requester_user_id,
        purpose_code=job.purpose_code,
        algorithm_digest=job.algorithm_spec_snapshot["algorithm_digest"],
        requested_output_types=job.requested_output_types,
        exclude_job_id=job.id,
    )
    scope_document = {
        "schema_version": "quota-scope/v1",
        "contract_revision_id": str(job.contract_revision_id),
        "quota_policy_id": str(context.quota_policy.id),
        "requester_contract_party_id": str(job.requester_contract_party_id),
        "contract_object_id": str(job.contract_object_id),
    }
    reservation_document = {
        **scope_document,
        "compute_run_id": str(run.id),
        "attempt_no": run.attempt_no,
        "authorization_evaluation_digest": canonical_document_digest(context.evaluation),
        "execution_environment_digest": canonical_document_digest(context.execution_environment),
    }
    await _append_command_event_or_rollback(
        session,
        space_id=job.space_id,
        event_type="compute.run.reserved",
        subject_type="compute_run",
        subject_id=run.id,
        result="success",
        evidence_snapshot={
            "schema_version": "compute-run-reserved-evidence/v1",
            "command_request_digest": command_request_digest,
            "compute_run_id": str(run.id),
            "compute_job_id": str(job.id),
            "contract_revision_id": str(job.contract_revision_id),
            "contract_object_id": str(job.contract_object_id),
            "attempt_no": run.attempt_no,
            "quota_policy_id": str(context.quota_policy.id),
            "run_count_constraint_id": str(context.run_count_constraint.id),
            "run_limit": context.run_limit,
            "quota_scope_digest": canonical_document_digest(scope_document),
            "quota_reservation_digest": canonical_document_digest(reservation_document),
            "authorization_evaluation_digest": canonical_document_digest(
                context.evaluation
            ),
            "execution_environment_digest": canonical_document_digest(
                context.execution_environment
            ),
        },
        **audit_command.append_kwargs(),
    )
    run.quota_policy_id = context.quota_policy.id
    run.run_count_constraint_id = context.run_count_constraint.id
    run.run_limit_snapshot = context.run_limit
    run.quota_scope_digest = canonical_document_digest(scope_document)
    run.quota_reservation_digest = canonical_document_digest(reservation_document)
    run.start_authorization_evaluation = context.evaluation
    run.start_authorization_evaluation_digest = canonical_document_digest(context.evaluation)
    run.compute_binding_id = context.compute_binding.id
    run.egress_binding_id = context.egress_binding.id
    run.audit_binding_id = context.audit_binding.id
    run.execution_environment_snapshot = context.execution_environment
    run.execution_environment_digest = canonical_document_digest(context.execution_environment)
    run._transition_validated = True
    run._reservation_validated = True
    run.status = "reserved"
    run.row_version += 1
    try:
        await session.flush()
        # PostgreSQL assigns the atomic reservation ordinal in a trigger.
        # Refresh so command callers observe the authoritative quota value in
        # the same transaction instead of a transient ORM-side None.
        await session.refresh(run)
    except DBAPIError as error:
        await session.rollback()
        if "AuditEvidenceUnavailable" in str(error.orig):
            raise AuditEvidenceUnavailable("AuditEvidenceUnavailable") from error
        raise
    except Exception:
        await session.rollback()
        raise


async def _matching_output_policies(
    session: AsyncSession,
    *,
    job: ComputeJob,
    artifact_type: str,
) -> tuple[list[Policy], list[Policy]]:
    policies = list(
        (
            await session.scalars(
                select(Policy).where(
                    Policy.contract_revision_id == job.contract_revision_id,
                    Policy.subject_contract_party_id == job.requester_contract_party_id,
                    Policy.contract_object_id == job.contract_object_id,
                    Policy.action_code == "export_artifact",
                    Policy.effect.in_(("permit", "deny")),
                )
            )
        ).all()
    )
    permits: list[Policy] = []
    denies: list[Policy] = []
    for policy in policies:
        output_constraints = list(
            (
                await session.scalars(
                    select(PolicyConstraint).where(
                        PolicyConstraint.policy_id == policy.id,
                        PolicyConstraint.constraint_name == "output_type",
                    )
                )
            ).all()
        )
        matches = (
            any(_constraint_allows(item, artifact_type) for item in output_constraints)
            if output_constraints
            else policy.effect == "deny"
        )
        if not matches:
            continue
        if policy.policy_digest is None:
            raise ComputeInvariantError("output Policy is not frozen")
        (permits if policy.effect == "permit" else denies).append(policy)
    if not permits:
        raise ComputeInvariantError("Artifact type is outside Contract permit scope")
    return permits, denies


async def evaluate_artifact_output_policy(
    session: AsyncSession,
    *,
    run: ComputeRun,
    artifact_type: str,
) -> tuple[ComputeJob, dict[str, Any], PolicyExecutionBinding, UUID]:
    if run.status != "succeeded":
        raise ComputeInvariantError("Artifact requires a succeeded ComputeRun")
    job = await session.get(ComputeJob, run.compute_job_id)
    if job is None or (
        run.space_id != job.space_id
        or run.contract_revision_id != job.contract_revision_id
        or run.contract_object_id != job.contract_object_id
    ):
        raise ComputeInvariantError("Artifact Run and Job scope is inconsistent")
    if artifact_type not in job.requested_output_types:
        raise ComputeInvariantError("Artifact type was not requested by the Job")

    permits, denies = await _matching_output_policies(
        session, job=job, artifact_type=artifact_type
    )
    bindings = list(
        (
            await session.scalars(
                select(PolicyExecutionBinding).where(
                    PolicyExecutionBinding.policy_id.in_([policy.id for policy in permits]),
                    PolicyExecutionBinding.execution_role == "egress_controller",
                    PolicyExecutionBinding.is_required.is_(True),
                    PolicyExecutionBinding.deployment_status == "accepted",
                )
            )
        ).all()
    )
    if len(bindings) != 1:
        raise ComputeInvariantError("Artifact requires one current egress binding")
    egress_binding = bindings[0]
    now = datetime.now(timezone.utc)
    connector, capability = await _validate_binding_current(
        session, egress_binding, space_id=job.space_id, now=now
    )
    if (
        egress_binding.required_capability_code != "egress_policy_enforcement"
        or egress_binding.required_capability_version != "1.0"
    ):
        raise ComputeInvariantError("Artifact egress capability is inconsistent")

    provider_organization_id = await session.scalar(
        select(DataProduct.provider_organization_id)
        .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
        .join(
            ContractObject,
            ContractObject.data_product_version_id == DataProductVersion.id,
        )
        .where(ContractObject.id == job.contract_object_id)
    )
    if provider_organization_id is None:
        raise ComputeInvariantError("Artifact provider organization cannot be resolved")

    evaluation = {
        "schema_version": "artifact-output-policy/v1",
        "decision": "deny" if denies else "permit",
        "evaluated_at": now.isoformat(),
        "space_id": str(job.space_id),
        "compute_job_id": str(job.id),
        "compute_run_id": str(run.id),
        "contract_revision_id": str(job.contract_revision_id),
        "contract_object_id": str(job.contract_object_id),
        "requester_contract_party_id": str(job.requester_contract_party_id),
        "artifact_type": artifact_type,
        "permit_policy_digests": sorted(policy.policy_digest for policy in permits),
        "deny_policy_digests": sorted(policy.policy_digest for policy in denies),
        "egress_binding_id": str(egress_binding.id),
        "egress_connector_id": str(connector.id),
        "egress_capability_code": capability.capability_code,
        "egress_capability_version": capability.capability_version,
        "provider_organization_id": str(provider_organization_id),
        "run_completion_receipt_digest": run.completion_receipt_digest,
        "run_start_authorization_digest": run.start_authorization_evaluation_digest,
    }
    return job, evaluation, egress_binding, provider_organization_id


async def create_artifact(
    session: AsyncSession,
    *,
    run_id: UUID,
    artifact_type: str,
    content_digest: str,
    storage_reference: str,
    size_bytes: int,
    classification_level: str,
    retention_until: datetime | None = None,
    audit_command: AuditCommandContext | None = None,
) -> Artifact:
    locked_run = await session.scalar(
        select(ComputeRun).where(ComputeRun.id == run_id).with_for_update()
    )
    if locked_run is None:
        raise ComputeInvariantError("ComputeRun does not exist")
    _require_digest(content_digest, "content_digest")
    _validate_storage_reference(storage_reference)
    job, evaluation, _, _ = await evaluate_artifact_output_policy(
        session, run=locked_run, artifact_type=artifact_type
    )
    if audit_command is None:
        raise AuditEvidenceUnavailable("Artifact creation requires AuditCommandContext")
    request_document = {
        "schema_version": "artifact-creation-command/v1",
        "compute_run_id": str(locked_run.id),
        "compute_job_id": str(job.id),
        "artifact_type": artifact_type,
        "content_digest": content_digest,
        "storage_reference_digest": canonical_document_digest(
            {"schema_version": "storage-reference/v1", "reference": storage_reference}
        ),
        "size_bytes": size_bytes,
        "classification_level": classification_level,
        "retention_until": (
            _as_utc(retention_until).isoformat() if retention_until is not None else None
        ),
    }
    existing, command_request_digest = await begin_audited_command(
        session,
        space_id=job.space_id,
        event_type="artifact.created",
        subject_type="artifact",
        command=audit_command,
        request_snapshot=request_document,
    )
    if existing is not None:
        replay = await session.get(Artifact, existing.subject_id)
        if replay is None or replay.compute_run_id != locked_run.id:
            raise AuditInvariantError("Artifact replay subject is unavailable")
        return replay
    max_no = await session.scalar(
        select(func.max(Artifact.artifact_no)).where(Artifact.compute_run_id == run_id)
    )
    artifact = Artifact(
        space_id=job.space_id,
        compute_job_id=job.id,
        compute_run_id=locked_run.id,
        artifact_no=int(max_no or 0) + 1,
        artifact_type=artifact_type,
        content_digest=content_digest,
        storage_reference=storage_reference,
        size_bytes=size_bytes,
        classification_level=classification_level,
        output_policy_evaluation=evaluation,
        output_policy_evaluation_digest=canonical_document_digest(evaluation),
        release_status="quarantined",
        retention_until=retention_until,
    )
    session.add(artifact)
    await session.flush()
    await _append_command_event_or_rollback(
        session,
        space_id=artifact.space_id,
        event_type="artifact.created",
        subject_type="artifact",
        subject_id=artifact.id,
        result="success",
        evidence_snapshot={
            "schema_version": "artifact-created-evidence/v1",
            "command_request_digest": command_request_digest,
            "compute_job_id": str(artifact.compute_job_id),
            "compute_run_id": str(artifact.compute_run_id),
            "artifact_no": artifact.artifact_no,
            "artifact_type": artifact.artifact_type,
            "content_digest": artifact.content_digest,
            "classification_level": artifact.classification_level,
            "output_policy_evaluation_digest": (
                artifact.output_policy_evaluation_digest
            ),
        },
        **audit_command.append_kwargs(),
    )
    return artifact


async def _artifact_provider_organization(
    session: AsyncSession, artifact: Artifact
) -> UUID:
    provider_id = await session.scalar(
        select(DataProduct.provider_organization_id)
        .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
        .join(
            ContractObject,
            ContractObject.data_product_version_id == DataProductVersion.id,
        )
        .join(ComputeJob, ComputeJob.contract_object_id == ContractObject.id)
        .where(
            ComputeJob.id == artifact.compute_job_id,
            ContractObject.contract_revision_id == ComputeJob.contract_revision_id,
        )
    )
    if provider_id is None:
        raise ComputeInvariantError("Artifact provider organization cannot be resolved")
    return provider_id


async def create_artifact_review(
    session: AsyncSession,
    *,
    artifact: Artifact,
    responsible_organization_id: UUID,
    routing_rule_digest: str,
) -> ArtifactReview:
    if artifact.release_status != "quarantined":
        raise ComputeInvariantError("only a quarantined Artifact can be reviewed")
    _require_digest(routing_rule_digest, "routing_rule_digest")
    provider_id = await _artifact_provider_organization(session, artifact)
    if responsible_organization_id != provider_id:
        raise ComputeInvariantError("ArtifactReview must be owned by the provider")
    participant = await session.scalar(
        select(SpaceParticipant).where(
            SpaceParticipant.space_id == artifact.space_id,
            SpaceParticipant.organization_id == responsible_organization_id,
            SpaceParticipant.admission_status == "admitted",
        )
    )
    if participant is None or await session.get(
        SpaceParticipantRole, (participant.id, "provider")
    ) is None:
        raise ComputeInvariantError("provider is not an admitted Space participant")
    review = ArtifactReview(
        space_id=artifact.space_id,
        artifact_id=artifact.id,
        target_content_digest=artifact.content_digest,
        responsible_organization_id=responsible_organization_id,
        status="pending",
        routing_rule_digest=routing_rule_digest,
    )
    session.add(review)
    await session.flush()
    return review


async def claim_artifact_review(
    session: AsyncSession,
    review: ArtifactReview,
    *,
    user_id: UUID,
    claimed_at: datetime | None = None,
) -> None:
    if review.status != "pending":
        raise ComputeInvariantError("only a pending ArtifactReview can be claimed")
    now = claimed_at or datetime.now(timezone.utc)
    member = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == review.responsible_organization_id,
            OrganizationMember.user_id == user_id,
            OrganizationMember.status == "active",
        )
    )
    if member is None:
        raise ComputeInvariantError("reviewer is not an active responsible organization member")
    valid_from = _as_utc(member.valid_from)
    valid_until = _as_utc(member.valid_until)
    if (valid_from is not None and now < valid_from) or (
        valid_until is not None and now >= valid_until
    ):
        raise ComputeInvariantError("reviewer membership is outside its validity window")
    review._transition_validated = True
    review.claimed_by_user_id = user_id
    review.claimed_at = now
    review.status = "claimed"
    review.row_version += 1
    await session.flush()


async def decide_artifact_review(
    session: AsyncSession,
    review: ArtifactReview,
    *,
    decision: str,
    reason_code: str,
    evidence: dict[str, Any],
    comment: str | None = None,
    decided_at: datetime | None = None,
    audit_command: AuditCommandContext | None = None,
) -> None:
    if decision not in ARTIFACT_REVIEW_DECISIONS:
        raise ComputeInvariantError("unknown ArtifactReview decision")
    if not reason_code:
        raise ComputeInvariantError("ArtifactReview decision requires a reason_code")
    if not isinstance(evidence, dict):
        raise ComputeInvariantError("ArtifactReview decision evidence must be a JSON object")
    artifact = await session.get(Artifact, review.artifact_id)
    if artifact is None or artifact.content_digest != review.target_content_digest:
        raise ComputeInvariantError("ArtifactReview target digest is unavailable")
    evaluation = _require_json_object(
        artifact.output_policy_evaluation, "output_policy_evaluation"
    )
    if decision == "approved" and (
        evaluation.get("decision") != "permit"
        or bool(evaluation.get("deny_policy_digests"))
    ):
        raise ComputeInvariantError("Policy deny cannot be overridden by human approval")
    if audit_command is None:
        raise AuditEvidenceUnavailable("ArtifactReview decision requires AuditCommandContext")
    request_document = {
        "schema_version": "artifact-review-decision-command/v1",
        "artifact_review_id": str(review.id),
        "artifact_id": str(artifact.id),
        "target_content_digest": review.target_content_digest,
        "decision": decision,
        "reason_code": reason_code,
        "comment": comment,
        "evidence_digest": canonical_document_digest(evidence),
        "requested_decided_at": (
            _as_utc(decided_at).isoformat() if decided_at is not None else None
        ),
    }
    existing, command_request_digest = await begin_audited_command(
        session,
        space_id=review.space_id,
        event_type="artifact.review.decided",
        subject_type="artifact_review",
        command=audit_command,
        request_snapshot=request_document,
        expected_subject_id=review.id,
    )
    if existing is not None:
        await session.refresh(review)
        if review.status != "decided" or review.decision != decision:
            raise AuditInvariantError("review decision event and business fact differ")
        return
    if review.status != "claimed" or review.claimed_by_user_id is None:
        raise ComputeInvariantError("ArtifactReview decision requires a claimed review")
    decided_at = decided_at or datetime.now(timezone.utc)
    decision_document = {
        "schema_version": "artifact-review-decision/v1",
        "artifact_review_id": str(review.id),
        "artifact_id": str(artifact.id),
        "target_content_digest": review.target_content_digest,
        "responsible_organization_id": str(review.responsible_organization_id),
        "decided_by_user_id": str(review.claimed_by_user_id),
        "decision": decision,
        "reason_code": reason_code,
        "comment": comment,
        "evidence": evidence,
        "decided_at": decided_at.isoformat(),
    }
    review._transition_validated = True
    review.status = "decided"
    review.decision = decision
    review.reason_code = reason_code
    review.comment = comment
    review.decision_evidence = decision_document
    review.decision_digest = canonical_document_digest(decision_document)
    review.decided_at = decided_at
    review.row_version += 1
    await session.flush()
    await _append_command_event_or_rollback(
        session,
        space_id=review.space_id,
        event_type="artifact.review.decided",
        subject_type="artifact_review",
        subject_id=review.id,
        result="success",
        evidence_snapshot={
            "schema_version": "artifact-review-decided-evidence/v1",
            "command_request_digest": command_request_digest,
            "artifact_id": str(artifact.id),
            "target_content_digest": review.target_content_digest,
            "responsible_organization_id": str(review.responsible_organization_id),
            "decided_by_user_id": str(review.claimed_by_user_id),
            "decision": review.decision,
            "reason_code": review.reason_code,
            "decision_digest": review.decision_digest,
        },
        **audit_command.append_kwargs(),
    )


async def release_artifact(session: AsyncSession, artifact: Artifact) -> None:
    """Revalidate immutable evidence, then fail closed until Audit/outbox exists."""

    if artifact.release_status != "quarantined":
        raise ComputeInvariantError("only a quarantined Artifact can be released")
    review = await session.scalar(
        select(ArtifactReview).where(ArtifactReview.artifact_id == artifact.id)
    )
    if review is None or review.status != "decided" or review.decision != "approved":
        raise ComputeInvariantError("Artifact requires one approved terminal review")
    run = await session.get(ComputeRun, artifact.compute_run_id)
    if run is None or run.status != "succeeded":
        raise ComputeInvariantError("Artifact source Run is not succeeded")
    _, evaluation, binding, _ = await evaluate_artifact_output_policy(
        session, run=run, artifact_type=artifact.artifact_type
    )
    if evaluation.get("decision") != "permit" or evaluation.get("deny_policy_digests"):
        raise ComputeInvariantError("current Policy denies Artifact release")
    if str(binding.id) != artifact.output_policy_evaluation.get("egress_binding_id"):
        raise ComputeInvariantError("Artifact egress binding no longer matches")
    raise AuditEvidenceUnavailable("AuditEvidenceUnavailable")
