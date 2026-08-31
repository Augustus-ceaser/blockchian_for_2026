from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ExecutionSubmissionReceipt:
    accepted: bool
    external_execution_id: str | None
    accepted_at: datetime | None
    request_digest: str
    retryable: bool
    error_code: str | None
    receipt_digest: str


@dataclass(frozen=True)
class ExecutionStatus:
    external_execution_id: str
    status: str


@dataclass(frozen=True)
class CancellationReceipt:
    external_execution_id: str
    accepted: bool
    receipt_digest: str
