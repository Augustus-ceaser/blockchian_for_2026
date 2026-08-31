from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.execution.callback import ExecutionCallbackEnvelope
from app.execution.callback_processor import process_execution_callback
from app.modules.compute import (
    Artifact,
    ArtifactReview,
    AuditEvidenceUnavailable,
    claim_artifact_review,
    create_artifact,
    create_artifact_review,
    decide_artifact_review,
    prepare_compute_run,
    release_artifact,
    reserve_compute_run,
)
from app.modules.compute.models import ComputeJob, ComputeRun
from app.modules.callback_inbox import claim_callback_batch, receive_execution_callback
from app.modules.contracts import Policy, PolicyConstraint, canonical_document_digest
from app.messaging import OutboxEnvelope
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    OutboxMessage,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.inbox import (
    claim_inbox_batch,
    complete_inbox,
    receive_inbox_envelope,
)
from tests.test_compute_models import _create_ready_job
from tests.test_contract_models import _system_audit_command

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


def test_artifact_schema_and_fail_closed_release_gate() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_artifact_schema(TEST_DATABASE_URL))


def test_artifact_review_lifecycle_and_direct_sql_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_artifact_guards(TEST_DATABASE_URL))


def test_policy_deny_blocks_direct_artifact_approval() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_policy_deny_guard(TEST_DATABASE_URL))


async def _assert_artifact_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260725_0032"
            assert await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_tables "
                    "WHERE schemaname='medtrust'"
                )
            ) == 54
            triggers = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tg.tgname FROM pg_catalog.pg_trigger tg "
                            "JOIN pg_catalog.pg_class c ON c.oid=tg.tgrelid "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='medtrust' AND NOT tg.tgisinternal "
                            "AND tg.tgname LIKE '%artifact%'"
                        )
                    )
                ).all()
            )
            assert triggers == {
                "trg_artifact_guard",
                "trg_artifact_review_guard",
                "trg_artifact_review_decisions_immutable",
            }
            definition = await connection.scalar(
                text(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='medtrust' "
                    "AND p.proname='assert_artifact_release_audit_ready_v7'"
                )
            )
            assert definition is not None and "AuditEvidenceUnavailable" in definition
    finally:
        await engine.dispose()


