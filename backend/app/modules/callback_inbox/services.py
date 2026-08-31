from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.callback import ExecutionCallbackEnvelope
from app.modules.audit.services import canonical_json_digest_v1
from app.modules.callback_inbox.models import (
    CALLBACK_OUTCOME_CODES,
    CALLBACK_TYPES,
    ExecutionCallbackInboxEntry,
)
from app.modules.compute.models import ComputeRun

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
MAX_ATTEMPTS = 10

_PAYLOAD_KEYS = {
    "execution.started": {"schema_version", "started_at", "runtime_summary"},
    "execution.completed": {
        "schema_version",
        "completed_at",
        "output_manifest",
        "output_digest",
        "execution_summary",
        "resource_usage_summary",
        "artifact_type",
        "object_storage_ref",
    },
    "execution.failed": {"schema_version", "failed_at", "error_code", "error_summary"},
    "execution.interrupted": {
        "schema_version",
        "interrupted_at",
        "error_code",
        "error_summary",
    },
}
_FORBIDDEN_KEY_PARTS = {
    "token",
    "secret",
    "password",
    "credential",
    "access_key",
    "private_key",
    "patient_id",
    "wsi_path",
}


class CallbackInboxInvariantError(ValueError):
    """Raised when callback evidence or processing ownership is invalid."""


class CallbackInboxIdempotencyConflict(CallbackInboxInvariantError):
    """Raised when one callback identity maps to different immutable evidence."""


@dataclass(frozen=True)
class CallbackReceiveResult:
    entry: ExecutionCallbackInboxEntry
    created: bool
    duplicate_fact: bool = False


@dataclass(frozen=True)
class CallbackReceiveAck:
    acknowledged: bool
    entry_id: UUID | None = None
    error_code: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize_error(value: str) -> str:
    cleaned = re.sub(
        r"(?i)(authorization|bearer|token|secret|password|access[_-]?key|"
        r"x-amz-signature|signature)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        value,
    )
    cleaned = re.sub(r"(?i)(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", cleaned)
    cleaned = re.sub(r"(?:[A-Za-z]:\\|/)[^\s]+", "[path-redacted]", cleaned)
    return cleaned[:1024] or "processing_error"


def _validate_identifier(value: str, name: str, max_length: int) -> None:
    if not value or len(value) > max_length or not IDENTIFIER_PATTERN.fullmatch(value):
        raise CallbackInboxInvariantError(f"{name} is invalid")


def _validate_digest(value: str, name: str) -> None:
    if not DIGEST_PATTERN.fullmatch(value):
        raise CallbackInboxInvariantError(f"{name} must be sha256:<64 lowercase hex>")


