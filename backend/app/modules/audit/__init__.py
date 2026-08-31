"""Append-only audit evidence and reliable transactional outbox."""

from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.audit.services import (
    AuditAppendResult,
    AuditCommandContext,
    AuditInvariantError,
    IdempotencyConflict,
    append_audit_event_with_outbox,
    begin_audited_command,
    canonical_json_digest_v1,
    canonical_json_text_v1,
    claim_outbox_batch,
    digest_idempotency_key,
    mark_outbox_failed,
    mark_outbox_published,
    reclaim_expired_outbox,
    sanitize_outbox_error,
)

__all__ = [
    "AuditAppendResult",
    "AuditCommandContext",
    "AuditEvent",
    "AuditInvariantError",
    "IdempotencyConflict",
    "OutboxMessage",
    "append_audit_event_with_outbox",
    "begin_audited_command",
    "canonical_json_digest_v1",
    "canonical_json_text_v1",
    "claim_outbox_batch",
    "digest_idempotency_key",
    "mark_outbox_failed",
    "mark_outbox_published",
    "reclaim_expired_outbox",
    "sanitize_outbox_error",
]
