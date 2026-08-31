from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.messaging.envelope import OutboxEnvelope
from app.messaging.publisher import PublishResult
from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.audit.services import canonical_json_digest_v1
from app.modules.inbox.models import ConsumerInboxEntry

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_ATTEMPTS = 10


class InboxInvariantError(ValueError):
    """Raised when Inbox receipt, ownership, or lifecycle evidence is invalid."""


class InboxIdempotencyConflict(InboxInvariantError):
    """Raised when an existing consumer/event identity has different content."""


@dataclass(frozen=True)
class InboxReceiveResult:
    entry: ConsumerInboxEntry
    created: bool


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
    return cleaned[:1024] or "processing_error"


def _retry_delay(attempt_count: int, entry_id: UUID) -> timedelta:
    base = min(900, 5 * (2 ** max(0, attempt_count - 1)))
    jitter_seed = int(hashlib.sha256(entry_id.bytes).hexdigest()[:8], 16)
    jitter = (jitter_seed % 21) / 100
    return timedelta(seconds=base * (1 + jitter))


async def receive_inbox_envelope(
    session: AsyncSession,
    *,
    consumer_name: str,
    envelope: OutboxEnvelope,
) -> InboxReceiveResult:
    """Persist a delivery in the caller transaction; the caller ACKs only after commit."""

    if not consumer_name or len(consumer_name) > 96:
        raise InboxInvariantError("consumer_name is invalid")
    if envelope.topic != "medtrust.compute.dispatch.v1" or envelope.destination != "compute.dispatch":
        raise InboxInvariantError("Inbox receiver only accepts compute.dispatch envelopes")
    if (
        envelope.event_type != "compute.run.reserved"
        or envelope.subject_type != "compute_run"
        or envelope.result != "success"
    ):
        raise InboxInvariantError("Envelope is not a successful compute.run.reserved fact")
    if envelope.schema_version != 1 or not DIGEST_PATTERN.fullmatch(envelope.payload_digest):
        raise InboxInvariantError("Envelope schema or payload digest is invalid")

    existing = await session.scalar(
        select(ConsumerInboxEntry).where(
            ConsumerInboxEntry.consumer_name == consumer_name,
            ConsumerInboxEntry.event_id == envelope.event_id,
        )
    )
    if existing is not None:
        if existing.payload_digest != envelope.payload_digest:
            raise InboxIdempotencyConflict(
                "consumer/event identity already exists with another payload digest"
            )
        return InboxReceiveResult(existing, created=False)

    source = await session.get(OutboxMessage, envelope.message_id)
    event = await session.get(AuditEvent, envelope.event_id)
    if source is None or event is None:
        raise InboxInvariantError("Envelope source records do not exist")
    if source.status != "processing":
        raise InboxInvariantError("Source OutboxMessage is not owned for delivery")
    if (
        source.audit_event_id != event.event_id
        or source.space_id != event.space_id
        or envelope.space_id != event.space_id
        or source.payload_digest != envelope.payload_digest
        or canonical_json_digest_v1(source.payload_snapshot) != source.payload_digest
    ):
        raise InboxInvariantError("Envelope source evidence is inconsistent")

    entry = ConsumerInboxEntry(
        consumer_name=consumer_name,
        event_id=envelope.event_id,
        source_message_id=envelope.message_id,
        space_id=envelope.space_id,
        payload_digest=envelope.payload_digest,
        status="received",
    )
    session.add(entry)
    await session.flush()
    return InboxReceiveResult(entry, created=True)


class InboxReceiverPublisher:
    """Dispatcher publisher that ACKs only after the Inbox transaction commits."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        consumer_name: str,
    ) -> None:
        self._session_maker = session_maker
        self.consumer_name = consumer_name

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        try:
            async with self._session_maker() as session:
                async with session.begin():
                    result = await receive_inbox_envelope(
                        session, consumer_name=self.consumer_name, envelope=message
                    )
            return PublishResult.acknowledged_result(
                external_message_id=f"inbox:{result.entry.id}"
            )
        except InboxIdempotencyConflict:
            return PublishResult.failed_result("inbox_idempotency_conflict", retryable=False)
        except InboxInvariantError:
            return PublishResult.failed_result("inbox_invariant_error", retryable=False)
        except Exception:
            return PublishResult.failed_result("inbox_commit_failed", retryable=True)


async def claim_inbox_batch(
    session: AsyncSession,
    *,
    consumer_name: str,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 60,
) -> list[ConsumerInboxEntry]:
    now = _utc_now()
    rows = list(
        (
            await session.scalars(
                select(ConsumerInboxEntry)
                .where(
                    ConsumerInboxEntry.consumer_name == consumer_name,
                    ConsumerInboxEntry.status == "received",
                    ConsumerInboxEntry.available_at <= now,
                    ConsumerInboxEntry.attempt_count < MAX_ATTEMPTS,
                )
                .order_by(
                    ConsumerInboxEntry.available_at,
                    ConsumerInboxEntry.received_at,
                    ConsumerInboxEntry.id,
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


async def reclaim_expired_inbox(
    session: AsyncSession,
    *,
    consumer_name: str,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 60,
) -> list[ConsumerInboxEntry]:
    now = _utc_now()
    rows = list(
        (
            await session.scalars(
                select(ConsumerInboxEntry)
                .where(
                    ConsumerInboxEntry.consumer_name == consumer_name,
                    ConsumerInboxEntry.status == "processing",
                    ConsumerInboxEntry.lease_expires_at <= now,
                    ConsumerInboxEntry.attempt_count < MAX_ATTEMPTS,
                )
                .order_by(ConsumerInboxEntry.lease_expires_at, ConsumerInboxEntry.id)
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


async def _owned_processing_entry(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    expected_row_version: int | None = None,
) -> ConsumerInboxEntry:
    now = _utc_now()
    entry = await session.scalar(
        select(ConsumerInboxEntry)
        .where(ConsumerInboxEntry.id == entry_id)
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
        raise InboxInvariantError("Inbox lease ownership is unavailable")
    return entry


async def release_inbox_for_retry(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    error: str,
    expected_row_version: int | None = None,
) -> ConsumerInboxEntry:
    entry = await _owned_processing_entry(
        session,
        entry_id=entry_id,
        worker_id=worker_id,
        expected_row_version=expected_row_version,
    )
    if entry.attempt_count >= MAX_ATTEMPTS:
        return await dead_letter_inbox(
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


async def complete_inbox(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    outcome_code: str,
    outcome_reference_type: str | None = None,
    outcome_reference_id: UUID | None = None,
    expected_row_version: int | None = None,
) -> ConsumerInboxEntry:
    entry = await _owned_processing_entry(
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


async def dead_letter_inbox(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    error: str,
    expected_row_version: int | None = None,
) -> ConsumerInboxEntry:
    entry = await _owned_processing_entry(
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
