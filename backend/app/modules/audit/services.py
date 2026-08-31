from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event, inspect, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.connectors.models import Connector
from app.modules.identity.models import OrganizationMember, User
from app.modules.spaces.models import Space

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ALLOWED_SYSTEM_SERVICES = {
    "medtrust.contract",
    "medtrust.compute",
    "medtrust.artifact",
    "medtrust.audit",
    "medtrust.marketplace",
}
EVENT_SHAPES = {
    "contract.revision.activated": ("contract_revision", "success"),
    "compute.job.created": ("compute_job", "success"),
    "compute.run.reserved": ("compute_run", "success"),
    "compute.run.dispatched": ("compute_run", "success"),
    "compute.run.started": ("compute_run", "success"),
    "compute.run.completed": ("compute_run", "success"),
    "compute.run.failed": ("compute_run", "failure"),
    "compute.run.interrupted": ("compute_run", "interrupted"),
    "artifact.created": ("artifact", "success"),
    "artifact.review.decided": ("artifact_review", "success"),
    "artifact.released": ("artifact", "success"),
    "data_product.version.created": ("data_product_version", "success"),
    "data_product.version.updated": ("data_product_version", "success"),
    "data_product.version.submitted": ("data_product_version", "success"),
    "data_product.version.returned": ("data_product_version", "success"),
    "data_product.version.approved": ("data_product_version", "success"),
    "data_product.version.published": ("data_product_version", "success"),
    "model_product.version.created": ("model_version", "success"),
    "model_product.version.updated": ("model_version", "success"),
    "model_product.version.submitted": ("model_version", "success"),
    "model_product.version.returned": ("model_version", "success"),
    "model_product.version.approved": ("model_version", "success"),
    "model_product.version.published": ("model_version", "success"),
    "application.created": ("application", "success"),
    "application.updated": ("application", "success"),
    "application.compatibility.checked": ("application", "success"),
    "application.submitted": ("application", "success"),
    "application.review.decided": ("review_decision", "success"),
    "application.returned": ("application", "success"),
    "application.rejected": ("application", "success"),
    "application.approved": ("application", "success"),
    "service_access.request.created": ("service_access_request", "success"),
    "service_access.provider.approved": ("service_access_request", "success"),
    "service_access.provider.rejected": ("service_access_request", "denied"),
    "service_access.operator.approved": ("service_access_request", "success"),
    "service_access.operator.rejected": ("service_access_request", "denied"),
    "commercial.order.created": ("commercial_order", "success"),
    "commercial.agreement.accepted": ("commercial_order", "success"),
    "commercial.payment.succeeded": ("commercial_payment", "success"),
    "commercial.fulfillment.created": ("commercial_fulfillment", "success"),
    "commercial.download.grant.created": ("commercial_download_grant", "success"),
    "commercial.download.completed": ("commercial_download_grant", "success"),
    "contract.draft.generated": ("contract_revision", "success"),
    "contract.policy.converged": ("contract_revision", "success"),
    "contract.revision.proposed": ("contract_revision", "success"),
    "contract.revision.signed": ("contract_revision", "success"),
    "contract.readiness.confirmed": ("contract_readiness", "success"),
    "contract.readiness.revoked": (
        "contract_readiness_revocation",
        "success",
    ),
    "execution.eligibility.passed": ("execution_eligibility", "success"),
    "execution.eligibility.blocked": ("contract_revision", "denied"),
    "execution.eligibility.invalidated": (
        "execution_eligibility_invalidation",
        "success",
    ),
    "compute.job.pre_dispatch_slot_reserved": ("compute_job", "success"),
    "artifact.review.plan.created": ("artifact", "success"),
    "artifact.multiparty_review.decided": ("artifact_review_decision", "success"),
    "result.package.created": ("result_package", "success"),
    "result.download.grant.created": ("result_download_grant", "success"),
    "result.download.completed": ("result_download_grant", "success"),
    "result.download.rejected": ("result_download_grant", "denied"),
    "external_catalog.sync.succeeded": ("external_catalog_sync_run", "success"),
    "external_catalog.sync.not_modified": ("external_catalog_sync_run", "success"),
    "external_catalog.sync.failed": ("external_catalog_sync_run", "failure"),
    "external_catalog.source.created": ("external_catalog_source", "success"),
    "external_catalog.sync.started": ("external_catalog_sync_run", "success"),
    "external_catalog.governance.profile.initialized": ("external_catalog_source", "success"),
    "external_catalog.governance.initialized": ("external_catalog_source", "success"),
    "external_catalog.governance.recalculated": ("external_catalog_source", "success"),
    "external_catalog.governance.review.created": ("external_catalog_source", "success"),
    "external_catalog.governance.review.superseded": ("external_catalog_source", "success"),
    "external_catalog.duplicate.resolved": ("external_catalog_source", "success"),
    "external_catalog.governance.duplicate.resolved": ("external_catalog_source", "success"),
    "external_catalog.productization.eligibility.changed": ("external_catalog_source", "success"),
    "external_catalog.product.submitted": ("data_product_version", "success"),
    "external_catalog.product.published": ("data_product_version", "success"),
    "external_catalog.product.publication.rejected": ("data_product_version", "denied"),
    "external_model_catalog.governance.profile.initialized": ("external_catalog_source", "success"),
    "external_model_catalog.governance.review.created": ("external_catalog_source", "success"),
    "external_model_catalog.governance.review.superseded": ("external_catalog_source", "success"),
    "external_model_catalog.family.resolved": ("external_catalog_source", "success"),
    "external_model_catalog.governance.recalculated": ("external_catalog_source", "success"),
    "external_model_catalog.productization.eligibility.changed": ("external_catalog_source", "success"),
    "external_model_catalog.product.submitted": ("model_version", "success"),
    "external_model_catalog.product.published": ("model_version", "success"),
    "external_model_catalog.product.publication.rejected": ("model_version", "denied"),
    "dataset_model_relation.created": ("dataset_model_relation", "success"),
    "dataset_model_evidence.created": ("dataset_model_relation", "success"),
    "dataset_model_evidence.superseded": ("dataset_model_relation", "success"),
    "dataset_model_evidence.execution_backfilled": ("dataset_model_relation", "success"),
    "dataset_model_evidence.verification_backfilled": ("dataset_model_relation", "success"),
    "dataset_model_relation.status_changed": ("dataset_model_relation", "success"),
    "dataset_model_relation.publication_changed": ("dataset_model_relation", "success"),
    "asset_materialization.plan.created": ("asset_materialization_plan", "success"),
    "asset_materialization.plan.submitted": ("asset_materialization_plan", "success"),
    "asset_materialization.plan.approved": ("asset_materialization_plan", "success"),
    "asset_materialization.plan.rejected": ("asset_materialization_plan", "denied"),
    "asset_materialization.plan.cancelled": ("asset_materialization_plan", "success"),
    "asset_materialization.plan.superseded": ("asset_materialization_plan", "success"),
}
for _lifecycle_event in (
    "data_product.unpublish.requested",
    "data_product.unpublish.approved",
    "data_product.unpublish.rejected",
    "data_product.unpublish.returned",
    "data_product.unpublished",
    "data_product.relist.requested",
    "data_product.relist.approved",
    "data_product.relist.rejected",
    "data_product.relist.returned",
    "data_product.republished",
    "data_product.deletion.requested",
    "data_product.deletion.approved",
    "data_product.deletion.rejected",
    "data_product.deletion.returned",
    "data_product.archived",
    "model_product.unpublish.requested",
    "model_product.unpublish.approved",
    "model_product.unpublish.rejected",
    "model_product.unpublish.returned",
    "model_product.unpublished",
    "model_product.relist.requested",
    "model_product.relist.approved",
    "model_product.relist.rejected",
    "model_product.relist.returned",
    "model_product.republished",
    "model_product.deletion.requested",
    "model_product.deletion.approved",
    "model_product.deletion.rejected",
    "model_product.deletion.returned",
    "model_product.archived",
    "product.lifecycle.cancelled",
):
    EVENT_SHAPES[_lifecycle_event] = ("product_lifecycle_request", "success")
