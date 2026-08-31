from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.compute import (
    Artifact,
    ArtifactReview,
    AuditEvidenceUnavailable,
    ComputeInvariantError,
    claim_artifact_review,
    create_artifact,
    create_artifact_review,
    decide_artifact_review,
    prepare_compute_run,
    release_artifact,
)
from app.modules.compute.models import ComputeRun
from app.modules.contracts import canonical_document_digest
from tests.test_application_models import create_schema, make_engine
from tests.test_compute_models import _create_ready_job
from tests.test_contract_models import _system_audit_command


def test_artifact_is_quarantined_and_immutable() -> None:
    asyncio.run(_artifact_is_quarantined_and_immutable())


def test_artifact_review_is_single_terminal_evidence() -> None:
    asyncio.run(_artifact_review_is_single_terminal_evidence())


def test_rejected_artifact_requires_new_artifact() -> None:
    asyncio.run(_rejected_artifact_requires_new_artifact())


def test_policy_deny_cannot_be_overridden() -> None:
    asyncio.run(_policy_deny_cannot_be_overridden())


async def _succeeded_run(session, *, number: str):
    job, _, _, _, _, user = await _create_ready_job(session, number=number)
    run = await prepare_compute_run(session, job, created_by=user.id)
    await session.execute(
        update(ComputeRun)
        .where(ComputeRun.id == run.id)
        .values(
            status="succeeded",
            completion_receipt_digest=f"sha256:{'d' * 64}",
            finished_at=datetime.now(timezone.utc),
            row_version=ComputeRun.row_version + 1,
        )
    )
    await session.refresh(run)
    return job, run, user


async def _new_artifact(session, *, number: str, digest_char: str = "e"):
    job, run, user = await _succeeded_run(session, number=number)
    artifact = await create_artifact(
        session,
        run_id=run.id,
        artifact_type="model_artifact",
        content_digest=f"sha256:{digest_char * 64}",
        storage_reference=f"quarantine/artifacts/{run.id}/{digest_char}",
        size_bytes=2048,
        classification_level="sensitive_personal_information",
        audit_command=_system_audit_command(
            f"create-artifact:{number}:{digest_char}", "medtrust.artifact"
        ),
    )
    return job, run, artifact, user


async def _artifact_is_quarantined_and_immutable() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, _, artifact, _ = await _new_artifact(
            session, number="CTR-ARTIFACT-001"
        )
        assert artifact.release_status == "quarantined"
        assert artifact.release_evidence is None
        assert artifact.output_policy_evaluation["decision"] == "permit"
        await session.commit()

        artifact.storage_reference = "quarantine/artifacts/tampered"
        with pytest.raises(ComputeInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _artifact_review_is_single_terminal_evidence() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, _, artifact, user = await _new_artifact(
            session, number="CTR-ARTIFACT-002"
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
        artifact_id = artifact.id
        artifact_space_id = artifact.space_id
        artifact_digest = artifact.content_digest
        user_id = user.id
        await session.commit()
        duplicate = ArtifactReview(
            space_id=artifact_space_id,
            artifact_id=artifact_id,
            target_content_digest=artifact_digest,
            responsible_organization_id=provider_id,
            status="pending",
            routing_rule_digest=f"sha256:{'f' * 64}",
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
        review = await session.scalar(
            select(ArtifactReview).where(ArtifactReview.artifact_id == artifact_id)
        )
        assert review is not None
        artifact = await session.get(Artifact, artifact_id)
        assert artifact is not None

        await claim_artifact_review(session, review, user_id=user_id)
        await decide_artifact_review(
            session,
            review,
            decision="approved",
            reason_code="policy_and_content_verified",
            evidence={"schema_version": "artifact-review-evidence/v1", "is_demo": True},
            audit_command=_system_audit_command(
                "approve-artifact-review", "medtrust.artifact"
            ),
        )
        assert review.status == "decided"
        assert artifact.release_status == "quarantined"
        with pytest.raises(AuditEvidenceUnavailable, match="AuditEvidenceUnavailable"):
            await release_artifact(session, artifact)

        await session.commit()
        review.comment = "tampered"
        with pytest.raises(ComputeInvariantError, match="terminal"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _rejected_artifact_requires_new_artifact() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, run, artifact, user = await _new_artifact(
            session, number="CTR-ARTIFACT-003"
        )
        provider_id = UUID(artifact.output_policy_evaluation["provider_organization_id"])
        review = await create_artifact_review(
            session,
            artifact=artifact,
            responsible_organization_id=provider_id,
            routing_rule_digest=f"sha256:{'1' * 64}",
        )
        await claim_artifact_review(session, review, user_id=user.id)
        await decide_artifact_review(
            session,
            review,
            decision="rejected",
            reason_code="output_contains_disallowed_detail",
            evidence={"schema_version": "artifact-review-evidence/v1"},
            audit_command=_system_audit_command(
                "reject-artifact-review", "medtrust.artifact"
            ),
        )
        run_id = run.id
        artifact_id = artifact.id
        await session.commit()

        artifact.content_digest = f"sha256:{'2' * 64}"
        with pytest.raises(ComputeInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()

        run = await session.get(ComputeRun, run_id)
        assert run is not None
        replacement = await create_artifact(
            session,
            run_id=run.id,
            artifact_type="model_artifact",
            content_digest=f"sha256:{'3' * 64}",
            storage_reference=f"quarantine/artifacts/{run.id}/replacement",
            size_bytes=1024,
            classification_level="sensitive_personal_information",
            audit_command=_system_audit_command(
                "create-replacement-artifact", "medtrust.artifact"
            ),
        )
        assert replacement.id != artifact_id
        assert replacement.artifact_no == 2
    await engine.dispose()


async def _policy_deny_cannot_be_overridden() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, run, artifact, user = await _new_artifact(
            session, number="CTR-ARTIFACT-004"
        )
        evaluation = {
            **artifact.output_policy_evaluation,
            "decision": "deny",
            "deny_policy_digests": [f"sha256:{'9' * 64}"],
        }
        denied = Artifact(
            space_id=artifact.space_id,
            compute_job_id=artifact.compute_job_id,
            compute_run_id=run.id,
            artifact_no=2,
            artifact_type="model_artifact",
            content_digest=f"sha256:{'4' * 64}",
            storage_reference=f"quarantine/artifacts/{run.id}/denied",
            size_bytes=512,
            classification_level="sensitive_personal_information",
            output_policy_evaluation=evaluation,
            output_policy_evaluation_digest=canonical_document_digest(evaluation),
            release_status="quarantined",
        )
        session.add(denied)
        await session.flush()
        review = await create_artifact_review(
            session,
            artifact=denied,
            responsible_organization_id=UUID(evaluation["provider_organization_id"]),
            routing_rule_digest=f"sha256:{'5' * 64}",
        )
        await claim_artifact_review(session, review, user_id=user.id)
        with pytest.raises(ComputeInvariantError, match="Policy deny"):
            await decide_artifact_review(
                session,
                review,
                decision="approved",
                reason_code="manual_approval_attempt",
                evidence={"schema_version": "artifact-review-evidence/v1"},
            )
    await engine.dispose()