async def _make_succeeded_run(session: AsyncSession, *, number: str):
    job, _, _, _, _, user = await _create_ready_job(
        session, number=f"{number}-{uuid4().hex}", run_limit=5
    )
    run_row = await prepare_compute_run(session, job, created_by=user.id)
    await reserve_compute_run(
        session,
        run_row,
        audit_command=_system_audit_command(
            f"reserve-artifact-run:{number}", "medtrust.compute"
        ),
    )
    reservation_event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "compute.run.reserved",
            AuditEvent.subject_id == run_row.id,
        )
    )
    assert reservation_event is not None
    dispatch_message = await session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.audit_event_id == reservation_event.event_id,
            OutboxMessage.destination == "compute.dispatch",
        )
    )
    assert dispatch_message is not None
    now = datetime.now(timezone.utc)
    dispatch_message._delivery_transition_validated = True
    dispatch_message.status = "processing"
    dispatch_message.attempt_count = 1
    dispatch_message.locked_at = now
    dispatch_message.lock_owner = "artifact-test-dispatcher"
    dispatch_message.lease_expires_at = now + timedelta(seconds=120)
    dispatch_message.row_version += 1
    await session.flush()
    received = await receive_inbox_envelope(
        session,
        consumer_name="execution-coordinator",
        envelope=OutboxEnvelope.from_records(dispatch_message, reservation_event),
    )
    claimed = await claim_inbox_batch(
        session,
        consumer_name="execution-coordinator",
        worker_id="artifact-test-coordinator",
        batch_size=100,
        lease_seconds=120,
    )
    assert any(entry.id == received.entry.id for entry in claimed)
    dispatch_receipt_digest = f"sha256:{'6' * 64}"
    command_id = uuid5(NAMESPACE_URL, f"artifact-test-dispatch:{run_row.id}")
    await append_audit_event_with_outbox(
        session,
        space_id=run_row.space_id,
        event_type="compute.run.dispatched",
        subject_type="compute_run",
        subject_id=run_row.id,
        result="success",
        evidence_snapshot={
            "schema_version": "compute-run-dispatched-evidence/v1",
            "compute_run_id": str(run_row.id),
            "compute_job_id": str(job.id),
            "contract_revision_id": str(run_row.contract_revision_id),
            "request_digest": canonical_json_digest_v1(
                {"schema_version": "artifact-test-request/v1", "run_id": str(run_row.id)}
            ),
            "dispatch_receipt_digest": dispatch_receipt_digest,
            "submission_idempotency_digest": digest_idempotency_key(
                f"artifact-test-submit:{run_row.id}"
            ),
            "external_execution_reference_digest": canonical_json_digest_v1(
                {"external_execution_id": f"demo-execution-{run_row.id}"}
            ),
        },
        **AuditCommandContext(
            command_id=command_id,
            idempotency_key=digest_idempotency_key(
                f"artifact-test-dispatch:{run_row.id}"
            ),
            correlation_id=reservation_event.correlation_id,
            causation_id=reservation_event.event_id,
            actor_type="system",
            actor_service_code="medtrust.compute",
        ).append_kwargs(),
    )
    await complete_inbox(
        session,
        entry_id=received.entry.id,
        worker_id="artifact-test-coordinator",
        outcome_code="executor_submitted",
        outcome_reference_type="compute_run",
        outcome_reference_id=run_row.id,
    )
    run_row._transition_validated = True
    run_row.status = "dispatched"
    run_row.execution_reference = f"demo-execution-{run_row.id}"
    run_row.dispatch_receipt_digest = dispatch_receipt_digest
    run_row.dispatched_at = now
    run_row.row_version += 1
    await session.commit()

    async def callback(callback_type: str, payload: dict[str, object]) -> None:
        received_callback = await receive_execution_callback(
            session,
            envelope=ExecutionCallbackEnvelope(
                space_id=run_row.space_id,
                compute_run_id=run_row.id,
                executor_namespace="medtrust.artifact-test-executor.v1",
                external_execution_id=f"demo-execution-{run_row.id}",
                callback_id=f"artifact-test:{run_row.id}:{callback_type}",
                callback_type=callback_type,
                callback_schema_version=1,
                occurred_at=datetime.now(timezone.utc),
                payload_snapshot=payload,
                execution_evidence_digest=canonical_json_digest_v1(payload),
                authentication_evidence_digest=canonical_json_digest_v1(
                    {"schema_version": "artifact-test-auth/v1", "run_id": str(run_row.id)}
                ),
                correlation_id=reservation_event.correlation_id,
                causation_id=reservation_event.event_id,
            ),
        )
        await session.commit()
        claimed = await claim_callback_batch(
            session,
            worker_id="artifact-test-callback-worker",
            batch_size=1,
            lease_seconds=120,
        )
        assert any(row.id == received_callback.entry.id for row in claimed)
        await session.commit()
        await process_execution_callback(
            session,
            entry_id=received_callback.entry.id,
            worker_id="artifact-test-callback-worker",
        )
        await session.commit()

    await callback(
        "execution.started",
        {
            "schema_version": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "runtime_summary": {"executor": "artifact-test"},
        },
    )
    await callback(
        "execution.completed",
        {
            "schema_version": 1,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "output_manifest": [
                {
                    "name": "synthetic-placeholder",
                    "media_type": "application/json",
                    "size_bytes": 1,
                    "digest": f"sha256:{'8' * 64}",
                }
            ],
            "output_digest": f"sha256:{'8' * 64}",
            "execution_summary": {"mode": "synthetic"},
            "resource_usage_summary": {"cpu_seconds": 0},
            "artifact_type": "model_artifact",
            "object_storage_ref": f"quarantine/{run_row.id}/synthetic-placeholder.json",
        },
    )
    await session.refresh(run_row)
    await session.refresh(job)
    return job, run_row, user


