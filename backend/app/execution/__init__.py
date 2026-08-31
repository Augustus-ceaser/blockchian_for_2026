"""Fail-closed execution coordination and prototype executor adapters."""

from app.execution.adapter import ExecutorAdapter, FakeExecutorAdapter
from app.execution.errors import (
    ExecutionCoordinatorError,
    ExecutionSubmissionConflict,
    ExecutionSubmissionUnknown,
    NonRetryableExecutionRequest,
)
from app.execution.receipt import (
    CancellationReceipt,
    ExecutionStatus,
    ExecutionSubmissionReceipt,
)
from app.execution.request import ExecutionRequest
from app.execution.local_adapter import LocalBuiltInExecutorAdapter
from app.execution.pathmnist import PathMNISTAssetBinding, PathMNISTExecutionResult
from app.execution.registry import DatasetRegistry, ModelRegistry
from app.execution.coordinator import (
    CoordinatorProcessResult,
    ExecutionCoordinatorConsumer,
    ExecutionCoordinatorService,
)

__all__ = [
    "CancellationReceipt",
    "ExecutionCoordinatorError",
    "ExecutionCoordinatorConsumer",
    "ExecutionCoordinatorService",
    "CoordinatorProcessResult",
    "ExecutionRequest",
    "ExecutionStatus",
    "ExecutionSubmissionConflict",
    "ExecutionSubmissionReceipt",
    "ExecutionSubmissionUnknown",
    "ExecutorAdapter",
    "FakeExecutorAdapter",
    "LocalBuiltInExecutorAdapter",
    "PathMNISTAssetBinding",
    "PathMNISTExecutionResult",
    "ModelRegistry",
    "DatasetRegistry",
    "NonRetryableExecutionRequest",
]
