from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
import signal
import socket
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import close_database, session_factory
from app.messaging import (
    EventPublisher,
    InMemoryPublisher,
    OutboxEnvelope,
    PublishResult,
    PublisherError,
    UnavailablePublisher,
)
from app.modules.audit import (
    AuditEvent,
    AuditInvariantError,
    OutboxMessage,
    claim_outbox_batch,
    mark_outbox_failed,
    mark_outbox_published,
    reclaim_expired_outbox,
)
from app.execution.coordinator import ExecutionCoordinatorConsumer

logger = logging.getLogger("medtrust.outbox_dispatcher")


@dataclass(frozen=True)
class DispatcherConfig:
    worker_id: str
    batch_size: int = 50
    poll_interval: float = 1.0
    lease_seconds: int = 60
    max_attempts: int = 10
    shutdown_timeout: float = 30.0

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if self.poll_interval < 0.05:
            raise ValueError("poll_interval must be at least 0.05 seconds")
        if not 15 <= self.lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 15 and 300")
        if self.max_attempts != 10:
            raise ValueError("max_attempts is frozen at 10 by the v8 schema")
        if self.shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")


@dataclass
class DispatcherStats:
    claimed_count: int = 0
    published_count: int = 0
    retry_count: int = 0
    dead_letter_count: int = 0
    lease_reclaimed_count: int = 0
    ownership_lost_count: int = 0


@dataclass(frozen=True)
class _ClaimedDelivery:
    message_id: UUID
    attempt_count: int
    envelope: OutboxEnvelope


