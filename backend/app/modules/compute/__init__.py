"""Controlled compute metadata and fail-closed orchestration boundaries."""

from importlib import import_module
from typing import Any

from app.modules.compute.models import Artifact, ArtifactReview, ComputeJob, ComputeRun

_SERVICE_EXPORTS = {
    "AuditEvidenceUnavailable",
    "ComputeInvariantError",
    "cancel_prepared_run",
    "claim_artifact_review",
    "create_artifact",
    "create_artifact_review",
    "create_compute_job",
    "decide_artifact_review",
    "evaluate_compute_authorization",
    "evaluate_artifact_output_policy",
    "prepare_compute_run",
    "release_artifact",
    "reserve_compute_run",
    "validate_compute_job",
}


def __getattr__(name: str) -> Any:
    if name not in _SERVICE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("app.modules.compute.services"), name)
    globals()[name] = value
    return value

__all__ = [
    "AuditEvidenceUnavailable",
    "ComputeInvariantError",
    "Artifact",
    "ArtifactReview",
    "ComputeJob",
    "ComputeRun",
    "cancel_prepared_run",
    "claim_artifact_review",
    "create_artifact",
    "create_artifact_review",
    "create_compute_job",
    "decide_artifact_review",
    "evaluate_artifact_output_policy",
    "evaluate_compute_authorization",
    "prepare_compute_run",
    "release_artifact",
    "reserve_compute_run",
    "validate_compute_job",
]
