from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.execution.builtins import BuiltInExecutionError, BuiltInFunctionRunner
from app.execution.local_adapter import declared_artifact_type
from app.execution.manifests import (
    InputManifestValidator,
    ManifestValidationError,
    OutputManifestValidator,
)
from app.execution.registry import DatasetRegistry, ModelRegistry, RegistryValidationError
from app.execution.workspace import ExecutionWorkspaceManager, WorkspaceSecurityError
from app.execution.request import ExecutionRequest
from app.tools.preflight_model_onboarding import PATHMNIST_OUTPUT_FILES, run_preflight


MODEL_DIGEST = f"sha256:{'c' * 64}"
DATASET_DIGEST = f"sha256:{'d' * 64}"


def test_pathmnist_preflight_uses_the_frozen_three_file_allowlist() -> None:
    assert PATHMNIST_OUTPUT_FILES == {
        "aggregate_metrics.json",
        "confusion_matrix.csv",
        "execution_summary.json",
    }


def model_manifest(**overrides):
    value = {
        "model_name": "synthetic statistics",
        "model_version": "1.0",
        "model_digest": MODEL_DIGEST,
        "entrypoint_id": "builtin.synthetic_statistics.v1",
        "runtime": "python-built-in",
        "dependency_lock_digest": f"sha256:{'a' * 64}",
        "input_schema_version": "synthetic-numeric-array/v1",
        "output_schema_version": "synthetic-statistics/v1",
        "allowed_output_types": ["model_artifact"],
        "network_access": False,
        "cpu_limit": 1,
        "memory_limit": 512,
        "timeout_seconds": 30,
        "enabled": True,
    }
    value.update(overrides)
    return value


def dataset_manifest(**overrides):
    value = {
        "dataset_name": "synthetic numeric fixture",
        "dataset_version": "1.0",
        "manifest_digest": DATASET_DIGEST,
        "data_type": "synthetic_numeric_array",
        "input_schema_version": "synthetic-numeric-array/v1",
        "source_type": "synthetic_fixture",
        "public_or_authorized": "synthetic",
        "case_count": 10,
        "allowed_model_types": ["builtin.synthetic_statistics.v1"],
        "authorized_use": ["ai_training"],
        "enabled": True,
    }
    value.update(overrides)
    return value


def test_registry_and_manifest_digests_are_stable() -> None:
    models, datasets = ModelRegistry(), DatasetRegistry()
    first_model = models.register(model_manifest())
    second_model = models.register(model_manifest())
    dataset = datasets.register(dataset_manifest())
    assert first_model.registration_digest == second_model.registration_digest
    assert InputManifestValidator().validate(
        model=first_model, dataset=dataset, requested_use="ai_training"
    ).startswith("sha256:")
    output = [
        {
            "name": "metrics.json",
            "media_type": "application/json",
            "size_bytes": 10,
            "digest": f"sha256:{'e' * 64}",
        }
    ]
    assert OutputManifestValidator().validate(
        model=first_model, artifact_type="model_artifact", manifest=output
    ).startswith("sha256:")
    with pytest.raises(RegistryValidationError, match="entrypoint"):
        models.register(model_manifest(entrypoint_id="file:C:/unsafe.py"))
    with pytest.raises(RegistryValidationError, match="network_access"):
        models.register(model_manifest(network_access=True))


def test_execution_request_output_type_must_be_single_and_allowlisted() -> None:
    request = ExecutionRequest.build(
        run_id=uuid4(),
        job_id=uuid4(),
        space_id=uuid4(),
        contract_revision_id=uuid4(),
        contract_object_id=uuid4(),
        policy_digest=f"sha256:{'1' * 64}",
        constraint_digest=f"sha256:{'2' * 64}",
        binding_id=uuid4(),
        connector_id=uuid4(),
        algorithm_spec_snapshot={
            "declared_output_types": ["aggregate_statistics"]
        },
        algorithm_digest=MODEL_DIGEST,
        compute_input_snapshot={"schema_version": "test/v1"},
        input_digest=DATASET_DIGEST,
        execution_environment_snapshot={"hard_isolation": False},
        resource_limits={"cpu_cores": 1, "memory_mb": 512, "timeout_seconds": 30},
        callback_correlation_id=uuid4(),
    )
    assert (
        declared_artifact_type(
            request,
            allowed_output_types=("model_artifact", "aggregate_statistics"),
        )
        == "aggregate_statistics"
    )
    with pytest.raises(ValueError, match="allowlisted"):
        declared_artifact_type(
            request,
            allowed_output_types=("model_artifact",),
        )


def test_fixed_entrypoint_and_output_file_allowlist_are_closed() -> None:
    models = ModelRegistry()
    with pytest.raises(RegistryValidationError, match="model_digest mismatch"):
        models.register(
            model_manifest(
                entrypoint_id="pathmnist_resnet18_v1",
                allowed_output_files=["aggregate_metrics.json"],
            )
        )
    model = models.register(
        model_manifest(
            model_digest=(
                "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
            ),
            entrypoint_id="pathmnist_resnet18_v1",
            allowed_output_files=["aggregate_metrics.json"],
        )
    )
    with pytest.raises(ManifestValidationError, match="not allowlisted"):
        OutputManifestValidator().validate(
            model=model,
            artifact_type="model_artifact",
            manifest=[
                {
                    "name": "raw-image.png",
                    "media_type": "image/png",
                    "size_bytes": 1,
                    "digest": f"sha256:{'e' * 64}",
                }
            ],
        )


def test_workspace_rejects_traversal_and_symlink(tmp_path: Path, monkeypatch) -> None:
    manager = ExecutionWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(uuid4())
    with pytest.raises(WorkspaceSecurityError, match="traversal"):
        manager.resolve_member(workspace, "output", "../escape.json")
    link = workspace.output / "link.json"
    try:
        link.symlink_to(tmp_path / "outside.json")
    except OSError:
        original = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda self: True if self == link else original(self),
        )
    with pytest.raises(WorkspaceSecurityError, match="symbolic"):
        manager.resolve_member(workspace, "output", "link.json")
    link.unlink(missing_ok=True)
    manager.cleanup(workspace)


def test_builtin_runner_timeout_and_allowlist() -> None:
    async def scenario() -> None:
        runner = BuiltInFunctionRunner()
        with pytest.raises(BuiltInExecutionError, match="allowlisted"):
            await runner.run(
                entrypoint_id="shell:anything",
                values=(1.0,),
                cpu_cores=1,
                memory_mb=128,
                timeout_seconds=1,
            )

        async def slow(_values):
            await asyncio.sleep(2)
            return {"count": 0}

        runner._entrypoints["builtin.synthetic_statistics.v1"] = slow
        with pytest.raises(TimeoutError):
            await runner.run(
                entrypoint_id="builtin.synthetic_statistics.v1",
                values=(1.0,),
                cpu_cores=1,
                memory_mb=128,
                timeout_seconds=1,
            )

    asyncio.run(scenario())


def test_preflight_accepts_only_repository_fixture_paths(tmp_path: Path) -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "onboarding"
    result = run_preflight(
        fixture_root / "model_manifest.yaml",
        fixture_root / "dataset_manifest.json",
        fixture_root=fixture_root,
    )
    assert result["ready"] is True
    outside_model = tmp_path / "model.yaml"
    outside_model.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="fixtures only"):
        run_preflight(
            outside_model,
            fixture_root / "dataset_manifest.json",
            fixture_root=fixture_root,
        )
