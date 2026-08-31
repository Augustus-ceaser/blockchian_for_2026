from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
from typing import Protocol

from app.messaging.envelope import OutboxEnvelope


@dataclass(frozen=True)
class PublishResult:
    acknowledged: bool
    delivered_at: datetime | None
    retryable: bool
    error_code: str | None = None
    external_message_id: str | None = None

    @classmethod
    def acknowledged_result(
        cls, *, external_message_id: str | None = None
    ) -> PublishResult:
        return cls(
            acknowledged=True,
            delivered_at=datetime.now(timezone.utc),
            retryable=False,
            external_message_id=external_message_id,
        )

    @classmethod
    def failed_result(
        cls, error_code: str, *, retryable: bool = True
    ) -> PublishResult:
        return cls(
            acknowledged=False,
            delivered_at=None,
            retryable=retryable,
            error_code=error_code,
        )


class EventPublisher(Protocol):
    async def publish(self, message: OutboxEnvelope) -> PublishResult: ...


class FakePublisher:
    """Scriptable publisher for tests only."""

    def __init__(self, results: Iterable[PublishResult] = ()) -> None:
        self._results = deque(results)
        self.messages: list[OutboxEnvelope] = []

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        self.messages.append(message)
        if self._results:
            return self._results.popleft()
        return PublishResult.acknowledged_result(
            external_message_id=f"fake:{message.message_id}"
        )


class InMemoryPublisher:
    """Development-only publisher with destination-scoped event idempotency."""

    def __init__(
        self,
        consumer: Callable[[OutboxEnvelope], Awaitable[None] | None] | None = None,
    ) -> None:
        self._consumer = consumer
        self._handled: set[tuple[str, object]] = set()
        self.delivery_attempts: list[OutboxEnvelope] = []
        self.delivered: list[OutboxEnvelope] = []

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        self.delivery_attempts.append(message)
        key = (message.destination, message.event_id)
        if key not in self._handled:
            if self._consumer is not None:
                outcome = self._consumer(message)
                if inspect.isawaitable(outcome):
                    await outcome
            self._handled.add(key)
            self.delivered.append(message)
        return PublishResult.acknowledged_result(
            external_message_id=f"memory:{message.destination}:{message.event_id}"
        )


class UnavailablePublisher:
    """Fail-closed default when no real transport is configured."""

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        del message
        return PublishResult.failed_result("publisher_unavailable", retryable=True)