EVENT_TARGETS = {
    "contract.revision.activated": (("medtrust.audit.v1", "audit.timeline"),),
    "compute.job.created": (("medtrust.audit.v1", "audit.timeline"),),
    "compute.run.reserved": (
        ("medtrust.audit.v1", "audit.timeline"),
        ("medtrust.compute.dispatch.v1", "compute.dispatch"),
    ),
    "compute.run.dispatched": (("medtrust.audit.v1", "audit.timeline"),),
    "compute.run.started": (("medtrust.audit.v1", "audit.timeline"),),
    "compute.run.completed": (("medtrust.audit.v1", "audit.timeline"),),
    "compute.run.failed": (("medtrust.audit.v1", "audit.timeline"),),
    "compute.run.interrupted": (("medtrust.audit.v1", "audit.timeline"),),
    "artifact.created": (
        ("medtrust.audit.v1", "audit.timeline"),
        ("medtrust.artifact.review.v1", "artifact.review-routing"),
    ),
    "artifact.review.decided": (
        ("medtrust.audit.v1", "audit.timeline"),
        (
            "medtrust.artifact.release-evaluation.v1",
            "artifact.release-evaluation",
        ),
    ),
    "artifact.released": (
        ("medtrust.audit.v1", "audit.timeline"),
        ("medtrust.artifact.delivery.v1", "artifact.delivery-notification"),
    ),
    "data_product.version.created": (("medtrust.audit.v1", "audit.timeline"),),
    "data_product.version.updated": (("medtrust.audit.v1", "audit.timeline"),),
    "data_product.version.submitted": (("medtrust.audit.v1", "audit.timeline"),),
    "data_product.version.returned": (("medtrust.audit.v1", "audit.timeline"),),
    "data_product.version.approved": (("medtrust.audit.v1", "audit.timeline"),),
    "data_product.version.published": (("medtrust.audit.v1", "audit.timeline"),),
    "model_product.version.created": (("medtrust.audit.v1", "audit.timeline"),),
    "model_product.version.updated": (("medtrust.audit.v1", "audit.timeline"),),
    "model_product.version.submitted": (("medtrust.audit.v1", "audit.timeline"),),
    "model_product.version.returned": (("medtrust.audit.v1", "audit.timeline"),),
    "model_product.version.approved": (("medtrust.audit.v1", "audit.timeline"),),
    "model_product.version.published": (("medtrust.audit.v1", "audit.timeline"),),
    "application.created": (("medtrust.audit.v1", "audit.timeline"),),
    "application.updated": (("medtrust.audit.v1", "audit.timeline"),),
    "application.compatibility.checked": (("medtrust.audit.v1", "audit.timeline"),),
    "application.submitted": (("medtrust.audit.v1", "audit.timeline"),),
    "application.review.decided": (("medtrust.audit.v1", "audit.timeline"),),
    "application.returned": (("medtrust.audit.v1", "audit.timeline"),),
    "application.rejected": (("medtrust.audit.v1", "audit.timeline"),),
    "application.approved": (("medtrust.audit.v1", "audit.timeline"),),
    "contract.draft.generated": (("medtrust.audit.v1", "audit.timeline"),),
    "contract.policy.converged": (("medtrust.audit.v1", "audit.timeline"),),
    "contract.revision.proposed": (("medtrust.audit.v1", "audit.timeline"),),
    "contract.revision.signed": (("medtrust.audit.v1", "audit.timeline"),),
    "contract.readiness.confirmed": (("medtrust.audit.v1", "audit.timeline"),),
    "contract.readiness.revoked": (("medtrust.audit.v1", "audit.timeline"),),
    "execution.eligibility.passed": (("medtrust.audit.v1", "audit.timeline"),),
    "execution.eligibility.blocked": (("medtrust.audit.v1", "audit.timeline"),),
    "execution.eligibility.invalidated": (("medtrust.audit.v1", "audit.timeline"),),
    "compute.job.pre_dispatch_slot_reserved": (
        ("medtrust.audit.v1", "audit.timeline"),
    ),
    "artifact.review.plan.created": (("medtrust.audit.v1", "audit.timeline"),),
    "artifact.multiparty_review.decided": (("medtrust.audit.v1", "audit.timeline"),),
    "result.package.created": (("medtrust.audit.v1", "audit.timeline"),),
    "result.download.grant.created": (("medtrust.audit.v1", "audit.timeline"),),
    "result.download.completed": (("medtrust.audit.v1", "audit.timeline"),),
    "result.download.rejected": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.sync.succeeded": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.sync.not_modified": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.sync.failed": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.source.created": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.sync.started": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.governance.profile.initialized": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.governance.initialized": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.governance.recalculated": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.governance.review.created": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.governance.review.superseded": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.duplicate.resolved": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.governance.duplicate.resolved": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.productization.eligibility.changed": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.product.submitted": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.product.published": (("medtrust.audit.v1", "audit.timeline"),),
    "external_catalog.product.publication.rejected": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.governance.profile.initialized": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.governance.review.created": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.governance.review.superseded": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.family.resolved": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.governance.recalculated": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.productization.eligibility.changed": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.product.submitted": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.product.published": (("medtrust.audit.v1", "audit.timeline"),),
    "external_model_catalog.product.publication.rejected": (("medtrust.audit.v1", "audit.timeline"),),
}
for _lifecycle_event in tuple(EVENT_SHAPES):
    if _lifecycle_event not in EVENT_TARGETS:
        EVENT_TARGETS[_lifecycle_event] = (("medtrust.audit.v1", "audit.timeline"),)

