"""Transport-neutral event envelopes and publisher contracts."""

from app.messaging.envelope import EnvelopeInvariantError, OutboxEnvelope
from app.messaging.errors import PublisherError
from app.messaging.publisher import (
    EventPublisher,
    FakePublisher,
    InMemoryPublisher,
    PublishResult,
    UnavailablePublisher,
)

__all__ = [
    "EnvelopeInvariantError",
    "EventPublisher",
    "FakePublisher",
    "InMemoryPublisher",
    "OutboxEnvelope",
    "PublishResult",
    "PublisherError",
    "UnavailablePublisher",
]
