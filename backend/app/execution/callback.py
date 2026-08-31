from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ExecutionCallbackEnvelope:
    """Authenticated, path-free callback presented by an Executor gateway."""

    space_id: UUID
    compute_run_id: UUID
    executor_namespace: str
    external_execution_id: str
    callback_id: str
    callback_type: str
    callback_schema_version: int
    occurred_at: datetime
    payload_snapshot: dict[str, Any]
    execution_evidence_digest: str
    authentication_evidence_digest: str
    correlation_id: UUID
    causation_id: UUID | None = None

    def occurred_at_utc(self) -> datetime:
        if self.occurred_at.tzinfo is None:
            return self.occurred_at.replace(tzinfo=timezone.utc)
        return self.occurred_at.astimezone(timezone.utc)