AUDIT_STABLE_FIELDS = {
    "event_id",
    "space_id",
    "stream_sequence",
    "event_type",
    "schema_version",
    "canonicalization_version",
    "occurred_at",
    "actor_type",
    "actor_organization_id",
    "actor_user_id",
    "actor_connector_id",
    "actor_service_code",
    "subject_type",
    "subject_id",
    "result",
    "correlation_id",
    "causation_id",
    "command_id",
    "idempotency_key",
    "evidence_snapshot",
    "evidence_digest",
    "previous_event_digest",
    "event_digest",
    "created_at",
}
OUTBOX_STABLE_FIELDS = {
    "message_id",
    "audit_event_id",
    "space_id",
    "topic",
    "destination",
    "message_schema_version",
    "payload_snapshot",
    "payload_digest",
    "idempotency_key",
    "created_at",
}
OUTBOX_TERMINAL_STATUSES = {"published", "dead_letter"}
SENSITIVE_KEYS = {
    "patient_name",
    "patient_id",
    "patient_identifier",
    "mrn",
    "medical_record_number",
    "pathology_number",
    "wsi_path",
    "pacs_path",
    "lis_path",
    "emr_path",
    "object_path",
    "presigned_url",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "access_key",
    "secret_key",
    "credential",
    "private_key",
}
SENSITIVE_ERROR_PATTERN = re.compile(
    r"(?i)(authorization|bearer|token|secret|password|access[_-]?key|"
    r"x-amz-signature|signature)\s*[:=]\s*[^\s,;]+"
)
AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?[^\s,;]+(?:\s+[^\s,;]+)?"
)
URL_QUERY_PATTERN = re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+")


class AuditInvariantError(ValueError):
    """Raised when Audit/outbox metadata violates the v8 freeze."""


class IdempotencyConflict(AuditInvariantError):
    """Raised when a repeated command key carries different immutable facts."""


@dataclass(frozen=True)
class AuditAppendResult:
    event: AuditEvent
    messages: tuple[OutboxMessage, ...]
    created: bool


@dataclass(frozen=True)
class AuditCommandContext:
    """Stable caller-supplied identity for one retryable business command."""

    command_id: UUID
    idempotency_key: str
    correlation_id: UUID
    actor_type: str
    actor_organization_id: UUID | None = None
    actor_user_id: UUID | None = None
    actor_connector_id: UUID | None = None
    actor_service_code: str | None = None
    causation_id: UUID | None = None

    def append_kwargs(self) -> dict[str, Any]:
        return {
            "actor_type": self.actor_type,
            "actor_organization_id": self.actor_organization_id,
            "actor_user_id": self.actor_user_id,
            "actor_connector_id": self.actor_connector_id,
            "actor_service_code": self.actor_service_code,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
        }


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise AuditInvariantError(f"{path} cannot contain non-integer numbers")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AuditInvariantError(f"{path} JSON object keys must be strings")
            if key.lower() in SENSITIVE_KEYS:
                raise AuditInvariantError(f"{path}.{key} is forbidden in audit evidence")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise AuditInvariantError(f"{path} contains a non-JSON value")


def canonical_json_text_v1(document: dict[str, Any]) -> str:
    if not isinstance(document, dict):
        raise AuditInvariantError("canonical JSON root must be an object")
    _validate_json_value(document)
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(canonical.encode("utf-8")) > 65536:
        raise AuditInvariantError("canonical JSON exceeds 64 KiB")
    return canonical


def canonical_json_digest_v1(document: dict[str, Any]) -> str:
    canonical = canonical_json_text_v1(document).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def digest_idempotency_key(raw_key: str) -> str:
    if not raw_key or len(raw_key) > 512:
        raise AuditInvariantError("raw idempotency key must contain 1-512 characters")
    return f"sha256:{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()}"