def _clean_payload(callback_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CallbackInboxInvariantError("callback payload must be a JSON object")
    allowed = _PAYLOAD_KEYS[callback_type]
    unknown = set(payload) - allowed
    if unknown:
        raise CallbackInboxInvariantError("callback payload contains non-allowlisted fields")
    if payload.get("schema_version") != 1:
        raise CallbackInboxInvariantError("callback payload schema_version must be 1")
    cleaned = dict(payload)
    if "error_summary" in cleaned:
        cleaned["error_summary"] = _sanitize_error(str(cleaned["error_summary"]))
    required = {
        "execution.started": {"started_at", "runtime_summary"},
        "execution.completed": {
            "completed_at",
            "output_manifest",
            "output_digest",
            "execution_summary",
            "resource_usage_summary",
            "artifact_type",
            "object_storage_ref",
        },
        "execution.failed": {"failed_at", "error_code", "error_summary"},
        "execution.interrupted": {"interrupted_at", "error_code", "error_summary"},
    }[callback_type]
    if not required.issubset(cleaned):
        raise CallbackInboxInvariantError("callback payload is incomplete")

    def validate_safe(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                    raise CallbackInboxInvariantError(
                        f"callback payload contains sensitive field at {path}"
                    )
                validate_safe(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                validate_safe(child, f"{path}[{index}]")
        elif isinstance(value, str):
            lowered = value.lower()
            if "-----begin " in lowered or re.search(r"(?i)(bearer|token|secret)=", value):
                raise CallbackInboxInvariantError(
                    f"callback payload contains sensitive value at {path}"
                )

    validate_safe(cleaned)
    canonical_json_digest_v1(cleaned)
    return cleaned


def _normalized_fact_digest(
    envelope: ExecutionCallbackEnvelope, payload_digest: str
) -> str:
    occurred_at = envelope.occurred_at_utc().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return canonical_json_digest_v1(
        {
            "schema_version": "execution-callback-fact/v1",
            "space_id": str(envelope.space_id),
            "compute_run_id": str(envelope.compute_run_id),
            "executor_namespace": envelope.executor_namespace,
            "external_execution_id": envelope.external_execution_id,
            "callback_type": envelope.callback_type,
            "occurred_at": occurred_at,
            "payload_digest": payload_digest,
            "execution_evidence_digest": envelope.execution_evidence_digest,
        }
    )


async def receive_execution_callback(
    session: AsyncSession, *, envelope: ExecutionCallbackEnvelope
) -> CallbackReceiveResult:
    """Persist one authenticated callback in the caller transaction."""

    if envelope.callback_type not in CALLBACK_TYPES or envelope.callback_schema_version != 1:
        raise CallbackInboxInvariantError("callback type or schema version is invalid")
    _validate_identifier(envelope.executor_namespace, "executor_namespace", 96)
    _validate_identifier(envelope.callback_id, "callback_id", 160)
    _validate_identifier(envelope.external_execution_id, "external_execution_id", 256)
    _validate_digest(envelope.execution_evidence_digest, "execution_evidence_digest")
    _validate_digest(
        envelope.authentication_evidence_digest, "authentication_evidence_digest"
    )
    payload = _clean_payload(envelope.callback_type, envelope.payload_snapshot)
    payload_digest = canonical_json_digest_v1(payload)
    fact_digest = _normalized_fact_digest(envelope, payload_digest)

    existing = await session.scalar(
        select(ExecutionCallbackInboxEntry).where(
            ExecutionCallbackInboxEntry.executor_namespace == envelope.executor_namespace,
            ExecutionCallbackInboxEntry.callback_id == envelope.callback_id,
        )
    )
    if existing is not None:
        expected = (
            existing.space_id == envelope.space_id
            and existing.compute_run_id == envelope.compute_run_id
            and existing.external_execution_id == envelope.external_execution_id
            and existing.callback_type == envelope.callback_type
            and existing.payload_digest == payload_digest
            and existing.execution_evidence_digest == envelope.execution_evidence_digest
            and existing.authentication_evidence_digest
            == envelope.authentication_evidence_digest
        )
        if not expected:
            raise CallbackInboxIdempotencyConflict(
                "callback identity already exists with different immutable evidence"
            )
        return CallbackReceiveResult(existing, created=False)

    semantic = await session.scalar(
        select(ExecutionCallbackInboxEntry).where(
            ExecutionCallbackInboxEntry.executor_namespace == envelope.executor_namespace,
            ExecutionCallbackInboxEntry.compute_run_id == envelope.compute_run_id,
            ExecutionCallbackInboxEntry.callback_type == envelope.callback_type,
            ExecutionCallbackInboxEntry.normalized_fact_digest == fact_digest,
        )
    )
    if semantic is not None:
        return CallbackReceiveResult(semantic, created=False, duplicate_fact=True)

    run = await session.get(ComputeRun, envelope.compute_run_id)
    if run is None or run.space_id != envelope.space_id:
        raise CallbackInboxInvariantError("callback Run or Space is invalid")

    entry = ExecutionCallbackInboxEntry(
        space_id=envelope.space_id,
        compute_run_id=envelope.compute_run_id,
        executor_namespace=envelope.executor_namespace,
        external_execution_id=envelope.external_execution_id,
        callback_id=envelope.callback_id,
        callback_type=envelope.callback_type,
        callback_schema_version=1,
        occurred_at=envelope.occurred_at_utc(),
        payload_snapshot=payload,
        payload_digest=payload_digest,
        normalized_fact_digest=fact_digest,
        execution_evidence_digest=envelope.execution_evidence_digest,
        authentication_evidence_digest=envelope.authentication_evidence_digest,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        status="received",
    )
    session.add(entry)
    await session.flush()
    return CallbackReceiveResult(entry, created=True)


class ExecutionCallbackReceiver:
    """ACKs only after the Callback Inbox transaction commits."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def receive(self, envelope: ExecutionCallbackEnvelope) -> CallbackReceiveAck:
        try:
            async with self._session_maker() as session:
                async with session.begin():
                    result = await receive_execution_callback(session, envelope=envelope)
            return CallbackReceiveAck(True, entry_id=result.entry.id)
        except CallbackInboxIdempotencyConflict:
            return CallbackReceiveAck(False, error_code="callback_idempotency_conflict")
        except CallbackInboxInvariantError:
            return CallbackReceiveAck(False, error_code="callback_invariant_error")
        except Exception:
            return CallbackReceiveAck(False, error_code="callback_commit_failed")


def _retry_delay(attempt_count: int, entry_id: UUID) -> timedelta:
    base = min(900, 5 * (2 ** max(0, attempt_count - 1)))
    seed = int(hashlib.sha256(entry_id.bytes).hexdigest()[:8], 16)
    return timedelta(seconds=base * (1 + (seed % 21) / 100))


async def claim_callback_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 60,
) -> list[ExecutionCallbackInboxEntry]:
    now = _utc_now()
    rows = list(
        (
            await session.scalars(
                select(ExecutionCallbackInboxEntry)
                .where(
                    ExecutionCallbackInboxEntry.status == "received",
                    ExecutionCallbackInboxEntry.available_at <= now,
                    ExecutionCallbackInboxEntry.attempt_count < MAX_ATTEMPTS,
                )
                .order_by(
                    ExecutionCallbackInboxEntry.available_at,
                    ExecutionCallbackInboxEntry.received_at,
                    ExecutionCallbackInboxEntry.id,
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for row in rows:
        row.status = "processing"
        row.attempt_count += 1
        row.locked_at = now
        row.lock_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.processing_started_at = row.processing_started_at or now
        row.processing_error = None
        row.row_version += 1
    await session.flush()
    return rows


async def reclaim_expired_callbacks(
    session: AsyncSession,
    *,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 60,
) -> list[ExecutionCallbackInboxEntry]:
    now = _utc_now()
    rows = list(
        (
            await session.scalars(
                select(ExecutionCallbackInboxEntry)
                .where(
                    ExecutionCallbackInboxEntry.status == "processing",
                    ExecutionCallbackInboxEntry.lease_expires_at <= now,
                    ExecutionCallbackInboxEntry.attempt_count < MAX_ATTEMPTS,
                )
                .order_by(
                    ExecutionCallbackInboxEntry.lease_expires_at,
                    ExecutionCallbackInboxEntry.id,
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for row in rows:
        row.attempt_count += 1
        row.locked_at = now
        row.lock_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.row_version += 1
    await session.flush()
    return rows


async def _owned_callback(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    expected_row_version: int | None = None,
) -> ExecutionCallbackInboxEntry:
    now = _utc_now()
    entry = await session.scalar(
        select(ExecutionCallbackInboxEntry)
        .where(ExecutionCallbackInboxEntry.id == entry_id)
        .with_for_update()
    )
    if (
        entry is None
        or entry.status != "processing"
        or entry.lock_owner != worker_id
        or entry.lease_expires_at is None
        or entry.lease_expires_at <= now
        or (expected_row_version is not None and entry.row_version != expected_row_version)
    ):
        raise CallbackInboxInvariantError("Callback Inbox lease ownership is unavailable")
    return entry


async def retry_callback(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    error: str,
    expected_row_version: int | None = None,
) -> ExecutionCallbackInboxEntry:
    entry = await _owned_callback(
        session,
        entry_id=entry_id,
        worker_id=worker_id,
        expected_row_version=expected_row_version,
    )
    if entry.attempt_count >= MAX_ATTEMPTS:
        return await dead_letter_callback(
            session,
            entry_id=entry_id,
            worker_id=worker_id,
            error=error,
            expected_row_version=entry.row_version,
        )
    entry.status = "received"
    entry.available_at = _utc_now() + _retry_delay(entry.attempt_count, entry.id)
    entry.locked_at = None
    entry.lock_owner = None
    entry.lease_expires_at = None
    entry.processing_error = _sanitize_error(error)
    entry.row_version += 1
    await session.flush()
    return entry


async def complete_callback(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    outcome_code: str,
    outcome_reference_type: str | None = None,
    outcome_reference_id: UUID | None = None,
    expected_row_version: int | None = None,
) -> ExecutionCallbackInboxEntry:
    if outcome_code not in CALLBACK_OUTCOME_CODES:
        raise CallbackInboxInvariantError("Callback outcome_code is invalid")
    entry = await _owned_callback(
        session,
        entry_id=entry_id,
        worker_id=worker_id,
        expected_row_version=expected_row_version,
    )
    now = _utc_now()
    entry.status = "completed"
    entry.outcome_code = outcome_code
    entry.outcome_reference_type = outcome_reference_type
    entry.outcome_reference_id = outcome_reference_id
    entry.locked_at = None
    entry.lock_owner = None
    entry.lease_expires_at = None
    entry.processing_error = None
    entry.completed_at = now
    entry.terminal_at = now
    entry.row_version += 1
    await session.flush()
    return entry


async def dead_letter_callback(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    error: str,
    expected_row_version: int | None = None,
) -> ExecutionCallbackInboxEntry:
    entry = await _owned_callback(
        session,
        entry_id=entry_id,
        worker_id=worker_id,
        expected_row_version=expected_row_version,
    )
    entry.status = "dead_letter"
    entry.locked_at = None
    entry.lock_owner = None
    entry.lease_expires_at = None
    entry.processing_error = _sanitize_error(error)
    entry.terminal_at = _utc_now()
    entry.row_version += 1
    await session.flush()
    return entry
