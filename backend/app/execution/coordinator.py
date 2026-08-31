from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.adapter import ExecutorAdapter
from app.execution.errors import (
    ExecutionCoordinatorError,
    ExecutionSubmissionConflict,
    ExecutionSubmissionUnknown,
)
from app.execution.receipt import ExecutionSubmissionReceipt
from app.execution.request import ExecutionRequest
from app.messaging import OutboxEnvelope, PublishResult
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    canonical_json_digest_v1,
    digest_idempotency_key,
)
from app.modules.compute.models import ComputeJob, ComputeRun
from app.modules.compute.services import (
    ComputeInvariantError,
    evaluate_compute_authorization,
)
from app.modules.connectors.models import Connector
from app.modules.contracts.models import PolicyConstraint
from app.modules.inbox import (
    ConsumerInboxEntry,
    InboxInvariantError,
    InboxReceiverPublisher,
    claim_inbox_batch,
    complete_inbox,
    dead_letter_inbox,
    reclaim_expired_inbox,
    release_inbox_for_retry,
)

CONSUMER_NAME = "execution-coordinator"


@dataclass(frozen=True)
class CoordinatorProcessResult:
    entry_id: UUID
    run_id: UUID
    outcome_code: str


class ExecutionCoordinatorConsumer:
    """Durably accepts compute.dispatch delivery before returning an ACK."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._receiver = InboxReceiverPublisher(
            session_maker=session_maker, consumer_name=CONSUMER_NAME
        )

    async def publish(self, message: OutboxEnvelope) -> PublishResult:
        return await self._receiver.publish(message)


class ExecutionCoordinatorService:
    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        executor: ExecutorAdapter,
    ) -> None:
        self._session_maker = session_maker
        self._executor = executor

    async def _assert_audit_chain(self, session: AsyncSession, space_id: UUID) -> None:
        if session.bind is None or session.bind.dialect.name != "postgresql":
            return
        row = (
            await session.execute(
                text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
                {"space_id": space_id},
            )
        ).mappings().one()
        if not row["is_valid"]:
            raise ExecutionCoordinatorError(
                f"audit chain is invalid at sequence {row['invalid_sequence']}"
            )

    async def _build_request(
        self, session: AsyncSession, entry_id: UUID
    ) -> tuple[ExecutionRequest, UUID]:
        entry = await session.get(ConsumerInboxEntry, entry_id)
        if entry is None or entry.status != "processing":
            raise InboxInvariantError("Inbox entry is not processing")
        event = await session.get(AuditEvent, entry.event_id)
        if (
            event is None
            or event.event_type != "compute.run.reserved"
            or event.subject_type != "compute_run"
            or event.space_id != entry.space_id
        ):
            raise ExecutionCoordinatorError("Inbox source event is invalid")
        run = await session.get(ComputeRun, event.subject_id)
        if run is None or run.status not in {"reserved", "dispatched"}:
            raise ExecutionCoordinatorError("ComputeRun is not dispatchable")
        job = await session.get(ComputeJob, run.compute_job_id)
        if job is None or job.status != "ready":
            raise ExecutionCoordinatorError("ComputeJob is not ready")
        await self._assert_audit_chain(session, entry.space_id)
        algorithm_digest = job.algorithm_spec_snapshot.get("algorithm_digest")
        if not isinstance(algorithm_digest, str):
            raise ExecutionCoordinatorError("Job algorithm registry digest is missing")
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
            run.reservation_ordinal is None
            or run.run_limit_snapshot != context.run_limit
            or run.reservation_ordinal > context.run_limit
            or run.compute_binding_id != context.compute_binding.id
        ):
            raise ExecutionCoordinatorError("Run reservation is no longer valid")
        connector = await session.get(Connector, context.compute_binding.connector_id)
        constraint = await session.get(PolicyConstraint, run.run_count_constraint_id)
        if connector is None or constraint is None or context.quota_policy.policy_digest is None:
            raise ExecutionCoordinatorError("execution Policy evidence is incomplete")
        constraint_digest = canonical_json_digest_v1(
            {
                "schema_version": "execution-constraint/v1",
                "constraint_id": str(constraint.id),
                "constraint_name": constraint.constraint_name,
                "operator": constraint.operator,
                "value": constraint.value,
                "unit": constraint.unit,
            }
        )
        request = ExecutionRequest.build(
            run_id=run.id,
            job_id=job.id,
            space_id=job.space_id,
            contract_revision_id=job.contract_revision_id,
            contract_object_id=job.contract_object_id,
            policy_digest=context.quota_policy.policy_digest,
            constraint_digest=constraint_digest,
            binding_id=context.compute_binding.id,
            connector_id=connector.id,
            algorithm_spec_snapshot=dict(job.algorithm_spec_snapshot),
            algorithm_digest=algorithm_digest,
            compute_input_snapshot=dict(job.compute_input_snapshot),
            input_digest=job.compute_input_digest,
            execution_environment_snapshot=dict(run.execution_environment_snapshot or {}),
            resource_limits={"cpu_cores": 1, "memory_mb": 512, "timeout_seconds": 300},
            callback_correlation_id=event.correlation_id,
        )
        return request, event.event_id

    async def _submit_or_recover(
        self, request: ExecutionRequest
    ) -> ExecutionSubmissionReceipt:
        existing = await self._executor.get_by_idempotency_key(
            request.submission_idempotency_key
        )
        if existing is not None:
            if existing.request_digest != request.request_digest:
                raise ExecutionSubmissionConflict(
                    "Executor submission receipt has another request digest"
                )
            return existing
        return await self._executor.submit(request)

    async def process_entry(
        self, *, entry_id: UUID, worker_id: str
    ) -> CoordinatorProcessResult:
        try:
            async with self._session_maker() as session:
                request, source_event_id = await self._build_request(session, entry_id)
            receipt = await self._submit_or_recover(request)
        except ExecutionSubmissionUnknown as exc:
            async with self._session_maker() as session:
                async with session.begin():
                    await release_inbox_for_retry(
                        session,
                        entry_id=entry_id,
                        worker_id=worker_id,
                        error=str(exc),
                    )
            raise
        except (ComputeInvariantError, ExecutionCoordinatorError) as exc:
            async with self._session_maker() as session:
                async with session.begin():
                    entry = await session.get(ConsumerInboxEntry, entry_id)
                    if entry is not None and entry.status == "processing":
                        await complete_inbox(
                            session,
                            entry_id=entry_id,
                            worker_id=worker_id,
                            outcome_code="authorization_revoked",
                        )
            raise

        if not receipt.accepted:
            async with self._session_maker() as session:
                async with session.begin():
                    if receipt.retryable:
                        await release_inbox_for_retry(
                            session,
                            entry_id=entry_id,
                            worker_id=worker_id,
                            error=receipt.error_code or "executor_retryable_rejection",
                        )
                    else:
                        await complete_inbox(
                            session,
                            entry_id=entry_id,
                            worker_id=worker_id,
                            outcome_code="non_retryable_rejection",
                        )
            return CoordinatorProcessResult(
                entry_id=entry_id,
                run_id=request.run_id,
                outcome_code="non_retryable_rejection" if not receipt.retryable else "retry_scheduled",
            )

        if receipt.external_execution_id is None or receipt.accepted_at is None:
            raise ExecutionCoordinatorError("accepted Executor receipt is incomplete")

        async with self._session_maker() as session:
            async with session.begin():
                entry = await session.scalar(
                    select(ConsumerInboxEntry)
                    .where(ConsumerInboxEntry.id == entry_id)
                    .with_for_update()
                )
                run = await session.scalar(
                    select(ComputeRun)
                    .where(ComputeRun.id == request.run_id)
                    .with_for_update()
                )
                if entry is None or run is None:
                    raise ExecutionCoordinatorError("Coordinator write-back scope is missing")
                if receipt.request_digest != request.request_digest:
                    raise ExecutionSubmissionConflict("Executor receipt request digest mismatch")
                if run.status == "dispatched":
                    if (
                        run.execution_reference != receipt.external_execution_id
                        or run.dispatch_receipt_digest != receipt.receipt_digest
                    ):
                        raise ExecutionSubmissionConflict("Run has another dispatch receipt")
                    await complete_inbox(
                        session,
                        entry_id=entry.id,
                        worker_id=worker_id,
                        outcome_code="already_dispatched",
                        outcome_reference_type="compute_run",
                        outcome_reference_id=run.id,
                    )
                    return CoordinatorProcessResult(entry.id, run.id, "already_dispatched")
                if run.status != "reserved":
                    raise ExecutionCoordinatorError("Run left reserved before write-back")

                job = await session.get(ComputeJob, run.compute_job_id)
                if job is None:
                    raise ExecutionCoordinatorError("Run Job is missing")
                # Re-evaluate after external acceptance to cover revocation during submission.
                algorithm_digest = job.algorithm_spec_snapshot.get("algorithm_digest")
                if not isinstance(algorithm_digest, str):
                    raise ExecutionCoordinatorError("Job algorithm registry digest is missing")
                await evaluate_compute_authorization(
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
                command_id = uuid5(NAMESPACE_URL, f"medtrust:dispatch:{run.id}")
                command = AuditCommandContext(
                    command_id=command_id,
                    idempotency_key=digest_idempotency_key(
                        f"dispatch:{run.id}:{request.request_digest}"
                    ),
                    correlation_id=request.callback_correlation_id,
                    causation_id=source_event_id,
                    actor_type="system",
                    actor_service_code="medtrust.compute",
                )
                await append_audit_event_with_outbox(
                    session,
                    space_id=run.space_id,
                    event_type="compute.run.dispatched",
                    subject_type="compute_run",
                    subject_id=run.id,
                    result="success",
                    evidence_snapshot={
                        "schema_version": "compute-run-dispatched-evidence/v1",
                        "compute_run_id": str(run.id),
                        "compute_job_id": str(run.compute_job_id),
                        "contract_revision_id": str(run.contract_revision_id),
                        "request_digest": request.request_digest,
                        "dispatch_receipt_digest": receipt.receipt_digest,
                        "submission_idempotency_digest": request.submission_idempotency_key,
                        "external_execution_reference_digest": canonical_json_digest_v1(
                            {"external_execution_id": receipt.external_execution_id}
                        ),
                    },
                    **command.append_kwargs(),
                )
                run._transition_validated = True
                run.status = "dispatched"
                run.execution_reference = receipt.external_execution_id
                run.dispatch_receipt_digest = receipt.receipt_digest
                run.dispatched_at = datetime.now(timezone.utc)
                run.row_version += 1
                await complete_inbox(
                    session,
                    entry_id=entry.id,
                    worker_id=worker_id,
                    outcome_code="executor_submitted",
                    outcome_reference_type="compute_run",
                    outcome_reference_id=run.id,
                )
        return CoordinatorProcessResult(entry_id, request.run_id, "executor_submitted")

    async def claim_and_process_once(
        self,
        *,
        worker_id: str,
        batch_size: int = 10,
        lease_seconds: int = 60,
    ) -> int:
        async with self._session_maker() as session:
            async with session.begin():
                reclaimed = await reclaim_expired_inbox(
                    session,
                    consumer_name=CONSUMER_NAME,
                    worker_id=worker_id,
                    batch_size=batch_size,
                    lease_seconds=lease_seconds,
                )
                remaining = batch_size - len(reclaimed)
                fresh = (
                    await claim_inbox_batch(
                        session,
                        consumer_name=CONSUMER_NAME,
                        worker_id=worker_id,
                        batch_size=remaining,
                        lease_seconds=lease_seconds,
                    )
                    if remaining
                    else []
                )
                entry_ids = [entry.id for entry in [*reclaimed, *fresh]]
        for entry_id in entry_ids:
            try:
                await self.process_entry(entry_id=entry_id, worker_id=worker_id)
            except (ExecutionCoordinatorError, ComputeInvariantError, InboxInvariantError):
                continue
        return len(entry_ids)
