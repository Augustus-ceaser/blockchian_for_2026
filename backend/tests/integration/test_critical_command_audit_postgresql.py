from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.audit import (
    AuditEvent,
    AuditInvariantError,
    IdempotencyConflict,
    OutboxMessage,
)
from app.modules.compute import (
    Artifact,
    ArtifactReview,
    AuditEvidenceUnavailable,
    claim_artifact_review,
    create_artifact,
    create_artifact_review,
    create_compute_job,
    decide_artifact_review,
    prepare_compute_run,
    reserve_compute_run,
)
from app.modules.compute.models import ComputeJob, ComputeRun
from app.modules.contracts import ContractObject, ContractParty, ContractRevision
from tests.integration.test_artifacts_postgresql import _make_succeeded_run
from tests.test_compute_models import (
    _algorithm_spec,
    _create_ready_job,
    _make_active_compute_contract,
)
from tests.test_contract_models import (
    _accept_required_bindings,
    _make_signed_revision,
    _system_audit_command,
)


TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_critical_commands_create_atomic_evidence_and_replay() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_critical_command_evidence(TEST_DATABASE_URL))


def test_concurrent_compute_job_command_returns_one_business_result() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_concurrent_job_idempotency(TEST_DATABASE_URL))


def test_business_flush_rolls_back_when_audit_append_fails(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_audit_failure_rollback(TEST_DATABASE_URL, monkeypatch))


def test_outbox_payload_failure_rolls_back_business_and_event(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_outbox_failure_rollback(TEST_DATABASE_URL, monkeypatch))


def test_each_other_protected_command_rolls_back_on_audit_failure(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_other_command_failure_rollbacks(TEST_DATABASE_URL, monkeypatch))


def test_legacy_service_calls_cannot_bypass_command_context() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_legacy_service_context_guards(TEST_DATABASE_URL))