async def _create_reviewed_artifact(session: AsyncSession, *, number: str):
    job, run_row, user = await _make_succeeded_run(session, number=number)
    artifact = await create_artifact(
        session,
        run_id=run_row.id,
        artifact_type="model_artifact",
        content_digest=f"sha256:{uuid4().hex * 2}",
        storage_reference=f"quarantine/artifacts/{run_row.id}/model",
        size_bytes=4096,
        classification_level="sensitive_personal_information",
        audit_command=_system_audit_command(
            f"create-pg-artifact:{number}", "medtrust.artifact"
        ),
    )
    provider_id = UUID(artifact.output_policy_evaluation["provider_organization_id"])
    review = await create_artifact_review(
        session,
        artifact=artifact,
        responsible_organization_id=provider_id,
        routing_rule_digest=canonical_document_digest(
            {"schema_version": "artifact-review-routing/v1", "manual": True}
        ),
    )
    await claim_artifact_review(session, review, user_id=user.id)
    await decide_artifact_review(
        session,
        review,
        decision="approved",
        reason_code="policy_and_content_verified",
        evidence={"schema_version": "artifact-review-evidence/v1", "is_demo": True},
        audit_command=_system_audit_command(
            f"decide-pg-artifact:{number}", "medtrust.artifact"
        ),
    )
    return job, run_row, artifact, review, user


async def _assert_artifact_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, _, artifact, review, _ = await _create_reviewed_artifact(
                session, number="CTR-ARTIFACT-PG"
            )
            artifact_id = artifact.id
            review_id = review.id
            artifact_values = {
                "space_id": artifact.space_id,
                "compute_job_id": artifact.compute_job_id,
                "compute_run_id": artifact.compute_run_id,
                "artifact_type": artifact.artifact_type,
                "content_digest": artifact.content_digest,
                "storage_reference": artifact.storage_reference,
                "size_bytes": artifact.size_bytes,
                "classification_level": artifact.classification_level,
                "output_policy_evaluation": artifact.output_policy_evaluation,
                "output_policy_evaluation_digest": artifact.output_policy_evaluation_digest,
            }
            review_values = {
                "space_id": review.space_id,
                "artifact_id": review.artifact_id,
                "target_content_digest": review.target_content_digest,
                "responsible_organization_id": review.responsible_organization_id,
                "routing_rule_digest": review.routing_rule_digest,
            }
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            artifact = await session.get(Artifact, artifact_id)
            assert artifact is not None and artifact.release_status == "quarantined"
            with pytest.raises(AuditEvidenceUnavailable, match="AuditEvidenceUnavailable"):
                await release_artifact(session, artifact)

        async def rejected(statement: object, expected: str) -> None:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                try:
                    with pytest.raises(DBAPIError) as caught:
                        await session.execute(statement)  # type: ignore[arg-type]
                    assert expected in str(caught.value.orig)
                finally:
                    await session.rollback()

        await rejected(
            update(Artifact)
            .where(Artifact.id == artifact_id)
            .values(
                storage_reference="quarantine/artifacts/tampered",
                row_version=Artifact.row_version + 1,
            ),
            "immutable",
        )
        await rejected(
            insert(ArtifactReview).values(
                id=uuid4(), status="pending", row_version=1, **review_values
            ),
            "duplicate key",
        )
        await rejected(
            update(ArtifactReview)
            .where(ArtifactReview.id == review_id)
            .values(comment="tampered", row_version=ArtifactReview.row_version + 1),
            "terminal ArtifactReview is immutable",
        )
        await rejected(
            delete(ArtifactReview).where(ArtifactReview.id == review_id),
            "ArtifactReview cannot be deleted",
        )
        await rejected(
            update(Artifact)
            .where(Artifact.id == artifact_id)
            .values(
                release_status="released",
                release_evidence={"schema_version": "artifact-release/v1"},
                release_evidence_digest=f"sha256:{'a' * 64}",
                released_at=text("clock_timestamp()"),
                row_version=Artifact.row_version + 1,
            ),
            "AuditEvidenceUnavailable",
        )
        await rejected(
            insert(Artifact).values(
                id=uuid4(),
                artifact_no=99,
                release_status="quarantined",
                row_version=1,
                **{**artifact_values, "space_id": uuid4()},
            ),
            "inconsistent",
        )
    finally:
        await engine.dispose()


