from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.execution.callback import ExecutionCallbackEnvelope
from app.modules.audit import canonical_json_digest_v1
from app.execution.errors import ExecutionSubmissionConflict, ExecutionSubmissionUnknown
from app.execution.receipt import CancellationReceipt, ExecutionStatus, ExecutionSubmissionReceipt
from app.execution.request import ExecutionRequest


class ExecutorAdapter(Protocol):
    async def submit(self, request: ExecutionRequest) -> ExecutionSubmissionReceipt: ...

    async def get_by_idempotency_key(
        self, key: str
    ) -> ExecutionSubmissionReceipt | None: ...

    async def get_status(self, external_execution_id: str) -> ExecutionStatus: ...

    async def cancel(
        self, external_execution_id: str, cancellation_idempotency_key: str
    ) -> CancellationReceipt: ...


class FakeExecutorAdapter:
    """Deterministic test adapter. It never executes code, models, or data."""

    def __init__(self) -> None:
        self._receipts: dict[str, ExecutionSubmissionReceipt] = {}
        self._request_digests: dict[str, str] = {}
        self._statuses: dict[str, str] = {}
        self._behaviors: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.submit_calls = 0

    def set_behavior(self, idempotency_key: str, behavior: str) -> None:
        if behavior not in {"accept", "timeout_unknown", "reject_retryable", "reject"}:
            raise ValueError("unsupported FakeExecutor behavior")
        self._behaviors[idempotency_key] = behavior

    async def submit(self, request: ExecutionRequest) -> ExecutionSubmissionReceipt:
        async with self._lock:
            existing = self._receipts.get(request.submission_idempotency_key)
            known_digest = self._request_digests.get(request.submission_idempotency_key)
            if known_digest is not None and known_digest != request.request_digest:
                raise ExecutionSubmissionConflict(
                    "submission idempotency key already has another request digest"
                )
            if existing is not None:
                return existing
            self.submit_calls += 1
            self._request_digests[request.submission_idempotency_key] = request.request_digest
            behavior = self._behaviors.get(request.submission_idempotency_key, "accept")
            if behavior == "timeout_unknown":
                raise ExecutionSubmissionUnknown("fake submission outcome is unknown")
            accepted_at = datetime.now(timezone.utc)
            external_id = f"fake:{request.run_id}"
            accepted = behavior == "accept"
            receipt_document = {
                "schema_version": "execution-submission-receipt/v1",
                "accepted": accepted,
                "external_execution_id": external_id if accepted else None,
                "accepted_at": accepted_at.isoformat() if accepted else None,
                "request_digest": request.request_digest,
                "retryable": behavior == "reject_retryable",
                "error_code": None if accepted else f"fake_{behavior}",
            }
            receipt = ExecutionSubmissionReceipt(
                accepted=accepted,
                external_execution_id=external_id if accepted else None,
                accepted_at=accepted_at if accepted else None,
                request_digest=request.request_digest,
                retryable=behavior == "reject_retryable",
                error_code=None if accepted else f"fake_{behavior}",
                receipt_digest=canonical_json_digest_v1(receipt_document),
            )
            self._receipts[request.submission_idempotency_key] = receipt
            if accepted:
                self._statuses[external_id] = "accepted"
            return receipt

    async def get_by_idempotency_key(
        self, key: str
    ) -> ExecutionSubmissionReceipt | None:
        return self._receipts.get(key)

    async def lookup_submission(
        self, key: str
    ) -> ExecutionSubmissionReceipt | None:
        return await self.get_by_idempotency_key(key)

    async def get_status(self, external_execution_id: str) -> ExecutionStatus:
        return ExecutionStatus(
            external_execution_id=external_execution_id,
            status=self._statuses.get(external_execution_id, "unknown"),
        )

    async def cancel(
        self, external_execution_id: str, cancellation_idempotency_key: str
    ) -> CancellationReceipt:
        del cancellation_idempotency_key
        accepted = external_execution_id in self._statuses
        if accepted:
            self._statuses[external_execution_id] = "cancelled"
        return CancellationReceipt(
            external_execution_id=external_execution_id,
            accepted=accepted,
            receipt_digest=canonical_json_digest_v1(
                {
                    "schema_version": "execution-cancellation/v1",
                    "external_execution_id": external_execution_id,
                    "accepted": accepted,
                }
            ),
        )

    def build_callback(
        self,
        *,
        run_id: UUID,
        space_id: UUID,
        callback_type: str,
        correlation_id: UUID,
        payload_snapshot: dict[str, object],
    ) -> ExecutionCallbackEnvelope:
        """Build deterministic synthetic callback evidence; no code or data is run."""

        external_execution_id = f"fake:{run_id}"
        if external_execution_id not in self._statuses:
            raise ValueError("FakeExecutor submission is unknown")
        ordinal = {
            "execution.started": 1,
            "execution.completed": 2,
            "execution.failed": 2,
            "execution.interrupted": 2,
        }.get(callback_type)
        if ordinal is None:
            raise ValueError("unsupported callback type")
        callback_id = f"fake-callback:{run_id}:{ordinal}:{callback_type}"
        occurred_at = datetime.now(timezone.utc)
        evidence = canonical_json_digest_v1(
            {
                "schema_version": "fake-execution-evidence/v1",
                "run_id": str(run_id),
                "callback_type": callback_type,
                "payload": payload_snapshot,
            }
        )
        self._statuses[external_execution_id] = callback_type.removeprefix("execution.")
        return ExecutionCallbackEnvelope(
            space_id=space_id,
            compute_run_id=run_id,
            executor_namespace="medtrust.fake-executor.v1",
            external_execution_id=external_execution_id,
            callback_id=callback_id,
            callback_type=callback_type,
            callback_schema_version=1,
            occurred_at=occurred_at,
            payload_snapshot=dict(payload_snapshot),
            execution_evidence_digest=evidence,
            authentication_evidence_digest=canonical_json_digest_v1(
                {
                    "schema_version": "fake-authentication-evidence/v1",
                    "namespace": "medtrust.fake-executor.v1",
                    "callback_id": callback_id,
                }
            ),
            correlation_id=correlation_id,
            causation_id=uuid5(NAMESPACE_URL, f"fake-callback-cause:{run_id}:{ordinal}"),
        )
