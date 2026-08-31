from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor, command_for
from app.modules.applications.models import Application
from app.modules.audit.models import AuditEvent
from app.modules.audit.services import (
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
)
from app.modules.callback_inbox.models import ExecutionCallbackInboxEntry
from app.modules.catalog.models import DataProduct, DataProductPublication, DataProductVersion
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.contracts.models import Contract
from app.modules.dataset_model_evidence.models import (
    DatasetModelEvidence,
    DatasetModelRelation,
)
from app.modules.marketplace.models import (
    ApprovedResultPackage,
    ArtifactReviewDecision,
    ArtifactReviewTask,
    ModelProduct,
    ModelPublication,
    ModelVersion,
    ResultDownloadGrant,
)


class ReferenceEvidenceBackfillError(ValueError):
    pass


ALLOWED_PACKAGE_FILES = {
    "aggregate_metrics.json",
    "confusion_matrix.csv",
    "execution_summary.json",
}
REQUIRED_RUN_EVENTS = {
    "compute.run.reserved",
    "compute.run.dispatched",
    "compute.run.started",
    "compute.run.completed",
}
REQUIRED_RELEASE_EVENTS = {
    "artifact.created",
    "artifact.review.plan.created",
    "result.package.created",
    "result.download.grant.created",
    "result.download.completed",
    "result.download.rejected",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReferenceEvidenceBackfillError(message)


async def _audit(
    session: AsyncSession,
    *,
    actor: DemoActor,
    relation: DatasetModelRelation,
    event_type: str,
    evidence: DatasetModelEvidence,
    old_status: str,
    new_status: str,
    run: ComputeRun,
    artifact: Artifact,
    package: ApprovedResultPackage,
) -> None:
    action = f"{event_type}:{evidence.id}"
    command = command_for(actor, action, action)
    await append_audit_event_with_outbox(
        session,
        space_id=relation.space_id,
        event_type=event_type,
        subject_type="dataset_model_relation",
        subject_id=relation.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase5.12.6br/reference-evidence-backfill/v1",
            "backfill": True,
            "new_execution": False,
            "compute_run_id": str(run.id),
            "data_product_version_id": str(relation.data_product_version_id),
            "model_product_version_id": str(relation.model_product_version_id),
            "artifact_id": str(artifact.id),
            "result_package_id": str(package.id),
            "evidence_id": str(evidence.id),
            "evidence_digest": evidence.source_record_digest,
            "old_status": old_status,
            "new_status": new_status,
            "backfilled_at": datetime.now(timezone.utc).isoformat(),
        },
        **command.append_kwargs(),
    )


async def _public_eligible(
    session: AsyncSession,
    data_product: DataProduct,
    data_version: DataProductVersion,
    model_product: ModelProduct,
    model_version: ModelVersion,
) -> bool:
    data_publication = await session.scalar(
        select(DataProductPublication.id).where(
            DataProductPublication.data_product_version_id == data_version.id,
            DataProductPublication.status == "active",
        )
    )
    model_publication = await session.scalar(
        select(ModelPublication.id).where(
            ModelPublication.model_version_id == model_version.id,
            ModelPublication.status == "active",
        )
    )
    return bool(
        data_product.lifecycle_status == "active"
        and data_version.status == "approved"
        and model_product.lifecycle_status == "active"
        and model_version.status == "approved"
        and data_publication
        and model_publication
    )