async def _assert_policy_deny_guard(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            job, run_row, user = await _make_succeeded_run(
                session, number="CTR-ARTIFACT-PG-DENY"
            )
            deny_policy_id = uuid4()
            deny_digest = f"sha256:{'b' * 64}"
            await session.execute(
                text("ALTER TABLE medtrust.policies DISABLE TRIGGER trg_policy_structure")
            )
            await session.execute(
                text(
                    "ALTER TABLE medtrust.policy_constraints "
                    "DISABLE TRIGGER trg_policy_constraint_structure"
                )
            )
            try:
                await session.execute(
                    insert(Policy).values(
                        id=deny_policy_id,
                        contract_revision_id=job.contract_revision_id,
                        policy_code=f"deny-output-{uuid4().hex}",
                        policy_type="prohibition",
                        effect="deny",
                        subject_contract_party_id=job.requester_contract_party_id,
                        contract_object_id=job.contract_object_id,
                        action_code="export_artifact",
                        priority=999,
                        policy_digest=deny_digest,
                        created_by=user.id,
                    )
                )
                await session.execute(
                    insert(PolicyConstraint).values(
                        id=uuid4(),
                        policy_id=deny_policy_id,
                        constraint_name="output_type",
                        operator="in",
                        value=["model_artifact"],
                        position_no=1,
                    )
                )
            except Exception:
                await session.rollback()
                async with engine.begin() as connection:
                    await connection.execute(
                        text("ALTER TABLE medtrust.policies ENABLE TRIGGER trg_policy_structure")
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE medtrust.policy_constraints "
                            "ENABLE TRIGGER trg_policy_constraint_structure"
                        )
                    )
                raise
            else:
                await session.execute(
                    text("ALTER TABLE medtrust.policies ENABLE TRIGGER trg_policy_structure")
                )
                await session.execute(
                    text(
                        "ALTER TABLE medtrust.policy_constraints "
                        "ENABLE TRIGGER trg_policy_constraint_structure"
                    )
                )

            artifact = await create_artifact(
                session,
                run_id=run_row.id,
                artifact_type="model_artifact",
                content_digest=f"sha256:{uuid4().hex * 2}",
                storage_reference=f"quarantine/artifacts/{run_row.id}/denied",
                size_bytes=512,
                classification_level="sensitive_personal_information",
                audit_command=_system_audit_command(
                    "create-denied-pg-artifact", "medtrust.artifact"
                ),
            )
            assert artifact.output_policy_evaluation["decision"] == "deny"
            provider_id = UUID(
                artifact.output_policy_evaluation["provider_organization_id"]
            )
            review = await create_artifact_review(
                session,
                artifact=artifact,
                responsible_organization_id=provider_id,
                routing_rule_digest=f"sha256:{'c' * 64}",
            )
            await claim_artifact_review(session, review, user_id=user.id)

            with pytest.raises(DBAPIError) as caught:
                await session.execute(
                    update(ArtifactReview)
                    .where(ArtifactReview.id == review.id)
                    .values(
                        status="decided",
                        decision="approved",
                        reason_code="manual_override_attempt",
                        decision_evidence={"schema_version": "artifact-review/v1"},
                        decision_digest=f"sha256:{'d' * 64}",
                        decided_at=text("clock_timestamp()"),
                        row_version=ArtifactReview.row_version + 1,
                    )
                )
            assert "Policy deny cannot be overridden" in str(caught.value.orig)
            await session.rollback()
    finally:
        await engine.dispose()

