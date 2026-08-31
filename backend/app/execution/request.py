from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from app.modules.audit import canonical_json_digest_v1, digest_idempotency_key


@dataclass(frozen=True)
class ExecutionRequest:
    run_id: UUID
    job_id: UUID
    space_id: UUID
    contract_revision_id: UUID
    contract_object_id: UUID
    policy_digest: str
    constraint_digest: str
    binding_id: UUID
    connector_id: UUID
    algorithm_spec_snapshot: Mapping[str, Any]
    algorithm_digest: str
    compute_input_snapshot: Mapping[str, Any]
    input_digest: str
    execution_environment_snapshot: Mapping[str, Any]
    resource_limits: Mapping[str, int]
    callback_correlation_id: UUID
    submission_idempotency_key: str
    request_digest: str

    @classmethod
    def build(cls, **values: Any) -> "ExecutionRequest":
        for field_name in (
            "algorithm_spec_snapshot",
            "compute_input_snapshot",
            "execution_environment_snapshot",
            "resource_limits",
        ):
            values[field_name] = MappingProxyType(dict(values[field_name]))
        values["submission_idempotency_key"] = digest_idempotency_key(
            f"medtrust:compute-run:{values['run_id']}"
        )
        manifest = {
            "schema_version": "execution-request/v1",
            "run_id": str(values["run_id"]),
            "job_id": str(values["job_id"]),
            "space_id": str(values["space_id"]),
            "contract_revision_id": str(values["contract_revision_id"]),
            "contract_object_id": str(values["contract_object_id"]),
            "policy_digest": values["policy_digest"],
            "constraint_digest": values["constraint_digest"],
            "binding_id": str(values["binding_id"]),
            "connector_id": str(values["connector_id"]),
            "algorithm_spec_snapshot": dict(values["algorithm_spec_snapshot"]),
            "algorithm_digest": values["algorithm_digest"],
            "compute_input_snapshot": dict(values["compute_input_snapshot"]),
            "input_digest": values["input_digest"],
            "execution_environment_snapshot": dict(values["execution_environment_snapshot"]),
            "resource_limits": dict(values["resource_limits"]),
            "callback_correlation_id": str(values["callback_correlation_id"]),
            "submission_idempotency_key": values["submission_idempotency_key"],
        }
        values["request_digest"] = canonical_json_digest_v1(manifest)
        return cls(**values)

    def safe_manifest(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "job_id": str(self.job_id),
            "space_id": str(self.space_id),
            "contract_revision_id": str(self.contract_revision_id),
            "contract_object_id": str(self.contract_object_id),
            "policy_digest": self.policy_digest,
            "constraint_digest": self.constraint_digest,
            "binding_id": str(self.binding_id),
            "connector_id": str(self.connector_id),
            "algorithm_spec_snapshot": dict(self.algorithm_spec_snapshot),
            "algorithm_digest": self.algorithm_digest,
            "compute_input_snapshot": dict(self.compute_input_snapshot),
            "input_digest": self.input_digest,
            "execution_environment_snapshot": dict(self.execution_environment_snapshot),
            "resource_limits": dict(self.resource_limits),
            "callback_correlation_id": str(self.callback_correlation_id),
            "submission_idempotency_key": self.submission_idempotency_key,
            "request_digest": self.request_digest,
        }