async def begin_audited_command(
    session: AsyncSession,
    *,
    space_id: UUID,
    event_type: str,
    subject_type: str,
    command: AuditCommandContext,
    request_snapshot: dict[str, Any],
    expected_subject_id: UUID | None = None,
) -> tuple[AuditEvent | None, str]:
    """Serialize a command in its Space and resolve an exact committed replay.

    This must run before a create command mutates business state.  The Space row
    lock is also the Audit stream lock, so concurrent retries cannot create two
    different business subjects before either command fact becomes visible.
    """

    expected_shape = EVENT_SHAPES.get(event_type)
    if expected_shape is None or expected_shape[0] != subject_type:
        raise AuditInvariantError("event type and subject type are inconsistent")
    _require_digest(command.idempotency_key, "idempotency_key")
    request_digest = canonical_json_digest_v1(request_snapshot)
    locked_space = await session.scalar(
        select(Space).where(Space.id == space_id).with_for_update()
    )
    if locked_space is None:
        raise AuditInvariantError("audited command Space does not exist")

    key_rows = list(
        (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.space_id == space_id,
                    AuditEvent.idempotency_key == command.idempotency_key,
                )
            )
        ).all()
    )
    if any(
        row.command_id != command.command_id
        or row.correlation_id != command.correlation_id
        for row in key_rows
    ):
        raise IdempotencyConflict("idempotency key maps to another command")
    command_rows = list(
        (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.space_id == space_id,
                    AuditEvent.command_id == command.command_id,
                )
            )
        ).all()
    )
    if any(
        row.idempotency_key != command.idempotency_key
        or row.correlation_id != command.correlation_id
        for row in command_rows
    ):
        raise IdempotencyConflict("command_id maps to another idempotency context")

    matches = [row for row in key_rows if row.event_type == event_type]
    if len(matches) > 1:
        raise IdempotencyConflict("command has multiple facts for the same event type")
    if not matches:
        return None, request_digest
    existing = matches[0]
    actor_fields = (
        ("actor_type", command.actor_type),
        ("actor_organization_id", command.actor_organization_id),
        ("actor_user_id", command.actor_user_id),
        ("actor_connector_id", command.actor_connector_id),
        ("actor_service_code", command.actor_service_code),
        ("causation_id", command.causation_id),
    )
    if any(getattr(existing, name) != value for name, value in actor_fields):
        raise IdempotencyConflict("command replay actor or causation context differs")
    if existing.subject_type != subject_type:
        raise IdempotencyConflict("command replay subject type differs")
    if expected_subject_id is not None and existing.subject_id != expected_subject_id:
        raise IdempotencyConflict("command replay subject differs")
    if existing.evidence_snapshot.get("command_request_digest") != request_digest:
        raise IdempotencyConflict("idempotency key was reused with a different request")
    expected_targets = set(EVENT_TARGETS[event_type])
    messages = list(
        (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.audit_event_id == existing.event_id
                )
            )
        ).all()
    )
    if {(row.topic, row.destination) for row in messages} != expected_targets:
        raise IdempotencyConflict("replayed command has an inconsistent Outbox target set")
    return existing, request_digest


def _require_digest(value: str, name: str) -> None:
    if not DIGEST_PATTERN.fullmatch(value):
        raise AuditInvariantError(f"{name} must be sha256:<64 lowercase hex>")


def _changed_columns(target: object) -> set[str]:
    state = inspect(target)
    return {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }


def _event_manifest(event_row: AuditEvent) -> dict[str, Any]:
    return {
        "event_id": str(event_row.event_id),
        "space_id": str(event_row.space_id),
        "stream_sequence": event_row.stream_sequence,
        "previous_event_digest": event_row.previous_event_digest,
        "event_type": event_row.event_type,
        "schema_version": event_row.schema_version,
        "canonicalization_version": event_row.canonicalization_version,
        "occurred_at": _canonical_timestamp(event_row.occurred_at),
        "actor_type": event_row.actor_type,
        "actor_organization_id": (
            str(event_row.actor_organization_id)
            if event_row.actor_organization_id is not None
            else None
        ),
        "actor_user_id": (
            str(event_row.actor_user_id) if event_row.actor_user_id is not None else None
        ),
        "actor_connector_id": (
            str(event_row.actor_connector_id)
            if event_row.actor_connector_id is not None
            else None
        ),
        "actor_service_code": event_row.actor_service_code,
        "subject_type": event_row.subject_type,
        "subject_id": str(event_row.subject_id),
        "result": event_row.result,
        "correlation_id": str(event_row.correlation_id),
        "causation_id": (
            str(event_row.causation_id) if event_row.causation_id is not None else None
        ),
        "command_id": str(event_row.command_id),
        "idempotency_key": event_row.idempotency_key,
        "evidence_digest": event_row.evidence_digest,
    }


def _validate_actor_shape(event_row: AuditEvent) -> None:
    if event_row.actor_type == "user":
        valid = (
            event_row.actor_organization_id is not None
            and event_row.actor_user_id is not None
            and event_row.actor_connector_id is None
            and event_row.actor_service_code is None
        )
    elif event_row.actor_type == "connector":
        valid = (
            event_row.actor_organization_id is not None
            and event_row.actor_user_id is None
            and event_row.actor_connector_id is not None
            and event_row.actor_service_code is None
        )
    elif event_row.actor_type == "system":
        valid = (
            event_row.actor_user_id is None
            and event_row.actor_connector_id is None
            and event_row.actor_service_code in ALLOWED_SYSTEM_SERVICES
        )
    else:
        valid = False
    if not valid:
        raise AuditInvariantError("invalid AuditEvent actor shape")


def _validate_event_shape(event_row: AuditEvent) -> None:
    expected = EVENT_SHAPES.get(event_row.event_type)
    if expected is None or expected != (event_row.subject_type, event_row.result):
        raise AuditInvariantError("event type, subject type or result is inconsistent")
    if event_row.schema_version != 1:
        raise AuditInvariantError("AuditEvent schema_version must be 1")
    if event_row.canonicalization_version != "medtrust-jsonb-c14n/v1":
        raise AuditInvariantError("unsupported canonicalization version")
    if event_row.stream_sequence <= 0:
        raise AuditInvariantError("stream_sequence must be positive")
    if (event_row.stream_sequence == 1) != (event_row.previous_event_digest is None):
        raise AuditInvariantError("AuditEvent chain shape is invalid")
    _validate_actor_shape(event_row)
    _require_digest(event_row.idempotency_key, "idempotency_key")
    _require_digest(event_row.evidence_digest, "evidence_digest")
    _require_digest(event_row.event_digest, "event_digest")
    if event_row.previous_event_digest is not None:
        _require_digest(event_row.previous_event_digest, "previous_event_digest")
    if canonical_json_digest_v1(event_row.evidence_snapshot) != event_row.evidence_digest:
        raise AuditInvariantError("evidence_digest does not match evidence_snapshot")
    if canonical_json_digest_v1(_event_manifest(event_row)) != event_row.event_digest:
        raise AuditInvariantError("event_digest does not match event manifest")


