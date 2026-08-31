from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.audit import (
    AuditInvariantError,
    IdempotencyConflict,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    canonical_json_text_v1,
    claim_outbox_batch,
    digest_idempotency_key,
    mark_outbox_failed,
    mark_outbox_published,
)
from tests.test_application_models import create_schema, make_engine
from tests.test_compute_models import _create_ready_job


def test_canonical_json_is_stable_and_rejects_non_integer_numbers() -> None:
    left = {"中文": [3, None, True], "a": {"z": 2, "b": "🙂"}}
    right = {"a": {"b": "🙂", "z": 2}, "中文": [3, None, True]}
    assert canonical_json_text_v1(left) == canonical_json_text_v1(right)
    assert canonical_json_digest_v1(left) == canonical_json_digest_v1(right)
    with pytest.raises(AuditInvariantError, match="non-integer"):
        canonical_json_text_v1({"score": 0.82})


def test_audit_chain_idempotency_and_outbox_lifecycle() -> None:
    asyncio.run(_audit_chain_and_outbox_lifecycle())


def test_audit_and_outbox_are_immutable() -> None:
    asyncio.run(_audit_and_outbox_are_immutable())


async def _seed_job(session, suffix: str):
    return await _create_ready_job(
        session, number=f"CTR-AUDIT-{suffix}-{uuid4().hex}", run_limit=3
    )


async def _append_job_event(session, job, *, raw_key: str, command_id=None):
    return await append_audit_event_with_outbox(
        session,
        space_id=job.space_id,
        event_type="compute.job.created",
        actor_type="system",
        actor_service_code="medtrust.compute",
        subject_type="compute_job",
        subject_id=job.id,
        result="success",
        correlation_id=uuid4(),
        command_id=command_id or uuid4(),
        idempotency_key=digest_idempotency_key(raw_key),
        evidence_snapshot={
            "schema_version": "compute-job-created/v1",
            "job_id": str(job.id),
            "authorization_digest": job.creation_authorization_evaluation_digest,
        },
    )


async def _audit_chain_and_outbox_lifecycle() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job, revision, *_ = await _seed_job(session, "CHAIN")
        command_id = uuid4()
        correlation_id = uuid4()
        key = digest_idempotency_key("audit-chain-command")
        first = await append_audit_event_with_outbox(
            session,
            space_id=job.space_id,
            event_type="compute.job.created",
            actor_type="system",
            actor_service_code="medtrust.compute",
            subject_type="compute_job",
            subject_id=job.id,
            result="success",
            correlation_id=correlation_id,
            command_id=command_id,
            idempotency_key=key,
            evidence_snapshot={"schema_version": "compute-job-created/v1", "job_id": str(job.id)},
        )
        replay = await append_audit_event_with_outbox(
            session,
            space_id=job.space_id,
            event_type="compute.job.created",
            actor_type="system",
            actor_service_code="medtrust.compute",
            subject_type="compute_job",
            subject_id=job.id,
            result="success",
            correlation_id=correlation_id,
            command_id=command_id,
            idempotency_key=key,
            evidence_snapshot={"schema_version": "compute-job-created/v1", "job_id": str(job.id)},
        )
        second = await append_audit_event_with_outbox(
            session,
            space_id=job.space_id,
            event_type="contract.revision.activated",
            actor_type="system",
            actor_service_code="medtrust.contract",
            subject_type="contract_revision",
            subject_id=revision.id,
            result="success",
            correlation_id=correlation_id,
            command_id=command_id,
            idempotency_key=key,
            evidence_snapshot={"schema_version": "contract-activated/v1", "revision_id": str(revision.id)},
        )
        assert first.event.stream_sequence >= 1
        assert replay.created is False and replay.event.event_id == first.event.event_id
        assert second.event.stream_sequence == first.event.stream_sequence + 1
        assert second.event.previous_event_digest == first.event.event_digest

        with pytest.raises(IdempotencyConflict):
            await append_audit_event_with_outbox(
                session,
                space_id=job.space_id,
                event_type="compute.job.created",
                actor_type="system",
                actor_service_code="medtrust.compute",
                subject_type="compute_job",
                subject_id=job.id,
                result="success",
                correlation_id=correlation_id,
                command_id=command_id,
                idempotency_key=key,
                evidence_snapshot={"schema_version": "compute-job-created/v1", "tampered": True},
            )

        claimed = await claim_outbox_batch(
            session, worker_id="audit-test-worker", batch_size=1, lease_seconds=60
        )
        assert len(claimed) == 1 and claimed[0].status == "processing"
        await mark_outbox_published(
            session, message_id=claimed[0].message_id, worker_id="audit-test-worker"
        )
        assert claimed[0].status == "published"

        claimed = await claim_outbox_batch(
            session, worker_id="audit-test-worker-2", batch_size=1, lease_seconds=60
        )
        await mark_outbox_failed(
            session,
            message_id=claimed[0].message_id,
            worker_id="audit-test-worker-2",
            error="Authorization: Bearer demo-secret https://example.test/path?token=hidden",
            retryable=False,
        )
        assert claimed[0].status == "dead_letter"
        assert "demo-secret" not in (claimed[0].last_error or "")
        await session.commit()
    await engine.dispose()


async def _audit_and_outbox_are_immutable() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job, *_ = await _seed_job(session, "IMMUTABLE")
        appended = await _append_job_event(
            session, job, raw_key=f"immutable-{uuid4()}"
        )
        await session.commit()
        appended.event.result = "failure"
        with pytest.raises(AuditInvariantError, match="append-only"):
            await session.flush()
        await session.rollback()

    async with factory() as session:
        job, *_ = await _seed_job(session, "OUTBOX")
        appended = await _append_job_event(session, job, raw_key=f"outbox-{uuid4()}")
        await session.commit()
        message = appended.messages[0]
        message.topic = "tampered.topic"
        with pytest.raises(AuditInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()
    await engine.dispose()