async def _assert_critical_command_evidence(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            job, revision, _, _, _, user = await _create_ready_job(
                session, number=f"CTR-AUDITED-COMMANDS-{uuid4().hex}", run_limit=3
            )
            run_row = await prepare_compute_run(session, job, created_by=user.id)
            reserve_command = _system_audit_command(
                f"reserve:{run_row.id}", "medtrust.compute"
            )
            await reserve_compute_run(
                session, run_row, audit_command=reserve_command
            )
            revision_id, job_id, run_id = revision.id, job.id, run_row.id
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            run_row = await session.get(ComputeRun, run_id)
            assert run_row is not None
            await reserve_compute_run(
                session, run_row, audit_command=reserve_command
            )
            assert run_row.status == "reserved"
            assert await _event_count(
                session, "compute.run.reserved", "compute_run", run_id
            ) == 1
            assert await _outbox_count(session, "compute.run.reserved", run_id) == 2
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            assert await _event_count(
                session, "contract.revision.activated", "contract_revision", revision_id
            ) == 1
            assert await _event_count(
                session, "compute.job.created", "compute_job", job_id
            ) == 1

        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, succeeded_run, user = await _make_succeeded_run(
                session, number=f"CTR-AUDITED-ARTIFACT-{uuid4().hex}"
            )
            artifact_command = _system_audit_command(
                f"artifact:{succeeded_run.id}", "medtrust.artifact"
            )
            artifact_kwargs = {
                "run_id": succeeded_run.id,
                "artifact_type": "model_artifact",
                "content_digest": f"sha256:{'a' * 64}",
                "storage_reference": f"quarantine/artifacts/{succeeded_run.id}/audited",
                "size_bytes": 4096,
                "classification_level": "sensitive_personal_information",
                "audit_command": artifact_command,
            }
            artifact = await create_artifact(session, **artifact_kwargs)
            provider_id = UUID(
                artifact.output_policy_evaluation["provider_organization_id"]
            )
            review = await create_artifact_review(
                session,
                artifact=artifact,
                responsible_organization_id=provider_id,
                routing_rule_digest=f"sha256:{'b' * 64}",
            )
            await claim_artifact_review(session, review, user_id=user.id)
            review_command = _system_audit_command(
                f"artifact-review:{review.id}", "medtrust.artifact"
            )
            review_kwargs = {
                "decision": "approved",
                "reason_code": "policy_and_content_verified",
                "evidence": {
                    "schema_version": "artifact-review-evidence/v1",
                    "is_demo": True,
                },
                "audit_command": review_command,
            }
            await decide_artifact_review(session, review, **review_kwargs)
            artifact_id, review_id = artifact.id, review.id
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            replay_artifact = await create_artifact(session, **artifact_kwargs)
            replay_review = await session.get(ArtifactReview, review_id)
            assert replay_review is not None
            await decide_artifact_review(
                session, replay_review, **review_kwargs
            )
            assert replay_artifact.id == artifact_id
            assert await _event_count(
                session, "artifact.created", "artifact", artifact_id
            ) == 1
            assert await _outbox_count(session, "artifact.created", artifact_id) == 2
            assert await _event_count(
                session, "artifact.review.decided", "artifact_review", review_id
            ) == 1
            assert await _outbox_count(
                session, "artifact.review.decided", review_id
            ) == 2
            with pytest.raises(IdempotencyConflict):
                await create_artifact(
                    session,
                    **{
                        **artifact_kwargs,
                        "content_digest": f"sha256:{'c' * 64}",
                    },
                )
            await session.rollback()
    finally:
        await engine.dispose()


async def _assert_concurrent_job_idempotency(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, revision, contract_object, consumer, _, user = (
                await _make_active_compute_contract(
                    session,
                    number=f"CTR-CONCURRENT-JOB-{uuid4().hex}",
                    run_limit=3,
                )
            )
            revision_id = revision.id
            object_id = contract_object.id
            party_id = consumer.id
            organization_id = consumer.organization_id
            user_id = user.id
            await session.commit()

        command = _system_audit_command(
            f"concurrent-job:{revision_id}", "medtrust.compute"
        )
        ready = asyncio.Event()

        async def create() -> UUID:
            await ready.wait()
            async with AsyncSession(engine, expire_on_commit=False) as session:
                job = await create_compute_job(
                    session,
                    revision_id=revision_id,
                    party_id=party_id,
                    contract_object_id=object_id,
                    requester_organization_id=organization_id,
                    requester_user_id=user_id,
                    purpose_code="ai_training",
                    requested_output_types=["model_artifact"],
                    algorithm_spec_snapshot=_algorithm_spec(),
                    audit_command=command,
                )
                await session.commit()
                return job.id

        tasks = [asyncio.create_task(create()), asyncio.create_task(create())]
        ready.set()
        job_ids = await asyncio.gather(*tasks)
        assert job_ids[0] == job_ids[1]
        async with AsyncSession(engine) as session:
            assert await _event_count(
                session, "compute.job.created", "compute_job", job_ids[0]
            ) == 1
    finally:
        await engine.dispose()


async def _assert_audit_failure_rollback(database_url: str, monkeypatch) -> None:
    import app.modules.compute.services as compute_services

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, run_row, _ = await _make_succeeded_run(
                session, number=f"CTR-AUDIT-FAIL-{uuid4().hex}"
            )
            run_id = run_row.id
            await session.commit()

        async def fail_append(*args, **kwargs):
            raise RuntimeError("injected AuditEvent insert failure")

        monkeypatch.setattr(
            compute_services, "append_audit_event_with_outbox", fail_append
        )
        content_digest = f"sha256:{'d' * 64}"
        async with AsyncSession(engine, expire_on_commit=False) as session:
            with pytest.raises(RuntimeError, match="injected AuditEvent"):
                await create_artifact(
                    session,
                    run_id=run_id,
                    artifact_type="model_artifact",
                    content_digest=content_digest,
                    storage_reference=f"quarantine/artifacts/{run_id}/audit-fail",
                    size_bytes=1,
                    classification_level="sensitive_personal_information",
                    audit_command=_system_audit_command(
                        "artifact-audit-failure", "medtrust.artifact"
                    ),
                )
            # The command service rolled back; an accidental commit cannot
            # persist the already-flushed Artifact.
            await session.commit()

        async with AsyncSession(engine) as session:
            assert await session.scalar(
                select(func.count(Artifact.id)).where(
                    Artifact.content_digest == content_digest
                )
            ) == 0
    finally:
        await engine.dispose()


async def _assert_outbox_failure_rollback(database_url: str, monkeypatch) -> None:
    import app.modules.audit.services as audit_services

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, run_row, _ = await _make_succeeded_run(
                session, number=f"CTR-OUTBOX-FAIL-{uuid4().hex}"
            )
            run_id = run_row.id
            await session.commit()

        original_digest = audit_services.canonical_json_digest_v1

        def fail_outbox_payload(document):
            if document.get("message_schema") == "medtrust-event-envelope/v1":
                raise RuntimeError("injected Outbox payload digest failure")
            return original_digest(document)

        monkeypatch.setattr(
            audit_services, "canonical_json_digest_v1", fail_outbox_payload
        )
        content_digest = f"sha256:{'e' * 64}"
        async with AsyncSession(engine, expire_on_commit=False) as session:
            with pytest.raises(RuntimeError, match="Outbox payload digest"):
                await create_artifact(
                    session,
                    run_id=run_id,
                    artifact_type="model_artifact",
                    content_digest=content_digest,
                    storage_reference=f"quarantine/artifacts/{run_id}/outbox-fail",
                    size_bytes=1,
                    classification_level="sensitive_personal_information",
                    audit_command=_system_audit_command(
                        "artifact-outbox-failure", "medtrust.artifact"
                    ),
                )
            await session.commit()

        async with AsyncSession(engine) as session:
            assert await session.scalar(
                select(func.count(Artifact.id)).where(
                    Artifact.content_digest == content_digest
                )
            ) == 0
            assert await session.scalar(
                select(func.count(AuditEvent.event_id)).where(
                    AuditEvent.event_type == "artifact.created",
                    AuditEvent.evidence_snapshot["content_digest"].as_string()
                    == content_digest,
                )
            ) == 0
    finally:
        await engine.dispose()


async def _assert_other_command_failure_rollbacks(
    database_url: str, monkeypatch
) -> None:
    import app.modules.compute.services as compute_services
    import app.modules.contracts.services as contract_services

    async def fail_append(*args, **kwargs):
        raise RuntimeError("injected command AuditEvent failure")

    engine = create_async_engine(database_url)
    original_contract_append = contract_services.append_audit_event_with_outbox
    original_compute_append = compute_services.append_audit_event_with_outbox
    try:
        # Contract activation had already flushed `active` before append; the
        # command must still leave the committed revision `signed` on failure.
        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision, _, _ = await _make_signed_revision(
                session, number=f"CTR-ACTIVATE-ROLLBACK-{uuid4().hex}"
            )
            await _accept_required_bindings(session, revision)
            revision_id = revision.id
            await session.commit()
        monkeypatch.setattr(
            contract_services, "append_audit_event_with_outbox", fail_append
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision = await session.get(ContractRevision, revision_id)
            assert revision is not None
            with pytest.raises(RuntimeError, match="command AuditEvent"):
                await contract_services.activate_contract_revision(
                    session,
                    revision,
                    audit_command=_system_audit_command(
                        "activation-audit-failure", "medtrust.contract"
                    ),
                )
            await session.commit()
        async with AsyncSession(engine) as session:
            revision = await session.get(ContractRevision, revision_id)
            assert revision is not None and revision.status == "signed"
        monkeypatch.setattr(
            contract_services,
            "append_audit_event_with_outbox",
            original_contract_append,
        )

        # ComputeJob insert is flushed before append and must disappear.
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, revision, contract_object, consumer, _, user = (
                await _make_active_compute_contract(
                    session,
                    number=f"CTR-JOB-ROLLBACK-{uuid4().hex}",
                    run_limit=2,
                )
            )
            revision_id = revision.id
            object_id = contract_object.id
            party_id = consumer.id
            organization_id = consumer.organization_id
            user_id = user.id
            await session.commit()
        monkeypatch.setattr(
            compute_services, "append_audit_event_with_outbox", fail_append
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            with pytest.raises(RuntimeError, match="command AuditEvent"):
                await create_compute_job(
                    session,
                    revision_id=revision_id,
                    party_id=party_id,
                    contract_object_id=object_id,
                    requester_organization_id=organization_id,
                    requester_user_id=user_id,
                    purpose_code="ai_training",
                    requested_output_types=["model_artifact"],
                    algorithm_spec_snapshot=_algorithm_spec(),
                    audit_command=_system_audit_command(
                        "job-audit-failure", "medtrust.compute"
                    ),
                )
            await session.commit()
        async with AsyncSession(engine) as session:
            assert await session.scalar(
                select(func.count(ComputeJob.id)).where(
                    ComputeJob.contract_revision_id == revision_id
                )
            ) == 0
        monkeypatch.setattr(
            compute_services,
            "append_audit_event_with_outbox",
            original_compute_append,
        )

        # Run reservation appends before its state change; both must disappear.
        async with AsyncSession(engine, expire_on_commit=False) as session:
            job, _, _, _, _, user = await _create_ready_job(
                session,
                number=f"CTR-RUN-ROLLBACK-{uuid4().hex}",
                run_limit=2,
            )
            run_row = await prepare_compute_run(session, job, created_by=user.id)
            run_id = run_row.id
            await session.commit()
        monkeypatch.setattr(
            compute_services, "append_audit_event_with_outbox", fail_append
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            run_row = await session.get(ComputeRun, run_id)
            assert run_row is not None
            with pytest.raises(RuntimeError, match="command AuditEvent"):
                await reserve_compute_run(
                    session,
                    run_row,
                    audit_command=_system_audit_command(
                        "run-audit-failure", "medtrust.compute"
                    ),
                )
            await session.commit()
        async with AsyncSession(engine) as session:
            run_row = await session.get(ComputeRun, run_id)
            assert run_row is not None
            assert run_row.status == "prepared"
            assert run_row.reservation_ordinal is None
            assert await _event_count(
                session, "compute.run.reserved", "compute_run", run_id
            ) == 0
        monkeypatch.setattr(
            compute_services,
            "append_audit_event_with_outbox",
            original_compute_append,
        )

        # Terminal ArtifactReview fields are flushed before append and must
        # return to `claimed` if evidence creation fails.
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, succeeded_run, user = await _make_succeeded_run(
                session, number=f"CTR-REVIEW-ROLLBACK-{uuid4().hex}"
            )
            artifact = await create_artifact(
                session,
                run_id=succeeded_run.id,
                artifact_type="model_artifact",
                content_digest=f"sha256:{'f' * 64}",
                storage_reference=f"quarantine/artifacts/{succeeded_run.id}/review-fail",
                size_bytes=8,
                classification_level="sensitive_personal_information",
                audit_command=_system_audit_command(
                    "review-failure-artifact", "medtrust.artifact"
                ),
            )
            provider_id = UUID(
                artifact.output_policy_evaluation["provider_organization_id"]
            )
            review = await create_artifact_review(
                session,
                artifact=artifact,
                responsible_organization_id=provider_id,
                routing_rule_digest=f"sha256:{'1' * 64}",
            )
            await claim_artifact_review(session, review, user_id=user.id)
            review_id = review.id
            await session.commit()
        monkeypatch.setattr(
            compute_services, "append_audit_event_with_outbox", fail_append
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            review = await session.get(ArtifactReview, review_id)
            assert review is not None
            with pytest.raises(RuntimeError, match="command AuditEvent"):
                await decide_artifact_review(
                    session,
                    review,
                    decision="approved",
                    reason_code="policy_and_content_verified",
                    evidence={"schema_version": "artifact-review-evidence/v1"},
                    audit_command=_system_audit_command(
                        "review-audit-failure", "medtrust.artifact"
                    ),
                )
            await session.commit()
        async with AsyncSession(engine) as session:
            review = await session.get(ArtifactReview, review_id)
            assert review is not None
            assert review.status == "claimed"
            assert review.decision is None
    finally:
        monkeypatch.setattr(
            contract_services,
            "append_audit_event_with_outbox",
            original_contract_append,
        )
        monkeypatch.setattr(
            compute_services,
            "append_audit_event_with_outbox",
            original_compute_append,
        )
        await engine.dispose()


async def _assert_legacy_service_context_guards(database_url: str) -> None:
    import app.modules.contracts.services as contract_services

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision, _, _ = await _make_signed_revision(
                session, number=f"CTR-LEGACY-ACTIVATE-{uuid4().hex}"
            )
            await _accept_required_bindings(session, revision)
            with pytest.raises(AuditInvariantError, match="AuditCommandContext"):
                await contract_services.activate_contract_revision(session, revision)
            await session.rollback()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, revision, contract_object, consumer, _, user = (
                await _make_active_compute_contract(
                    session,
                    number=f"CTR-LEGACY-JOB-{uuid4().hex}",
                    run_limit=2,
                )
            )
            with pytest.raises(AuditEvidenceUnavailable, match="AuditCommandContext"):
                await create_compute_job(
                    session,
                    revision_id=revision.id,
                    party_id=consumer.id,
                    contract_object_id=contract_object.id,
                    requester_organization_id=consumer.organization_id,
                    requester_user_id=user.id,
                    purpose_code="ai_training",
                    requested_output_types=["model_artifact"],
                    algorithm_spec_snapshot=_algorithm_spec(),
                )
            await session.rollback()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            job, _, _, _, _, user = await _create_ready_job(
                session,
                number=f"CTR-LEGACY-RUN-{uuid4().hex}",
                run_limit=2,
            )
            run_row = await prepare_compute_run(session, job, created_by=user.id)
            with pytest.raises(AuditEvidenceUnavailable, match="AuditEvidenceUnavailable"):
                await reserve_compute_run(session, run_row)
            await session.rollback()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, succeeded_run, user = await _make_succeeded_run(
                session, number=f"CTR-LEGACY-ARTIFACT-{uuid4().hex}"
            )
            with pytest.raises(AuditEvidenceUnavailable, match="AuditCommandContext"):
                await create_artifact(
                    session,
                    run_id=succeeded_run.id,
                    artifact_type="model_artifact",
                    content_digest=f"sha256:{'2' * 64}",
                    storage_reference=f"quarantine/artifacts/{succeeded_run.id}/legacy",
                    size_bytes=4,
                    classification_level="sensitive_personal_information",
                )
            await session.rollback()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, succeeded_run, user = await _make_succeeded_run(
                session, number=f"CTR-LEGACY-REVIEW-{uuid4().hex}"
            )
            artifact = await create_artifact(
                session,
                run_id=succeeded_run.id,
                artifact_type="model_artifact",
                content_digest=f"sha256:{'3' * 64}",
                storage_reference=f"quarantine/artifacts/{succeeded_run.id}/review",
                size_bytes=4,
                classification_level="sensitive_personal_information",
                audit_command=_system_audit_command(
                    "legacy-review-artifact", "medtrust.artifact"
                ),
            )
            provider_id = UUID(
                artifact.output_policy_evaluation["provider_organization_id"]
            )
            review = await create_artifact_review(
                session,
                artifact=artifact,
                responsible_organization_id=provider_id,
                routing_rule_digest=f"sha256:{'4' * 64}",
            )
            await claim_artifact_review(session, review, user_id=user.id)
            with pytest.raises(AuditEvidenceUnavailable, match="AuditCommandContext"):
                await decide_artifact_review(
                    session,
                    review,
                    decision="approved",
                    reason_code="policy_and_content_verified",
                    evidence={"schema_version": "artifact-review-evidence/v1"},
                )
            assert review.status == "claimed"
            await session.rollback()
    finally:
        await engine.dispose()


async def _event_count(
    session: AsyncSession,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
) -> int:
    value = await session.scalar(
        select(func.count(AuditEvent.event_id)).where(
            AuditEvent.event_type == event_type,
            AuditEvent.subject_type == subject_type,
            AuditEvent.subject_id == subject_id,
        )
    )
    return int(value or 0)


async def _outbox_count(
    session: AsyncSession, event_type: str, subject_id: UUID
) -> int:
    value = await session.scalar(
        select(func.count(OutboxMessage.message_id))
        .join(AuditEvent, AuditEvent.event_id == OutboxMessage.audit_event_id)
        .where(
            AuditEvent.event_type == event_type,
            AuditEvent.subject_id == subject_id,
        )
    )
    return int(value or 0)