def _validate_outbox_shape(message: OutboxMessage) -> None:
    if message.status not in {"pending", "processing", "published", "dead_letter"}:
        raise AuditInvariantError("invalid Outbox status")
    if not 0 <= message.attempt_count <= 10:
        raise AuditInvariantError("Outbox attempt_count is outside 0-10")
    if message.message_schema_version != 1:
        raise AuditInvariantError("Outbox message_schema_version must be 1")
    _require_digest(message.payload_digest, "payload_digest")
    _require_digest(message.idempotency_key, "outbox idempotency_key")
    if canonical_json_digest_v1(message.payload_snapshot) != message.payload_digest:
        raise AuditInvariantError("payload_digest does not match payload_snapshot")
    if message.status == "pending":
        valid = (
            message.locked_at is None
            and message.lock_owner is None
            and message.lease_expires_at is None
            and message.published_at is None
        )
    elif message.status == "processing":
        valid = (
            message.locked_at is not None
            and message.lock_owner is not None
            and message.lease_expires_at is not None
            and message.published_at is None
        )
    elif message.status == "published":
        valid = (
            message.locked_at is None
            and message.lock_owner is None
            and message.lease_expires_at is None
            and message.published_at is not None
            and message.last_error is None
        )
    else:
        valid = (
            message.locked_at is None
            and message.lock_owner is None
            and message.lease_expires_at is None
            and message.published_at is None
        )
    if not valid:
        raise AuditInvariantError(f"invalid Outbox delivery shape for {message.status}")


@event.listens_for(Session, "before_flush")
def guard_audit_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.deleted:
        if isinstance(target, AuditEvent):
            raise AuditInvariantError("AuditEvent cannot be deleted")
        if isinstance(target, OutboxMessage):
            raise AuditInvariantError("OutboxMessage cannot be deleted")

    for target in session.new:
        if isinstance(target, AuditEvent):
            if getattr(target, "_append_validated", False) is not True:
                raise AuditInvariantError("AuditEvent must use the append service")
            _validate_event_shape(target)
        elif isinstance(target, OutboxMessage):
            if getattr(target, "_enqueue_validated", False) is not True:
                raise AuditInvariantError("OutboxMessage must use the append service")
            if target.status != "pending" or target.attempt_count != 0:
                raise AuditInvariantError("new OutboxMessage must start pending")
            _validate_outbox_shape(target)

    for target in session.dirty:
        if isinstance(target, AuditEvent) and _changed_columns(target):
            raise AuditInvariantError("AuditEvent is append-only")
        if not isinstance(target, OutboxMessage):
            continue
        changed = _changed_columns(target)
        if not changed:
            continue
        if changed & OUTBOX_STABLE_FIELDS:
            raise AuditInvariantError("OutboxMessage identity and payload are immutable")
        state = inspect(target)
        old_status = (
            state.attrs.status.history.deleted[0]
            if state.attrs.status.history.deleted
            else target.status
        )
        if old_status in OUTBOX_TERMINAL_STATUSES:
            raise AuditInvariantError("terminal OutboxMessage is immutable")
        if getattr(target, "_delivery_transition_validated", False) is not True:
            raise AuditInvariantError("Outbox delivery changes require a service transition")
        _validate_outbox_shape(target)


async def _database_now(session: AsyncSession) -> datetime:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        value = await session.scalar(select(text("clock_timestamp()")))
        if isinstance(value, datetime):
            return _as_utc(value)
    return datetime.now(timezone.utc)


async def _validate_actor_current(session: AsyncSession, event_row: AuditEvent) -> None:
    now = event_row.occurred_at
    if event_row.actor_type == "user":
        user = await session.get(User, event_row.actor_user_id)
        member = await session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == event_row.actor_organization_id,
                OrganizationMember.user_id == event_row.actor_user_id,
                OrganizationMember.status == "active",
            )
        )
        if user is None or user.status != "active" or member is None:
            raise AuditInvariantError("AuditEvent user actor is not active")
        valid_from = _as_utc(member.valid_from) if member.valid_from else None
        valid_until = _as_utc(member.valid_until) if member.valid_until else None
        if (valid_from is not None and now < valid_from) or (
            valid_until is not None and now >= valid_until
        ):
            raise AuditInvariantError("AuditEvent user membership is outside validity")
    elif event_row.actor_type == "connector":
        connector = await session.get(Connector, event_row.actor_connector_id)
        if connector is None or (
            connector.space_id != event_row.space_id
            or connector.owner_organization_id != event_row.actor_organization_id
        ):
            raise AuditInvariantError("AuditEvent connector actor is outside its Space")


