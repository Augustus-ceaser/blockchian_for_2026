from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.audit.services import canonical_json_digest_v1


class EnvelopeInvariantError(ValueError):
    """Raised when persisted Outbox evidence cannot form a stable envelope."""


@dataclass(frozen=True)
class OutboxEnvelope:
    message_id: UUID
    event_id: UUID
    event_type: str
    schema_version: int
    space_id: UUID
    topic: str
    destination: str
    subject_type: str
    subject_id: UUID
    result: str
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: datetime
    evidence: dict[str, Any]
    event_digest: str
    payload_digest: str
    idempotency_key: str

    @classmethod
    def from_records(
        cls, message: OutboxMessage, event: AuditEvent
    ) -> OutboxEnvelope:
        if message.audit_event_id != event.event_id or message.space_id != event.space_id:
            raise EnvelopeInvariantError("OutboxMessage and AuditEvent are inconsistent")
        if canonical_json_digest_v1(message.payload_snapshot) != message.payload_digest:
            raise EnvelopeInvariantError("OutboxMessage payload digest is invalid")

        payload = message.payload_snapshot
        expected = {
            "message_id": str(message.message_id),
            "event_id": str(event.event_id),
            "space_id": str(event.space_id),
            "event_type": event.event_type,
            "subject_type": event.subject_type,
            "subject_id": str(event.subject_id),
            "correlation_id": str(event.correlation_id),
            "event_digest": event.event_digest,
        }
        if any(payload.get(name) != value for name, value in expected.items()):
            raise EnvelopeInvariantError("OutboxMessage payload does not match AuditEvent")
        if payload.get("evidence") != event.evidence_snapshot:
            raise EnvelopeInvariantError("OutboxMessage evidence does not match AuditEvent")

        return cls(
            message_id=message.message_id,
            event_id=event.event_id,
            event_type=event.event_type,
            schema_version=message.message_schema_version,
            space_id=event.space_id,
            topic=message.topic,
            destination=message.destination,
            subject_type=event.subject_type,
            subject_id=event.subject_id,
            result=event.result,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            occurred_at=event.occurred_at,
            evidence=deepcopy(event.evidence_snapshot),
            event_digest=event.event_digest,
            payload_digest=message.payload_digest,
            idempotency_key=message.idempotency_key,
        )
