from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.execution.adapter import ExecutorAdapter
from app.execution.builtins import BuiltInFunctionRunner
from app.execution.callback import ExecutionCallbackEnvelope
from app.execution.errors import ExecutionSubmissionConflict
from app.execution.manifests import InputManifestValidator, OutputManifestValidator
from app.execution.pathmnist import (
    PATHMNIST_ENTRYPOINT_ID,
    PathMNISTAssetBinding,
    run_pathmnist_smoke,
)
from app.execution.receipt import CancellationReceipt, ExecutionStatus, ExecutionSubmissionReceipt
from app.execution.registry import DatasetRegistry, ModelRegistry
from app.execution.request import ExecutionRequest
from app.execution.workspace import ExecutionWorkspaceManager
from app.modules.audit import canonical_json_digest_v1


def declared_artifact_type(
    request: ExecutionRequest, *, allowed_output_types: tuple[str, ...]
) -> str:
    declared = request.algorithm_spec_snapshot.get("declared_output_types")
    if (
        not isinstance(declared, (list, tuple))
        or len(declared) != 1
        or not isinstance(declared[0], str)
        or declared[0] not in allowed_output_types
    ):
        raise ValueError("execution request requires one allowlisted output type")
    return declared[0]


class LocalBuiltInExecutorAdapter(ExecutorAdapter):
    """Prototype adapter restricted to code-owned, allowlisted entrypoints.

    It does not provide production-grade Windows network, CPU, or memory isolation.
    """

    def __init__(
        self,
        *,
        model_registry: ModelRegistry,
        dataset_registry: DatasetRegistry,
        dataset_manifest_digest: str,
        workspace_root: Path,
        pathmnist_asset_binding: PathMNISTAssetBinding | None = None,
        pathmnist_test_indices: tuple[int, ...] = (),
    ) -> None:
        self._models = model_registry
        self._datasets = dataset_registry
        self._dataset_digest = dataset_manifest_digest
        self._workspaces = ExecutionWorkspaceManager(workspace_root)
        self._pathmnist_assets = pathmnist_asset_binding
        self._pathmnist_test_indices = pathmnist_test_indices
        self._runner = BuiltInFunctionRunner()
        self._input_validator = InputManifestValidator()
        self._output_validator = OutputManifestValidator()
        self._receipts: dict[str, ExecutionSubmissionReceipt] = {}
        self._requests: dict[str, ExecutionRequest] = {}
        self._statuses: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def submit(self, request: ExecutionRequest) -> ExecutionSubmissionReceipt:
        async with self._lock:
            existing = self._receipts.get(request.submission_idempotency_key)
            if existing is not None:
                if existing.request_digest != request.request_digest:
                    raise ExecutionSubmissionConflict("local submission idempotency conflict")
                return existing
            model = self._models.require_enabled(request.algorithm_digest)
            dataset = self._datasets.require_enabled(self._dataset_digest)
            requested_use = (
                "model_validation"
                if model.entrypoint_id == PATHMNIST_ENTRYPOINT_ID
                else "ai_training"
            )
            self._input_validator.validate(
                model=model, dataset=dataset, requested_use=requested_use
            )
            external_id = f"local-builtin:{request.run_id}"
            accepted_at = datetime.now(timezone.utc)
            receipt = ExecutionSubmissionReceipt(
                accepted=True,
                external_execution_id=external_id,
                accepted_at=accepted_at,
                request_digest=request.request_digest,
                retryable=False,
                error_code=None,
                receipt_digest=canonical_json_digest_v1(
                    {
                        "schema_version": "local-built-in-submission/v1",
                        "external_execution_id": external_id,
                        "request_digest": request.request_digest,
                        "accepted_at": accepted_at.isoformat(),
                    }
                ),
            )
            self._receipts[request.submission_idempotency_key] = receipt
            self._requests[external_id] = request
            self._statuses[external_id] = "accepted"
            return receipt

    async def get_by_idempotency_key(self, key: str) -> ExecutionSubmissionReceipt | None:
        return self._receipts.get(key)

    async def get_status(self, external_execution_id: str) -> ExecutionStatus:
        return ExecutionStatus(
            external_execution_id=external_execution_id,
            status=self._statuses.get(external_execution_id, "unknown"),
        )

    def pending_execution_ids(self) -> tuple[str, ...]:
        """Return locally accepted executions not yet run by this process."""

        return tuple(
            execution_id
            for execution_id, state in self._statuses.items()
            if state == "accepted"
        )

    async def cancel(
        self, external_execution_id: str, cancellation_idempotency_key: str
    ) -> CancellationReceipt:
        accepted = external_execution_id in self._statuses
        if accepted:
            self._statuses[external_execution_id] = "cancelled"
        return CancellationReceipt(
            external_execution_id=external_execution_id,
            accepted=accepted,
            receipt_digest=canonical_json_digest_v1(
                {
                    "schema_version": "local-built-in-cancel/v1",
                    "external_execution_id": external_execution_id,
                    "idempotency_key_digest": canonical_json_digest_v1(
                        {"key": cancellation_idempotency_key}
                    ),
                    "accepted": accepted,
                }
            ),
        )

    async def execute_self_test(
        self, external_execution_id: str
    ) -> tuple[ExecutionCallbackEnvelope, ExecutionCallbackEnvelope]:
        request = self._requests.get(external_execution_id)
        if request is None:
            raise ValueError("local execution is unknown")
        model = self._models.require_enabled(request.algorithm_digest)
        dataset = self._datasets.require_enabled(self._dataset_digest)
        workspace = self._workspaces.create(request.run_id)
        started_at = datetime.now(timezone.utc)
        started_payload = {
            "schema_version": 1,
            "started_at": started_at.isoformat(),
            "runtime_summary": {
                "entrypoint_id": model.entrypoint_id,
                "network_access": False,
                "isolation_level": "prototype_in_process_allowlist",
            },
        }
        started = self._callback(
            request=request,
            external_execution_id=external_execution_id,
            callback_type="execution.started",
            ordinal=1,
            payload=started_payload,
        )
        self._statuses[external_execution_id] = "started"
        if model.entrypoint_id == PATHMNIST_ENTRYPOINT_ID:
            if self._pathmnist_assets is None or len(self._pathmnist_test_indices) != 20:
                raise ValueError("PathMNIST local asset binding is not configured")
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    run_pathmnist_smoke,
                    binding=self._pathmnist_assets,
                    test_indices=self._pathmnist_test_indices,
                    output_dir=workspace.output,
                    verify_reproducibility=False,
                ),
                timeout=model.timeout_seconds,
            )
            manifest = list(result.output_manifest)
            metrics_digest = result.output_digest
            summary = {
                **result.execution_summary,
                "model_registration_digest": model.registration_digest,
                "dataset_registration_digest": dataset.registration_digest,
            }
            resource_usage = result.resource_usage_summary
        else:
            values = tuple(float(index) for index in range(1, dataset.case_count + 1))
            raw_metrics = await self._runner.run(
                entrypoint_id=model.entrypoint_id,
                values=values,
                cpu_cores=min(model.cpu_limit, 1),
                memory_mb=min(model.memory_limit, 512),
                timeout_seconds=min(model.timeout_seconds, 30),
            )
            metrics = {
                "count": int(raw_metrics["count"]),
                "mean": format(float(raw_metrics["mean"]), ".12g"),
                "standard_deviation": format(
                    float(raw_metrics["standard_deviation"]), ".12g"
                ),
            }
            metrics_bytes = json.dumps(
                metrics, sort_keys=True, separators=(",", ":")
            ).encode()
            metrics_digest = canonical_json_digest_v1(
                {"schema_version": "synthetic-metrics/v1", "metrics": metrics}
            )
            summary = {
                "schema_version": "synthetic-execution-summary/v1",
                "entrypoint_id": model.entrypoint_id,
                "model_registration_digest": model.registration_digest,
                "dataset_registration_digest": dataset.registration_digest,
                "non_clinical": True,
            }
            summary_bytes = json.dumps(
                summary, sort_keys=True, separators=(",", ":")
            ).encode()
            metrics_path = self._workspaces.resolve_member(
                workspace, "output", "metrics.json"
            )
            summary_path = self._workspaces.resolve_member(
                workspace, "output", "execution_summary.json"
            )
            metrics_path.write_bytes(metrics_bytes)
            summary_path.write_bytes(summary_bytes)
            manifest = [
                {
                    "name": "metrics.json",
                    "media_type": "application/json",
                    "size_bytes": len(metrics_bytes),
                    "digest": metrics_digest,
                },
                {
                    "name": "execution_summary.json",
                    "media_type": "application/json",
                    "size_bytes": len(summary_bytes),
                    "digest": canonical_json_digest_v1(summary),
                },
            ]
            resource_usage = {
                "cpu_seconds": 0,
                "peak_memory_mb": 0,
                "enforcement": "prototype_not_os_isolated",
            }
        artifact_type = declared_artifact_type(
            request, allowed_output_types=model.allowed_output_types
        )
        self._output_validator.validate(
            model=model, artifact_type=artifact_type, manifest=manifest
        )
        completed_at = datetime.now(timezone.utc)
        completed = self._callback(
            request=request,
            external_execution_id=external_execution_id,
            callback_type="execution.completed",
            ordinal=2,
            payload={
                "schema_version": 1,
                "completed_at": completed_at.isoformat(),
                "output_manifest": manifest,
                "output_digest": metrics_digest,
                "execution_summary": summary,
                "resource_usage_summary": resource_usage,
                "artifact_type": artifact_type,
                "object_storage_ref": f"workspace-output:{request.run_id}",
            },
        )
        self._statuses[external_execution_id] = "completed"
        return started, completed

    def cleanup(self, run_id: UUID) -> None:
        root = self._workspaces._safe_child(str(run_id))
        if root.exists():
            from app.execution.workspace import ExecutionWorkspace

            self._workspaces.cleanup(
                ExecutionWorkspace(
                    root,
                    root / "input",
                    root / "work",
                    root / "output",
                    root / "logs",
                    root / "manifests",
                )
            )

    @staticmethod
    def _callback(
        *,
        request: ExecutionRequest,
        external_execution_id: str,
        callback_type: str,
        ordinal: int,
        payload: dict[str, object],
    ) -> ExecutionCallbackEnvelope:
        callback_id = f"local:{request.run_id}:{ordinal}:{callback_type}"
        return ExecutionCallbackEnvelope(
            space_id=request.space_id,
            compute_run_id=request.run_id,
            executor_namespace="medtrust.local-built-in.v1",
            external_execution_id=external_execution_id,
            callback_id=callback_id,
            callback_type=callback_type,
            callback_schema_version=1,
            occurred_at=datetime.now(timezone.utc),
            payload_snapshot=payload,
            execution_evidence_digest=canonical_json_digest_v1(
                {
                    "schema_version": "local-built-in-execution-evidence/v1",
                    "request_digest": request.request_digest,
                    "callback_type": callback_type,
                    "payload": payload,
                }
            ),
            authentication_evidence_digest=canonical_json_digest_v1(
                {
                    "schema_version": "local-built-in-auth/v1",
                    "namespace": "medtrust.local-built-in.v1",
                    "callback_id": callback_id,
                }
            ),
            correlation_id=request.callback_correlation_id,
            causation_id=uuid5(NAMESPACE_URL, f"local-executor:{request.run_id}:{ordinal}"),
        )