async def _validate_subject_current(session: AsyncSession, event_row: AuditEvent) -> None:
    # Domain model imports stay local so Contract/Compute command services can
    # depend on Audit infrastructure without creating service import cycles.
    from app.modules.compute.models import (
        Artifact,
        ArtifactReview,
        ComputeJob,
        ComputeRun,
        ExecutionEligibilityInvalidation,
        ExecutionEligibilitySnapshot,
    )
    from app.modules.contracts.models import Contract, ContractRevision
    from app.modules.applications.models import Application
    from app.modules.catalog.models import DataProductVersion
    from app.modules.marketplace.models import (
        ApprovedResultPackage,
        ArtifactReviewDecision,
        ArtifactReviewTask,
        ContractReadinessConfirmation,
        ContractReadinessRevocation,
        ModelVersion,
        ResultDownloadGrant,
    )
    from app.modules.reviews.models import ReviewDecision, ReviewTask

    if event_row.subject_type == "contract_revision":
        revision = await session.get(ContractRevision, event_row.subject_id)
        contract = await session.get(Contract, revision.contract_id) if revision else None
        valid = contract is not None and contract.space_id == event_row.space_id
    elif event_row.subject_type == "compute_job":
        subject = await session.get(ComputeJob, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "compute_run":
        subject = await session.get(ComputeRun, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "artifact":
        subject = await session.get(Artifact, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "artifact_review":
        subject = await session.get(ArtifactReview, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "data_product_version":
        subject = await session.get(DataProductVersion, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "model_version":
        subject = await session.get(ModelVersion, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "application":
        subject = await session.get(Application, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "review_decision":
        subject = await session.get(ReviewDecision, event_row.subject_id)
        task = await session.get(ReviewTask, subject.review_task_id) if subject else None
        valid = task is not None and task.space_id == event_row.space_id
    elif event_row.subject_type == "contract_readiness":
        subject = await session.get(ContractReadinessConfirmation, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "contract_readiness_revocation":
        subject = await session.get(ContractReadinessRevocation, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "execution_eligibility":
        subject = await session.get(ExecutionEligibilitySnapshot, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "execution_eligibility_invalidation":
        subject = await session.get(
            ExecutionEligibilityInvalidation, event_row.subject_id
        )
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "artifact_review_decision":
        subject = await session.get(ArtifactReviewDecision, event_row.subject_id)
        task = (
            await session.get(ArtifactReviewTask, subject.artifact_review_task_id)
            if subject
            else None
        )
        valid = task is not None and task.space_id == event_row.space_id
    elif event_row.subject_type == "result_package":
        subject = await session.get(ApprovedResultPackage, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "result_download_grant":
        subject = await session.get(ResultDownloadGrant, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "product_lifecycle_request":
        from app.modules.lifecycle.models import ProductLifecycleRequest

        subject = await session.get(ProductLifecycleRequest, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "external_catalog_sync_run":
        from app.modules.external_catalog.models import (
            ExternalCatalogSource,
            ExternalCatalogSyncRun,
        )

        subject = await session.get(ExternalCatalogSyncRun, event_row.subject_id)
        source = (
            await session.get(ExternalCatalogSource, subject.source_id)
            if subject
            else None
        )
        valid = source is not None and source.space_id == event_row.space_id
    elif event_row.subject_type == "external_catalog_source":
        from app.modules.external_catalog.models import ExternalCatalogSource

        subject = await session.get(ExternalCatalogSource, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "dataset_model_relation":
        from app.modules.dataset_model_evidence.models import DatasetModelRelation

        subject = await session.get(DatasetModelRelation, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type == "service_access_request":
        from app.modules.service_access.models import ServiceAccessRequest

        subject = await session.get(ServiceAccessRequest, event_row.subject_id)
        valid = subject is not None and subject.space_id == event_row.space_id
    elif event_row.subject_type in {
        "commercial_order",
        "commercial_payment",
        "commercial_fulfillment",
        "commercial_download_grant",
    }:
        from app.modules.commerce.models import (
            CommercialDownloadGrant,
            CommercialFulfillment,
            CommercialOrder,
            DemoPayment,
        )

        if event_row.subject_type == "commercial_order":
            subject = await session.get(CommercialOrder, event_row.subject_id)
            valid = subject is not None and subject.space_id == event_row.space_id
        elif event_row.subject_type == "commercial_payment":
            subject = await session.get(DemoPayment, event_row.subject_id)
            order = (
                await session.get(CommercialOrder, subject.order_id)
                if subject is not None
                else None
            )
            valid = order is not None and order.space_id == event_row.space_id
        elif event_row.subject_type == "commercial_fulfillment":
            subject = await session.get(CommercialFulfillment, event_row.subject_id)
            valid = subject is not None and subject.space_id == event_row.space_id
        else:
            subject = await session.get(CommercialDownloadGrant, event_row.subject_id)
            valid = subject is not None and subject.space_id == event_row.space_id
    else:
        valid = False
    if not valid:
        raise AuditInvariantError("AuditEvent subject is missing or cross-Space")


def _assert_exact_replay(
    existing: AuditEvent,
    candidate: AuditEvent,
) -> None:
    fields = (
        "command_id",
        "idempotency_key",
        "event_type",
        "schema_version",
        "actor_type",
        "actor_organization_id",
        "actor_user_id",
        "actor_connector_id",
        "actor_service_code",
        "subject_type",
        "subject_id",
        "result",
        "correlation_id",
        "causation_id",
        "evidence_digest",
    )
    if any(getattr(existing, name) != getattr(candidate, name) for name in fields):
        raise IdempotencyConflict("idempotency key maps to different AuditEvent facts")


def _outbox_payload(event_row: AuditEvent, message_id: UUID) -> dict[str, Any]:
    return {
        "message_schema": "medtrust-event-envelope/v1",
        "message_id": str(message_id),
        "event_id": str(event_row.event_id),
        "space_id": str(event_row.space_id),
        "event_type": event_row.event_type,
        "event_schema_version": event_row.schema_version,
        "occurred_at": _canonical_timestamp(event_row.occurred_at),
        "subject_type": event_row.subject_type,
        "subject_id": str(event_row.subject_id),
        "result": event_row.result,
        "correlation_id": str(event_row.correlation_id),
        "event_digest": event_row.event_digest,
        "evidence": event_row.evidence_snapshot,
    }


def _outbox_idempotency_key(
    event_id: UUID, topic: str, destination: str, schema_version: int = 1
) -> str:
    raw = f"{event_id}|{topic}|{destination}|{schema_version}".encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


async def append_audit_event_with_outbox(
    session: AsyncSession,
    *,
    space_id: UUID,
    event_type: str,
    actor_type: str,
    subject_type: str,
    subject_id: UUID,
    result: str,
    correlation_id: UUID,
    command_id: UUID,
    idempotency_key: str,
    evidence_snapshot: dict[str, Any],
    actor_organization_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    actor_connector_id: UUID | None = None,
    actor_service_code: str | None = None,
    causation_id: UUID | None = None,
) -> AuditAppendResult:
    """Append one immutable event and all catalog-required outbox messages."""

    _require_digest(idempotency_key, "idempotency_key")
    evidence_digest = canonical_json_digest_v1(evidence_snapshot)
    locked_space = await session.scalar(
        select(Space).where(Space.id == space_id).with_for_update()
    )
    if locked_space is None:
        raise AuditInvariantError("AuditEvent Space does not exist")
    now = await _database_now(session)
    candidate = AuditEvent(
        event_id=uuid4(),
        space_id=space_id,
        stream_sequence=1,
        event_type=event_type,
        schema_version=1,
        canonicalization_version="medtrust-jsonb-c14n/v1",
        occurred_at=now,
        actor_type=actor_type,
        actor_organization_id=actor_organization_id,
        actor_user_id=actor_user_id,
        actor_connector_id=actor_connector_id,
        actor_service_code=actor_service_code,
        subject_type=subject_type,
        subject_id=subject_id,
        result=result,
        correlation_id=correlation_id,
        causation_id=causation_id,
        command_id=command_id,
        idempotency_key=idempotency_key,
        evidence_snapshot=evidence_snapshot,
        evidence_digest=evidence_digest,
        previous_event_digest=None,
        event_digest="",
        created_at=now,
    )
    expected_shape = EVENT_SHAPES.get(event_type)
    if expected_shape != (subject_type, result):
        raise AuditInvariantError("event type, subject type or result is inconsistent")
    _validate_actor_shape(candidate)
    await _validate_actor_current(session, candidate)
    await _validate_subject_current(session, candidate)

    key_rows = list(
        (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.space_id == space_id,
                    AuditEvent.idempotency_key == idempotency_key,
                )
            )
        ).all()
    )
    if any(
        row.command_id != command_id or row.correlation_id != correlation_id
        for row in key_rows
    ):
        raise IdempotencyConflict("idempotency key maps to another command")
    command_rows = list(
        (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.space_id == space_id,
                    AuditEvent.command_id == command_id,
                )
            )
        ).all()
    )
    if any(
        row.idempotency_key != idempotency_key
        or row.correlation_id != correlation_id
        for row in command_rows
    ):
        raise IdempotencyConflict("command_id maps to another idempotency context")

    existing = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.space_id == space_id,
            AuditEvent.idempotency_key == idempotency_key,
            AuditEvent.event_type == event_type,
            AuditEvent.subject_type == subject_type,
            AuditEvent.subject_id == subject_id,
        )
    )
    if existing is not None:
        _assert_exact_replay(existing, candidate)
        messages = tuple(
            (
                await session.scalars(
                    select(OutboxMessage)
                    .where(OutboxMessage.audit_event_id == existing.event_id)
                    .order_by(OutboxMessage.topic, OutboxMessage.destination)
                )
            ).all()
        )
        expected_targets = set(EVENT_TARGETS[event_type])
        actual_targets = {(row.topic, row.destination) for row in messages}
        if actual_targets != expected_targets:
            raise IdempotencyConflict("existing event has an inconsistent Outbox target set")
        return AuditAppendResult(existing, messages, False)

    previous = await session.scalar(
        select(AuditEvent)
        .where(AuditEvent.space_id == space_id)
        .order_by(AuditEvent.stream_sequence.desc())
        .limit(1)
    )
    candidate.stream_sequence = 1 if previous is None else previous.stream_sequence + 1
    candidate.previous_event_digest = None if previous is None else previous.event_digest
    if causation_id is not None:
        cause = await session.get(AuditEvent, causation_id)
        if cause is None or cause.space_id != space_id:
            raise AuditInvariantError("causation event is missing or cross-Space")
        if previous is None or cause.stream_sequence >= candidate.stream_sequence:
            raise AuditInvariantError("causation event must precede the new event")
    candidate.event_digest = canonical_json_digest_v1(_event_manifest(candidate))
    candidate._append_validated = True
    session.add(candidate)

    messages: list[OutboxMessage] = []
    for topic, destination in EVENT_TARGETS[event_type]:
        message_id = uuid4()
        payload = _outbox_payload(candidate, message_id)
        message = OutboxMessage(
            message_id=message_id,
            audit_event_id=candidate.event_id,
            space_id=space_id,
            topic=topic,
            destination=destination,
            message_schema_version=1,
            payload_snapshot=payload,
            payload_digest=canonical_json_digest_v1(payload),
            idempotency_key=_outbox_idempotency_key(
                candidate.event_id, topic, destination
            ),
            status="pending",
            attempt_count=0,
            available_at=now,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        message._enqueue_validated = True
        session.add(message)
        messages.append(message)
    await session.flush()
    candidate._append_validated = False
    for message in messages:
        message._enqueue_validated = False
    return AuditAppendResult(candidate, tuple(messages), True)


def sanitize_outbox_error(value: str) -> str:
    collapsed = " ".join((value or "delivery_error").split())
    collapsed = AUTHORIZATION_VALUE_PATTERN.sub("[redacted]", collapsed)
    collapsed = URL_QUERY_PATTERN.sub(r"\1?[redacted]", collapsed)
    collapsed = SENSITIVE_ERROR_PATTERN.sub("[redacted]", collapsed)
    return collapsed[:1024] or "delivery_error"


def _validate_worker_id(worker_id: str) -> None:
    if not worker_id or len(worker_id) > 96 or re.search(
        r"(?i)(token|secret|password|authorization|access[_-]?key)", worker_id
    ):
        raise AuditInvariantError("worker_id must be a non-secret 1-96 character label")


async def _claim_outbox(
    session: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
    include_pending: bool,
) -> list[OutboxMessage]:
    _validate_worker_id(worker_id)
    if not 1 <= batch_size <= 100:
        raise AuditInvariantError("batch_size must be between 1 and 100")
    if not 15 <= lease_seconds <= 300:
        raise AuditInvariantError("lease_seconds must be between 15 and 300")
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        pending_clause = (
            "(status='pending' AND available_at<=clock_timestamp()) OR "
            if include_pending
            else ""
        )
        statement = text(
            f"""
            WITH exhausted AS (
                UPDATE medtrust.outbox_messages
                   SET status='dead_letter',
                       locked_at=NULL,
                       lock_owner=NULL,
                       lease_expires_at=NULL,
                       last_error='lease_expired:max_attempts',
                       updated_at=clock_timestamp(),
                       row_version=row_version+1
                 WHERE status='processing'
                   AND lease_expires_at<=clock_timestamp()
                   AND attempt_count>=10
                 RETURNING message_id
            ), candidates AS (
                SELECT message_id
                  FROM medtrust.outbox_messages
                 WHERE ({pending_clause}
                        (status='processing' AND lease_expires_at<=clock_timestamp()))
                   AND attempt_count<10
                 ORDER BY available_at, created_at, message_id
                 FOR UPDATE SKIP LOCKED
                 LIMIT :batch_size
            ), claimed AS (
                UPDATE medtrust.outbox_messages o
                   SET status='processing',
                       attempt_count=o.attempt_count+1,
                       locked_at=clock_timestamp(),
                       lock_owner=:worker_id,
                       lease_expires_at=clock_timestamp()+(:lease_seconds * interval '1 second'),
                       last_error=NULL,
                       updated_at=clock_timestamp(),
                       row_version=o.row_version+1
                  FROM candidates c
                 WHERE o.message_id=c.message_id
                 RETURNING o.message_id
            )
            SELECT message_id FROM claimed
            """
        )
        ids = list(
            (
                await session.scalars(
                    statement,
                    {
                        "batch_size": batch_size,
                        "worker_id": worker_id,
                        "lease_seconds": lease_seconds,
                    },
                )
            ).all()
        )
        if not ids:
            return []
        rows = list(
            (
                await session.scalars(
                    select(OutboxMessage)
                    .where(OutboxMessage.message_id.in_(ids))
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        by_id = {row.message_id: row for row in rows}
        return [by_id[item] for item in ids]

    now = datetime.now(timezone.utc)
    exhausted = list(
        (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.status == "processing",
                    OutboxMessage.lease_expires_at <= now,
                    OutboxMessage.attempt_count >= 10,
                )
            )
        ).all()
    )
    for message in exhausted:
        message._delivery_transition_validated = True
        message.status = "dead_letter"
        message.locked_at = None
        message.lock_owner = None
        message.lease_expires_at = None
        message.last_error = "lease_expired:max_attempts"
        message.updated_at = now
        message.row_version += 1
    conditions = [
        OutboxMessage.status == "processing",
        OutboxMessage.lease_expires_at <= now,
    ]
    criterion = conditions[0] & conditions[1]
    if include_pending:
        criterion = or_(
            criterion,
            (OutboxMessage.status == "pending")
            & (OutboxMessage.available_at <= now),
        )
    rows = list(
        (
            await session.scalars(
                select(OutboxMessage)
                .where(criterion, OutboxMessage.attempt_count < 10)
                .order_by(
                    OutboxMessage.available_at,
                    OutboxMessage.created_at,
                    OutboxMessage.message_id,
                )
                .limit(batch_size)
            )
        ).all()
    )
    for message in rows:
        message._delivery_transition_validated = True
        message.status = "processing"
        message.attempt_count += 1
        message.locked_at = now
        message.lock_owner = worker_id
        message.lease_expires_at = now + timedelta(seconds=lease_seconds)
        message.last_error = None
        message.updated_at = now
        message.row_version += 1
    await session.flush()
    for message in [*exhausted, *rows]:
        message._delivery_transition_validated = False
    return rows


async def claim_outbox_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 60,
) -> list[OutboxMessage]:
    return await _claim_outbox(
        session,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        include_pending=True,
    )


async def reclaim_expired_outbox(
    session: AsyncSession,
    *,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 60,
) -> list[OutboxMessage]:
    return await _claim_outbox(
        session,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        include_pending=False,
    )


async def _locked_processing_message(
    session: AsyncSession, message_id: UUID, worker_id: str
) -> OutboxMessage:
    _validate_worker_id(worker_id)
    message = await session.scalar(
        select(OutboxMessage)
        .where(OutboxMessage.message_id == message_id)
        .with_for_update()
    )
    if message is None or message.status != "processing":
        raise AuditInvariantError("OutboxMessage is not processing")
    now = await _database_now(session)
    if message.lock_owner != worker_id or message.lease_expires_at is None or (
        _as_utc(message.lease_expires_at) <= now
    ):
        raise AuditInvariantError("Outbox lease is unavailable to this worker")
    return message


async def mark_outbox_published(
    session: AsyncSession, *, message_id: UUID, worker_id: str
) -> OutboxMessage:
    message = await _locked_processing_message(session, message_id, worker_id)
    now = await _database_now(session)
    message._delivery_transition_validated = True
    message.status = "published"
    message.locked_at = None
    message.lock_owner = None
    message.lease_expires_at = None
    message.last_error = None
    message.published_at = now
    message.updated_at = now
    message.row_version += 1
    await session.flush()
    message._delivery_transition_validated = False
    return message


def _retry_delay(message: OutboxMessage) -> timedelta:
    seconds = min(5 * (2 ** max(message.attempt_count - 1, 0)), 900)
    digest = hashlib.sha256(str(message.message_id).encode("ascii")).digest()
    jitter = (int.from_bytes(digest[:2], "big") / 65535) * 0.2
    return timedelta(seconds=seconds * (1 + jitter))


async def mark_outbox_failed(
    session: AsyncSession,
    *,
    message_id: UUID,
    worker_id: str,
    error: str,
    retryable: bool = True,
) -> OutboxMessage:
    message = await _locked_processing_message(session, message_id, worker_id)
    now = await _database_now(session)
    message._delivery_transition_validated = True
    message.locked_at = None
    message.lock_owner = None
    message.lease_expires_at = None
    message.last_error = sanitize_outbox_error(error)
    message.published_at = None
    if not retryable or message.attempt_count >= 10:
        message.status = "dead_letter"
        message.available_at = now
    else:
        message.status = "pending"
        message.available_at = now + _retry_delay(message)
    message.updated_at = now
    message.row_version += 1
    await session.flush()
    message._delivery_transition_validated = False
    return message
