from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.callback_inbox import (
    CallbackInboxInvariantError,
    ExecutionCallbackInboxEntry,
    complete_callback,
    dead_letter_callback,
    retry_callback,
)
from app.modules.compute import create_artifact, evaluate_compute_authorization
from app.modules.compute.models import ComputeJob, ComputeRun
from app.execution.quarantine import MinioQuarantineArtifactWriter


class ExecutionCallbackProcessingError(ValueError):
    """Raised when an Executor callback cannot be applied safely."""


@dataclass(frozen=True)
class CallbackProcessResult:
    entry_id: UUID
    run_id: UUID
    outcome_code: str
    artifact_id: UUID | None = None


_EVENTS = {
    "execution.started": ("compute.run.started", "success", "run_started"),
    "execution.completed": ("compute.run.completed", "success", "run_completed"),
    "execution.failed": ("compute.run.failed", "failure", "run_failed"),
    "execution.interrupted": (
        "compute.run.interrupted",
        "interrupted",
        "run_interrupted",
    ),
}


def _command(
    entry: ExecutionCallbackInboxEntry, event_type: str, *, causation_id: UUID
) -> AuditCommandContext:
    command_id = uuid5(NAMESPACE_URL, f"medtrust:{entry.id}:{event_type}")
    return AuditCommandContext(
        command_id=command_id,
        idempotency_key=digest_idempotency_key(
            f"execution-callback:{entry.id}:{event_type}:{entry.normalized_fact_digest}"
        ),
        correlation_id=entry.correlation_id,
        causation_id=causation_id,
        actor_type="system",
        actor_service_code="medtrust.compute",
    )


def _evidence(entry: ExecutionCallbackInboxEntry, run: ComputeRun) -> dict[str, Any]:
    return {
        "schema_version": "execution-callback-transition-evidence/v1",
        "callback_entry_id": str(entry.id),
        "callback_type": entry.callback_type,
        "callback_fact_digest": entry.normalized_fact_digest,
        "execution_evidence_digest": entry.execution_evidence_digest,
        "authentication_evidence_digest": entry.authentication_evidence_digest,
        "compute_run_id": str(run.id),
        "compute_job_id": str(run.compute_job_id),
        "contract_revision_id": str(run.contract_revision_id),
        "external_execution_reference_digest": canonical_json_digest_v1(
            {"external_execution_id": entry.external_execution_id}
        ),
    }


async def _revalidate_current_authorization(
    session: AsyncSession, run: ComputeRun, job: ComputeJob
) -> None:
    algorithm_digest = job.algorithm_spec_snapshot.get("algorithm_digest")
    if not isinstance(algorithm_digest, str):
        raise ExecutionCallbackProcessingError("Job algorithm digest is unavailable")
    context = await evaluate_compute_authorization(
        session,
        revision_id=job.contract_revision_id,
        party_id=job.requester_contract_party_id,
        contract_object_id=job.contract_object_id,
        requester_organization_id=job.requester_organization_id,
        requester_user_id=job.requester_user_id,
        purpose_code=job.purpose_code,
        algorithm_digest=algorithm_digest,
        requested_output_types=list(job.requested_output_types),
        exclude_run_id=run.id,
        exclude_job_id=job.id,
    )
    if (
        run.compute_binding_id != context.compute_binding.id
        or run.egress_binding_id != context.egress_binding.id
        or run.audit_binding_id != context.audit_binding.id
        or run.reservation_ordinal is None
        or run.reservation_ordinal > context.run_limit
    ):
        raise ExecutionCallbackProcessingError("Run authorization is no longer current")


def _set_job_status(job: ComputeJob, status: str, when: datetime) -> None:
    if job.status == status:
        return
    job._transition_validated = True
    job.status = status
    if status == "running":
        job.started_at = job.started_at or when
    elif status in {"succeeded", "failed", "interrupted"}:
        job.finished_at = when
        if status == "failed":
            job.failure_code = "executor_callback"
        if status == "interrupted":
            job.interruption_code = "executor_callback"
    job.row_version += 1


