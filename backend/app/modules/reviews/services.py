from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.reviews.models import (
    REVIEW_CANCEL_REASONS,
    REVIEW_DECISIONS,
    REVIEW_REASON_CODES,
    REVIEW_REMEDIATIONS,
    REVIEW_TYPES,
    ReviewDecision,
    ReviewTask,
)


class ReviewInvariantError(ValueError):
    """Raised when a Review mutation violates a frozen invariant."""


DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_STRUCTURAL_FIELDS = {
    "space_id",
    "review_type",
    "application_id",
    "application_snapshot_id",
    "target_digest",
    "assignee_organization_id",
    "sequence_no",
    "is_required",
    "routing_rule_digest",
    "created_by",
    "created_at",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _require_digest(value: str | None, field_name: str) -> None:
    if not DIGEST_PATTERN.fullmatch(value or ""):
        raise ReviewInvariantError(f"{field_name} must be sha256:<64 lowercase hex>")


def _validate_task_shape(task: ReviewTask) -> None:
    status = task.task_status or "pending"
    if task.review_type not in REVIEW_TYPES:
        raise ReviewInvariantError("unknown review type")
    _require_digest(task.target_digest, "target_digest")
    _require_digest(task.routing_rule_digest, "routing_rule_digest")
    if task.sequence_no is None or task.sequence_no <= 0:
        raise ReviewInvariantError("sequence_no must be positive")
    if task.row_version is not None and task.row_version < 1:
        raise ReviewInvariantError("row_version must be positive")

    if status == "pending":
        valid = (
            task.assignee_user_id is None
            and task.claimed_at is None
            and task.decided_at is None
            and task.cancelled_at is None
            and task.cancel_reason is None
        )
    elif status == "claimed":
        valid = (
            task.assignee_user_id is not None
            and task.claimed_at is not None
            and task.decided_at is None
            and task.cancelled_at is None
            and task.cancel_reason is None
        )
    elif status == "decided":
        valid = (
            task.assignee_user_id is not None
            and task.claimed_at is not None
            and task.decided_at is not None
            and task.cancelled_at is None
            and task.cancel_reason is None
        )
    elif status == "cancelled":
        valid = (
            task.decided_at is None
            and task.cancelled_at is not None
            and task.cancel_reason in REVIEW_CANCEL_REASONS
            and ((task.assignee_user_id is None) == (task.claimed_at is None))
        )
    else:
        valid = False
    if not valid:
        raise ReviewInvariantError(f"invalid review task shape for status {status}")


def _validate_new_task(task: ReviewTask) -> None:
    if task.task_status not in (None, "pending"):
        raise ReviewInvariantError("new review task must start as pending")
    _validate_task_shape(task)


def _guard_task_update(task: ReviewTask) -> None:
    changed = _changed_columns(task)
    if not changed:
        return
    if changed & TASK_STRUCTURAL_FIELDS:
        raise ReviewInvariantError("review task target and routing fields are immutable")

    old_status = _old_value(task, "task_status", task.task_status)
    allowed = {
        "pending": {"claimed", "cancelled"},
        "claimed": {"pending", "decided", "cancelled"},
        "decided": set(),
        "cancelled": set(),
    }
    if task.task_status not in allowed.get(old_status, set()):
        raise ReviewInvariantError(
            f"invalid review task transition: {old_status} -> {task.task_status}"
        )
    _validate_task_shape(task)


def _validate_decision(decision: ReviewDecision) -> None:
    if decision.decision not in REVIEW_DECISIONS:
        raise ReviewInvariantError("unknown review decision")
    if decision.reason_code is not None and decision.reason_code not in REVIEW_REASON_CODES:
        raise ReviewInvariantError("unknown review reason code")
    if decision.remediation is not None and decision.remediation not in REVIEW_REMEDIATIONS:
        raise ReviewInvariantError("unknown review remediation")
    if decision.decision == "approved":
        if decision.reason_code is not None or decision.remediation is not None:
            raise ReviewInvariantError(
                "approved decision cannot include a rejection reason or remediation"
            )
    elif decision.reason_code is None:
        raise ReviewInvariantError("rejected decision requires a reason code")
    if not isinstance(decision.evidence, dict):
        raise ReviewInvariantError("decision evidence must be a JSON object")
    try:
        json.dumps(decision.evidence, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ReviewInvariantError("decision evidence must be canonical JSON") from error
    _require_digest(decision.target_digest, "target_digest")
    _require_digest(decision.decision_digest, "decision_digest")


@event.listens_for(Session, "before_flush")
def guard_review_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.new:
        if isinstance(target, ReviewTask):
            _validate_new_task(target)
        elif isinstance(target, ReviewDecision):
            _validate_decision(target)

    for target in session.dirty:
        if isinstance(target, ReviewTask):
            _guard_task_update(target)
        elif isinstance(target, ReviewDecision):
            raise ReviewInvariantError("review decision is append-only")

    for target in session.deleted:
        if isinstance(target, ReviewTask):
            raise ReviewInvariantError("review task cannot be deleted")
        if isinstance(target, ReviewDecision):
            raise ReviewInvariantError("review decision is append-only")


def claim_review_task(
    task: ReviewTask, *, user_id: UUID, claimed_at: datetime | None = None
) -> None:
    if task.task_status != "pending":
        raise ReviewInvariantError("only a pending review task can be claimed")
    task.assignee_user_id = user_id
    task.claimed_at = claimed_at or _utc_now()
    task.task_status = "claimed"
    task.row_version += 1


def release_review_task(task: ReviewTask) -> None:
    if task.task_status != "claimed":
        raise ReviewInvariantError("only a claimed review task can be released")
    task.assignee_user_id = None
    task.claimed_at = None
    task.task_status = "pending"
    task.row_version += 1


def cancel_review_task(
    task: ReviewTask,
    *,
    reason: str,
    cancelled_at: datetime | None = None,
) -> None:
    if task.task_status not in {"pending", "claimed"}:
        raise ReviewInvariantError("only an open review task can be cancelled")
    if reason not in REVIEW_CANCEL_REASONS:
        raise ReviewInvariantError("unknown review cancellation reason")
    task.task_status = "cancelled"
    task.cancel_reason = reason
    task.cancelled_at = cancelled_at or _utc_now()
    task.row_version += 1


def canonical_decision_digest(
    *,
    task: ReviewTask,
    decision: str,
    reason_code: str | None,
    comment: str | None,
    remediation: str | None,
    evidence: dict[str, Any],
    decided_by_user_id: UUID,
    decided_for_organization_id: UUID,
    decided_at: datetime,
) -> str:
    document = {
        "schema_version": "1.0",
        "review_task_id": str(task.id),
        "review_type": task.review_type,
        "target_digest": task.target_digest,
        "decision": decision,
        "reason_code": reason_code,
        "comment": comment,
        "remediation": remediation,
        "evidence": evidence,
        "decided_by_user_id": str(decided_by_user_id),
        "decided_for_organization_id": str(decided_for_organization_id),
        "decided_at": decided_at.astimezone(timezone.utc).isoformat(),
    }
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


async def submit_review_decision(
    session: AsyncSession,
    task: ReviewTask,
    *,
    decision: str,
    decided_by_user_id: UUID,
    decided_for_organization_id: UUID,
    reason_code: str | None = None,
    comment: str | None = None,
    remediation: str | None = None,
    evidence: dict[str, Any] | None = None,
    decided_at: datetime | None = None,
) -> ReviewDecision:
    """Append one final decision, then close its claimed task in one transaction."""

    if task.task_status != "claimed":
        raise ReviewInvariantError("review decision requires a claimed task")
    if task.assignee_user_id != decided_by_user_id:
        raise ReviewInvariantError("decision user must be the current assignee")
    if task.assignee_organization_id != decided_for_organization_id:
        raise ReviewInvariantError("decision organization must own the task")

    decided_at = decided_at or _utc_now()
    evidence = evidence or {}
    record = ReviewDecision(
        review_task_id=task.id,
        decision=decision,
        reason_code=reason_code,
        comment=comment,
        remediation=remediation,
        decided_by_user_id=decided_by_user_id,
        decided_for_organization_id=decided_for_organization_id,
        target_digest=task.target_digest,
        evidence=evidence,
        decision_digest=canonical_decision_digest(
            task=task,
            decision=decision,
            reason_code=reason_code,
            comment=comment,
            remediation=remediation,
            evidence=evidence,
            decided_by_user_id=decided_by_user_id,
            decided_for_organization_id=decided_for_organization_id,
            decided_at=decided_at,
        ),
        decided_at=decided_at,
        created_at=decided_at,
    )
    _validate_decision(record)
    session.add(record)
    await session.flush()

    task.task_status = "decided"
    task.decided_at = decided_at
    task.row_version += 1
    await session.flush()
    return record