class OutboxDispatcher:
    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        config: DispatcherConfig,
    ) -> None:
        self._session_maker = session_maker
        self._publisher = publisher
        self.config = config
        self.stats = DispatcherStats()
        self._stop_requested = asyncio.Event()

    def request_stop(self) -> None:
        self._stop_requested.set()

    async def _claim(self) -> list[_ClaimedDelivery]:
        if self._stop_requested.is_set():
            return []
        async with self._session_maker() as session:
            async with session.begin():
                reclaimed = await reclaim_expired_outbox(
                    session,
                    worker_id=self.config.worker_id,
                    batch_size=self.config.batch_size,
                    lease_seconds=self.config.lease_seconds,
                )
                remaining = self.config.batch_size - len(reclaimed)
                pending = (
                    await claim_outbox_batch(
                        session,
                        worker_id=self.config.worker_id,
                        batch_size=remaining,
                        lease_seconds=self.config.lease_seconds,
                    )
                    if remaining
                    else []
                )
                rows = [*reclaimed, *pending]
                deliveries: list[_ClaimedDelivery] = []
                for message in rows:
                    event = await session.get(AuditEvent, message.audit_event_id)
                    if event is None:
                        raise AuditInvariantError(
                            "OutboxMessage refers to a missing AuditEvent"
                        )
                    deliveries.append(
                        _ClaimedDelivery(
                            message_id=message.message_id,
                            attempt_count=message.attempt_count,
                            envelope=OutboxEnvelope.from_records(message, event),
                        )
                    )
        self.stats.claimed_count += len(deliveries)
        self.stats.lease_reclaimed_count += len(reclaimed)
        return deliveries

    async def _settle_success(self, delivery: _ClaimedDelivery) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await mark_outbox_published(
                    session,
                    message_id=delivery.message_id,
                    worker_id=self.config.worker_id,
                )
        self.stats.published_count += 1

    async def _settle_failure(
        self,
        delivery: _ClaimedDelivery,
        *,
        error_code: str,
        retryable: bool,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                message = await mark_outbox_failed(
                    session,
                    message_id=delivery.message_id,
                    worker_id=self.config.worker_id,
                    error=f"publisher:{error_code}",
                    retryable=retryable,
                )
                terminal = message.status == "dead_letter"
        if terminal:
            self.stats.dead_letter_count += 1
        else:
            self.stats.retry_count += 1

    def _log(self, delivery: _ClaimedDelivery, result: str, error_code: str | None = None) -> None:
        fields: dict[str, Any] = {
            "message_id": str(delivery.message_id),
            "event_id": str(delivery.envelope.event_id),
            "event_type": delivery.envelope.event_type,
            "attempt_count": delivery.attempt_count,
            "worker_id": self.config.worker_id,
            "result": result,
            "error_code": error_code or "none",
        }
        logger.info(
            "outbox_delivery message_id=%(message_id)s event_id=%(event_id)s "
            "event_type=%(event_type)s attempt_count=%(attempt_count)s "
            "worker_id=%(worker_id)s result=%(result)s error_code=%(error_code)s",
            fields,
        )

    async def _deliver(self, delivery: _ClaimedDelivery) -> None:
        try:
            result = await self._publisher.publish(delivery.envelope)
            if result.acknowledged:
                if result.delivered_at is None:
                    raise PublisherError(
                        "publisher_ack_missing_timestamp", retryable=True
                    )
                await self._settle_success(delivery)
                self._log(delivery, "published")
                return
            error_code = result.error_code or "publisher_not_acknowledged"
            await self._settle_failure(
                delivery,
                error_code=error_code,
                retryable=result.retryable,
            )
            self._log(delivery, "failed", error_code)
        except PublisherError as exc:
            await self._handle_failure(
                delivery, error_code=exc.error_code, retryable=exc.retryable
            )
        except AuditInvariantError:
            self.stats.ownership_lost_count += 1
            self._log(delivery, "ownership_lost", "lease_unavailable")
        except Exception as exc:  # Publisher implementations are an isolation boundary.
            await self._handle_failure(
                delivery,
                error_code=f"publisher_exception:{type(exc).__name__}",
                retryable=True,
            )

    async def _handle_failure(
        self,
        delivery: _ClaimedDelivery,
        *,
        error_code: str,
        retryable: bool,
    ) -> None:
        try:
            await self._settle_failure(
                delivery, error_code=error_code, retryable=retryable
            )
            self._log(delivery, "failed", error_code)
        except AuditInvariantError:
            self.stats.ownership_lost_count += 1
            self._log(delivery, "ownership_lost", "lease_unavailable")

    async def dispatch_once(self) -> int:
        deliveries = await self._claim()
        for delivery in deliveries:
            await self._deliver(delivery)
        return len(deliveries)

    async def run_forever(self) -> None:
        while not self._stop_requested.is_set():
            delivery_task = asyncio.create_task(self.dispatch_once())
            stop_task = asyncio.create_task(self._stop_requested.wait())
            done, _ = await asyncio.wait(
                {delivery_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done and not delivery_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(delivery_task), self.config.shutdown_timeout
                    )
                except TimeoutError:
                    delivery_task.cancel()
                    await asyncio.gather(delivery_task, return_exceptions=True)
                return
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            claimed = await delivery_task
            if claimed == 0 and not self._stop_requested.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_requested.wait(), self.config.poll_interval
                    )
                except TimeoutError:
                    pass


def _build_worker_id() -> str:
    return f"outbox:{socket.gethostname()}:{os.getpid()}"[:96]


def _build_publisher(kind: str) -> EventPublisher:
    if kind == "in_memory":
        logger.warning(
            "using development-only in-memory publisher; no external reliability is implied"
        )
        return InMemoryPublisher()
    if kind == "database_inbox":
        return _DatabaseInboxDemoPublisher(session_factory)
    return UnavailablePublisher()


class _DatabaseInboxDemoPublisher:
    """Route executable commands durably and ACK local read projections.

    This publisher is deliberately scoped to the single-machine demo. It is
    not an external message broker and makes no cross-host delivery claim.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._coordinator = ExecutionCoordinatorConsumer(session_maker)

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        if message.destination == "compute.dispatch":
            return await self._coordinator.publish(message)
        return PublishResult.acknowledged_result(
            external_message_id=f"demo-local-projection:{message.message_id}"
        )


async def _run() -> None:
    settings = get_settings()
    if settings.outbox_publisher == "unavailable":
        raise RuntimeError(
            "Outbox Dispatcher has no configured Publisher; refusing to claim messages"
        )
    dispatcher = OutboxDispatcher(
        session_maker=session_factory,
        publisher=_build_publisher(settings.outbox_publisher),
        config=DispatcherConfig(
            worker_id=_build_worker_id(),
            batch_size=settings.outbox_batch_size,
            poll_interval=settings.outbox_poll_interval,
            lease_seconds=settings.outbox_lease_seconds,
            max_attempts=settings.outbox_max_attempts,
            shutdown_timeout=settings.outbox_shutdown_timeout,
        ),
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, dispatcher.request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda *_: dispatcher.request_stop())
    try:
        await dispatcher.run_forever()
    finally:
        await close_database()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
