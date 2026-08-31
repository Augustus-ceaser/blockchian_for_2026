from __future__ import annotations

import asyncio
from dataclasses import replace
from types import MappingProxyType
from uuid import uuid4

import pytest

from app.execution import (
    ExecutionRequest,
    ExecutionSubmissionConflict,
    FakeExecutorAdapter,
)
from app.modules.audit import canonical_json_digest_v1


def _request() -> ExecutionRequest:
    return ExecutionRequest.build(
        run_id=uuid4(),
        job_id=uuid4(),
        space_id=uuid4(),
        contract_revision_id=uuid4(),
        contract_object_id=uuid4(),
        policy_digest="sha256:" + "1" * 64,
        constraint_digest="sha256:" + "2" * 64,
        binding_id=uuid4(),
        connector_id=uuid4(),
        algorithm_spec_snapshot={"entrypoint_id": "synthetic.aggregate.v1"},
        algorithm_digest="sha256:" + "3" * 64,
        compute_input_snapshot={"dataset_registration_id": "synthetic-v1"},
        input_digest="sha256:" + "4" * 64,
        execution_environment_snapshot={"network_mode": "deny_by_default"},
        resource_limits={"cpu_cores": 1, "memory_mb": 512, "timeout_seconds": 30},
        callback_correlation_id=uuid4(),
    )


def test_execution_request_digest_is_stable_and_snapshots_are_read_only() -> None:
    first = _request()
    rebuilt = ExecutionRequest.build(
        **{
            key: value
            for key, value in first.__dict__.items()
            if key not in {"submission_idempotency_key", "request_digest"}
        }
    )
    assert rebuilt.request_digest == first.request_digest
    assert rebuilt.submission_idempotency_key == first.submission_idempotency_key
    assert isinstance(first.algorithm_spec_snapshot, MappingProxyType)
    with pytest.raises(TypeError):
        first.algorithm_spec_snapshot["entrypoint_id"] = "tampered"  # type: ignore[index]


def test_fake_executor_submission_is_idempotent_and_rejects_digest_conflict() -> None:
    asyncio.run(_fake_executor_submission_is_idempotent())


async def _fake_executor_submission_is_idempotent() -> None:
    adapter = FakeExecutorAdapter()
    request = _request()
    first = await adapter.submit(request)
    replay = await adapter.submit(request)
    assert first == replay
    assert adapter.submit_calls == 1
    assert await adapter.get_by_idempotency_key(request.submission_idempotency_key) == first
    conflicting = replace(request, request_digest="sha256:" + "9" * 64)
    with pytest.raises(ExecutionSubmissionConflict):
        await adapter.submit(conflicting)