async def backfill_reference_run_evidence(
    session: AsyncSession,
    *,
    actor: DemoActor,
    run_id: UUID,
) -> tuple[DatasetModelRelation, DatasetModelEvidence, DatasetModelEvidence, bool]:
    if actor.role != "space_operator":
        raise ReferenceEvidenceBackfillError("only the platform operator may backfill evidence")

    run = await session.get(ComputeRun, run_id)
    _require(run is not None and run.space_id is not None, "compute run not found")
    _require(run.status == "succeeded" and run.finished_at is not None, "run is not successful and terminal")
    _require(
        bool(run.execution_reference)
        and run.execution_reference.startswith("local-builtin:")
        and "fake" not in run.execution_reference.lower()
        and "simulat" not in run.execution_reference.lower(),
        "run does not identify the approved real executor adapter",
    )
    _require(bool(run.execution_environment_digest), "execution environment digest is missing")

    job = await session.get(ComputeJob, run.compute_job_id)
    _require(job is not None and job.status == "succeeded", "compute job is not successful")
    data_version_id = UUID(str(job.compute_input_snapshot.get("data_product_version_id")))
    model_version_id = UUID(str(job.algorithm_spec_snapshot.get("model_version_id")))
    data_version = await session.get(DataProductVersion, data_version_id)
    model_version = await session.get(ModelVersion, model_version_id)
    _require(data_version is not None and model_version is not None, "locked product version is missing")
    data_product = await session.get(DataProduct, data_version.data_product_id)
    model_product = await session.get(ModelProduct, model_version.model_product_id)
    _require(data_product is not None and model_product is not None, "locked product is missing")
    _require(
        job.compute_input_snapshot.get("product_snapshot_digest") == data_version.snapshot_digest,
        "data version digest does not match the historical job",
    )
    _require(
        job.algorithm_spec_snapshot.get("model_snapshot_digest") == model_version.snapshot_digest,
        "model version digest does not match the historical job",
    )

    contract = await session.get(Contract, job.contract_id)
    application = await session.get(Application, contract.application_id) if contract else None
    _require(contract is not None and application is not None, "application or contract is missing")
    _require(application.status == "approved", "historical application is not approved")

    callback = await session.scalar(
        select(ExecutionCallbackInboxEntry).where(
            ExecutionCallbackInboxEntry.compute_run_id == run.id,
            ExecutionCallbackInboxEntry.callback_type == "execution.completed",
            ExecutionCallbackInboxEntry.status == "completed",
        )
    )
    _require(callback is not None, "completed executor callback is missing")
    summary = callback.payload_snapshot.get("execution_summary") or {}
    _require(summary and callback.payload_snapshot.get("output_digest"), "run output summary is missing")
    _require(summary.get("dataset_digest_unchanged") is True, "dataset digest was not preserved")
    _require(summary.get("model_digest_verified") is True, "model digest was not verified")

    artifact = await session.scalar(select(Artifact).where(Artifact.compute_run_id == run.id))
    _require(artifact is not None, "result artifact is missing")
    _require(artifact.release_status == "quarantined", "artifact isolation state changed")
    _require(
        artifact.content_digest == callback.payload_snapshot.get("output_digest"),
        "artifact digest conflicts with executor output",
    )
    tasks = list(
        (
            await session.scalars(
                select(ArtifactReviewTask).where(
                    ArtifactReviewTask.artifact_id == artifact.id,
                    ArtifactReviewTask.is_required.is_(True),
                )
            )
        ).all()
    )
    decisions = list(
        (
            await session.scalars(
                select(ArtifactReviewDecision).where(
                    ArtifactReviewDecision.artifact_review_task_id.in_(
                        [task.id for task in tasks]
                    )
                )
            )
        ).all()
    )
    _require(len(tasks) == 3 and len(decisions) == 3, "three required reviews are not complete")
    _require(all(item.decision == "approved" for item in decisions), "a required review is not approved")

    package = await session.scalar(
        select(ApprovedResultPackage).where(
            ApprovedResultPackage.artifact_id == artifact.id,
            ApprovedResultPackage.status == "available",
        )
    )
    _require(package is not None, "available release package is missing")
    _require(
        len(package.package_digest) == 71
        and package.package_digest.startswith("sha256:"),
        "package digest format is invalid",
    )
    files = package.manifest_snapshot.get("files") or []
    _require({item.get("name") for item in files} == ALLOWED_PACKAGE_FILES, "package file allowlist is invalid")
    _require(len(files) == 3 and all(item.get("digest") and item.get("size_bytes", 0) > 0 for item in files), "package manifest is incomplete")
    _require(
        not any(
            package.manifest_snapshot.get(key)
            for key in (
                "contains_raw_data",
                "contains_raw_features",
                "contains_model_weights",
                "contains_patient_level_results",
            )
        ),
        "package contains prohibited content",
    )
    grant = await session.scalar(
        select(ResultDownloadGrant).where(
            ResultDownloadGrant.result_package_id == package.id,
            ResultDownloadGrant.max_downloads == 1,
            ResultDownloadGrant.download_count == 1,
            ResultDownloadGrant.status == "exhausted",
        )
    )
    _require(grant is not None, "one-time download evidence is incomplete")

    chain = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": run.space_id},
        )
    ).mappings().one()
    _require(bool(chain["is_valid"]), "audit chain is invalid")
    audit_tip = (
        await session.execute(
            select(AuditEvent.stream_sequence, AuditEvent.event_digest)
            .where(AuditEvent.space_id == run.space_id)
            .order_by(AuditEvent.stream_sequence.desc())
            .limit(1)
        )
    ).one()
    run_events = set(
        (
            await session.scalars(
                select(AuditEvent.event_type).where(AuditEvent.subject_id == run.id)
            )
        ).all()
    )
    _require(REQUIRED_RUN_EVENTS <= run_events, "run audit evidence is incomplete")
    release_subject_ids = (artifact.id, package.id, grant.id)
    release_events = set(
        (
            await session.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.subject_id.in_(release_subject_ids)
                )
            )
        ).all()
    )
    _require(REQUIRED_RELEASE_EVENTS <= release_events, "release audit evidence is incomplete")

    relation = await session.scalar(
        select(DatasetModelRelation).where(
            DatasetModelRelation.data_product_version_id == data_version.id,
            DatasetModelRelation.model_product_version_id == model_version.id,
        )
    )
    created = relation is None
    if relation is None:
        relation = DatasetModelRelation(
            id=uuid5(NAMESPACE_URL, f"medtrust:dataset-model:{data_version.id}:{model_version.id}"),
            space_id=run.space_id,
            data_product_id=data_product.id,
            data_product_version_id=data_version.id,
            model_product_id=model_product.id,
            model_product_version_id=model_version.id,
            current_status="not_assessed",
            strongest_evidence_level="none",
            data_source_link_id=None,
            model_source_link_id=None,
            data_version_digest=data_version.snapshot_digest,
            model_version_digest=model_version.snapshot_digest,
            data_source_digest=None,
            model_source_digest=None,
            data_governance_digest=None,
            model_governance_digest=None,
            public_visible=False,
        )
        session.add(relation)
        await session.flush()

    _require(
        relation.data_version_digest == data_version.snapshot_digest
        and relation.model_version_digest == model_version.snapshot_digest,
        "relation version lock conflicts with historical run",
    )
    executed_key = canonical_json_digest_v1(
        {"kind": "reference-run-executed", "run_id": str(run.id)}
    )
    verified_key = canonical_json_digest_v1(
        {"kind": "reference-run-verified", "run_id": str(run.id), "package_id": str(package.id)}
    )
    executed = await session.scalar(
        select(DatasetModelEvidence).where(
            DatasetModelEvidence.idempotency_digest == executed_key
        )
    )
    verified = await session.scalar(
        select(DatasetModelEvidence).where(
            DatasetModelEvidence.idempotency_digest == verified_key
        )
    )
    if executed is not None and verified is not None:
        return relation, executed, verified, False

    assessment = {
        "sample_count": summary.get("sample_count"),
        "success_count": summary.get("processed_count"),
        "failure_count": summary.get("failed_count"),
        "correct_count": summary.get("correct_predictions"),
        "aggregate_metrics": {
            "accuracy": summary.get("accuracy"),
            "mean_confidence": summary.get("mean_confidence"),
        },
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat(),
        "duration_seconds": (
            str((run.finished_at - run.started_at).total_seconds())
            if run.started_at else None
        ),
        "executor_adapter": run.execution_reference.split(":", 1)[0],
        "resource_usage": summary.get("resource_usage", {}),
        "software_environment_digest": run.execution_environment_digest,
        "result_artifact_id": str(artifact.id),
        "limitations": [
            "Fixed 20-image PathMNIST demonstration subset only.",
            "Engineering workflow verification; non-clinical.",
            "Does not represent full-dataset or diagnostic performance.",
            "hard_isolation=false prototype execution boundary.",
        ],
    }
    reference = {
        "application_id": str(application.id),
        "contract_id": str(contract.id),
        "compute_job_id": str(job.id),
        "compute_run_id": str(run.id),
        "artifact_id": str(artifact.id),
        "completion_callback_id": str(callback.id),
        "audit_chain_last_sequence": audit_tip.stream_sequence,
        "audit_chain_last_digest": audit_tip.event_digest,
    }
    if executed is None:
        old_status = relation.current_status
        source_digest = canonical_json_digest_v1(
            {"reference": reference, "assessment": assessment}
        )
        executed = DatasetModelEvidence(
            id=uuid5(NAMESPACE_URL, f"medtrust:reference-executed:{run.id}"),
            relation_id=relation.id,
            evidence_level="runtime_execution",
            evidence_type="executed",
            outcome="supports",
            evidence_scope="runtime",
            evidence_reference=reference,
            evidence_note=(
                "Historical fixed-version PathMNIST and ResNet-18 run completed "
                "through the controlled CPU executor. This is non-clinical evidence."
            ),
            structured_assessment=assessment,
            transformation_requirements=[],
            blocking_reasons=[],
            warning_reasons=["historical_fixed_version_scope", "non_clinical_demo"],
            data_product_version_id=data_version.id,
            model_product_version_id=model_version.id,
            data_version_digest=data_version.snapshot_digest,
            model_version_digest=model_version.snapshot_digest,
            data_source_digest=None,
            model_source_digest=None,
            data_governance_digest=None,
            model_governance_digest=None,
            reviewer_user_id=actor.user_id,
            reviewer_organization_id=actor.organization_id,
            source_record_digest=source_digest,
            idempotency_digest=executed_key,
            supersedes_evidence_id=None,
        )
        session.add(executed)
        await session.flush()
        relation.current_status = "executed"
        relation.strongest_evidence_level = "runtime_execution"
        relation.current_evidence_id = executed.id
        await _audit(
            session, actor=actor, relation=relation,
            event_type="dataset_model_evidence.execution_backfilled",
            evidence=executed, old_status=old_status, new_status="executed",
            run=run, artifact=artifact, package=package,
        )

    if verified is None:
        old_status = relation.current_status
        verification = {
            "executed_evidence_id": str(executed.id),
            "artifact_id": str(artifact.id),
            "artifact_status": artifact.release_status,
            "review_ids": [str(item.id) for item in decisions],
            "review_decision_digests": [item.decision_digest for item in decisions],
            "review_status": "3/3 approved",
            "result_package_id": str(package.id),
            "package_digest": package.package_digest,
            "package_files": [
                {"name": item["name"], "digest": item["digest"], "size_bytes": item["size_bytes"]}
                for item in files
            ],
            "download_grant_id": str(grant.id),
            "one_time_download": {"status": grant.status, "usage": "1/1"},
            "audit_chain_valid": True,
            "audit_chain_last_sequence": audit_tip.stream_sequence,
            "audit_chain_last_digest": audit_tip.event_digest,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        verified = DatasetModelEvidence(
            id=uuid5(NAMESPACE_URL, f"medtrust:reference-verified:{run.id}:{package.id}"),
            relation_id=relation.id,
            evidence_level="platform_verification",
            evidence_type="verified",
            outcome="supports",
            evidence_scope="verification",
            evidence_reference=reference | {
                "result_package_id": str(package.id),
                "download_grant_id": str(grant.id),
            },
            evidence_note=(
                "Platform verification covers this historical fixed-version run, "
                "its quarantined artifact, three approvals, safe package, and one-time download."
            ),
            structured_assessment=assessment | {"platform_verification": verification},
            transformation_requirements=[],
            blocking_reasons=[],
            warning_reasons=["historical_fixed_version_scope", "non_clinical_demo"],
            data_product_version_id=data_version.id,
            model_product_version_id=model_version.id,
            data_version_digest=data_version.snapshot_digest,
            model_version_digest=model_version.snapshot_digest,
            data_source_digest=None,
            model_source_digest=None,
            data_governance_digest=None,
            model_governance_digest=None,
            reviewer_user_id=actor.user_id,
            reviewer_organization_id=actor.organization_id,
            source_record_digest=canonical_json_digest_v1(verification),
            idempotency_digest=verified_key,
            supersedes_evidence_id=executed.id,
        )
        session.add(verified)
        await session.flush()
        relation.current_status = "verified"
        relation.strongest_evidence_level = "platform_verification"
        relation.current_evidence_id = verified.id
        relation.public_visible = await _public_eligible(
            session, data_product, data_version, model_product, model_version
        )
        relation.updated_at = datetime.now(timezone.utc)
        await _audit(
            session, actor=actor, relation=relation,
            event_type="dataset_model_evidence.verification_backfilled",
            evidence=verified, old_status=old_status, new_status="verified",
            run=run, artifact=artifact, package=package,
        )
        await _audit(
            session, actor=actor, relation=relation,
            event_type="dataset_model_relation.status_changed",
            evidence=verified, old_status=old_status, new_status="verified",
            run=run, artifact=artifact, package=package,
        )
    return relation, executed, verified, created