async def process_execution_callback(
    session: AsyncSession,
    *,
    entry_id: UUID,
    worker_id: str,
    artifact_writer: MinioQuarantineArtifactWriter | None = None,
) -> CallbackProcessResult:
    """Apply one leased callback, its AuditEvent and Outbox in one transaction."""

    entry = await session.scalar(
        select(ExecutionCallbackInboxEntry)
        .where(ExecutionCallbackInboxEntry.id == entry_id)
        .with_for_update()
    )
    if entry is None or entry.status != "processing" or entry.lock_owner != worker_id:
        raise CallbackInboxInvariantError("Callback Inbox lease is unavailable")
    run = await session.scalar(
        select(ComputeRun).where(ComputeRun.id == entry.compute_run_id).with_for_update()
    )
    if run is None or run.space_id != entry.space_id:
        raise ExecutionCallbackProcessingError("Callback Run scope is invalid")
    if run.execution_reference != entry.external_execution_id:
        await dead_letter_callback(
            session,
            entry_id=entry.id,
            worker_id=worker_id,
            error="external_execution_id_conflict",
            expected_row_version=entry.row_version,
        )
        return CallbackProcessResult(entry.id, run.id, "non_retryable_rejection")
    job = await session.scalar(
        select(ComputeJob).where(ComputeJob.id == run.compute_job_id).with_for_update()
    )
    if job is None:
        raise ExecutionCallbackProcessingError("Callback Job is unavailable")

    expected_source = {
        "execution.started": {"dispatched"},
        "execution.completed": {"running"},
        "execution.failed": {"dispatched", "running"},
        "execution.interrupted": {"dispatched", "running"},
    }[entry.callback_type]
    if run.status in {"succeeded", "failed", "interrupted", "cancelled", "timed_out"}:
        await dead_letter_callback(
            session,
            entry_id=entry.id,
            worker_id=worker_id,
            error="terminal_run_conflict",
            expected_row_version=entry.row_version,
        )
        return CallbackProcessResult(entry.id, run.id, "non_retryable_rejection")
    if run.status not in expected_source:
        await retry_callback(
            session,
            entry_id=entry.id,
            worker_id=worker_id,
            error="callback_out_of_order",
            expected_row_version=entry.row_version,
        )
        return CallbackProcessResult(entry.id, run.id, "callback_out_of_order")

    await _revalidate_current_authorization(session, run, job)
    event_type, result, outcome = _EVENTS[entry.callback_type]
    when = entry.occurred_at.astimezone(timezone.utc)
    cause_event = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.space_id == run.space_id,
            AuditEvent.subject_type == "compute_run",
            AuditEvent.subject_id == run.id,
        )
        .order_by(AuditEvent.stream_sequence.desc())
        .limit(1)
    )
    if cause_event is None:
        raise ExecutionCallbackProcessingError("Run Audit causation evidence is unavailable")
    command = _command(entry, event_type, causation_id=cause_event.event_id)
    audit_result = await append_audit_event_with_outbox(
        session,
        space_id=run.space_id,
        event_type=event_type,
        subject_type="compute_run",
        subject_id=run.id,
        result=result,
        evidence_snapshot=_evidence(entry, run),
        **command.append_kwargs(),
    )

    run._transition_validated = True
    artifact_id: UUID | None = None
    if entry.callback_type == "execution.started":
        run.status = "running"
        run.start_receipt_digest = entry.execution_evidence_digest
        run.started_at = when
        _set_job_status(job, "running", when)
    elif entry.callback_type == "execution.completed":
        run.status = "succeeded"
        run.completion_receipt_digest = entry.execution_evidence_digest
        run.finished_at = when
        _set_job_status(job, "succeeded", when)
    elif entry.callback_type == "execution.failed":
        run.status = "failed"
        run.failure_code = str(entry.payload_snapshot.get("error_code") or "executor_failed")[:64]
        run.finished_at = when
        if job.status == "running":
            _set_job_status(job, "failed", when)
    else:
        run.status = "interrupted"
        run.interruption_code = str(
            entry.payload_snapshot.get("error_code") or "executor_interrupted"
        )[:64]
        run.finished_at = when
        if job.status == "running":
            _set_job_status(job, "interrupted", when)
    run.audit_receipt_digest = audit_result.event.event_digest
    run.row_version += 1
    await session.flush()

    if entry.callback_type == "execution.completed":
        payload = entry.payload_snapshot
        manifest = payload.get("output_manifest")
        size_bytes = 0
        if isinstance(manifest, list):
            size_bytes = sum(
                int(item.get("size_bytes", 0))
                for item in manifest
                if isinstance(item, dict) and isinstance(item.get("size_bytes", 0), int)
            )
        artifact_command = _command(
            entry, "artifact.created", causation_id=audit_result.event.event_id
        )
        storage_reference = str(payload.get("object_storage_ref"))
        if storage_reference.startswith("workspace-output:"):
            if artifact_writer is None or not isinstance(manifest, list):
                raise ExecutionCallbackProcessingError(
                    "quarantine Artifact writer is unavailable"
                )
            storage_reference = artifact_writer.upload(
                run_id=run.id,
                workspace_reference=storage_reference,
                manifest=manifest,
                manifest_digest=str(payload.get("output_digest")),
            )
        artifact = await create_artifact(
            session,
            run_id=run.id,
            artifact_type=str(payload.get("artifact_type")),
            content_digest=str(payload.get("output_digest")),
            storage_reference=storage_reference,
            size_bytes=size_bytes,
            classification_level="sensitive",
            audit_command=artifact_command,
        )
        artifact_id = artifact.id

    await complete_callback(
        session,
        entry_id=entry.id,
        worker_id=worker_id,
        outcome_code=outcome,
        outcome_reference_type="artifact" if artifact_id else "compute_run",
        outcome_reference_id=artifact_id or run.id,
        expected_row_version=entry.row_version,
    )
    return CallbackProcessResult(entry.id, run.id, outcome, artifact_id)


class ExecutionCallbackWorker:
    """One-shot worker; scheduling remains an explicit later concern."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        *,
        artifact_writer: MinioQuarantineArtifactWriter | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._artifact_writer = artifact_writer

    async def process_one(self, *, entry_id: UUID, worker_id: str) -> CallbackProcessResult:
        async with self._session_maker() as session:
            async with session.begin():
                return await process_execution_callback(
                    session,
                    entry_id=entry_id,
                    worker_id=worker_id,
                    artifact_writer=self._artifact_writer,
                )
