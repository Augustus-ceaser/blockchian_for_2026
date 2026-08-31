from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml

from app.execution.manifests import InputManifestValidator
from app.execution.registry import DatasetRegistry, ModelRegistry


PATHMNIST_ENTRYPOINT = "pathmnist_resnet18_v1"
PATHMNIST_OUTPUT_FILES = {
    "aggregate_metrics.json",
    "confusion_matrix.csv",
    "execution_summary.json",
}
PATHMNIST_DATASET_DIGEST = (
    "sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72"
)
PATHMNIST_MODEL_DIGEST = (
    "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
)
PATHMNIST_DEPENDENCY_LOCK_DIGEST = (
    "sha256:0f26784743e5c7609b59e3d8b4ca832d7d50444ebf9c0a490a2025b548598e9b"
)
PATHMNIST_TEST_INDICES = (
    126,
    345,
    449,
    561,
    670,
    1296,
    2416,
    2920,
    3085,
    3500,
    3513,
    4188,
    4444,
    5047,
    5090,
    5278,
    5439,
    5642,
    5770,
    6108,
)
_HOST_PATH = re.compile(r"(?:^|[\s'\"])[A-Za-z]:[\\/]")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return not path.is_symlink()
    except (FileNotFoundError, ValueError):
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _read_object(path: Path, *, yaml_format: bool) -> dict[str, Any]:
    value = (
        yaml.safe_load(path.read_text(encoding="utf-8"))
        if yaml_format
        else json.loads(path.read_text(encoding="utf-8"))
    )
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    return value


def _assert_no_host_paths(value: Any) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _assert_no_host_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_host_paths(nested)
    elif isinstance(value, str) and _HOST_PATH.search(value):
        raise ValueError("manifest must not contain a host path")


def run_pathmnist_preflight(
    model_manifest: Path,
    dataset_manifest: Path,
    smoke_test_plan: Path,
    *,
    model_asset: Path,
    dataset_asset: Path,
    dependency_lock: Path,
    registry_root: Path,
    smoke_plan_root: Path,
) -> dict[str, str | bool]:
    if not _inside(model_manifest, registry_root) or not _inside(
        dataset_manifest, registry_root
    ):
        raise ValueError("registered manifests must stay inside registered_assets")
    if not _inside(smoke_test_plan, smoke_plan_root):
        raise ValueError("smoke plan must stay inside smoke_test_plans")
    for asset in (model_asset, dataset_asset, dependency_lock):
        if not asset.is_file() or asset.is_symlink():
            raise ValueError("an explicitly supplied asset is missing or unsafe")

    model_document = _read_object(model_manifest, yaml_format=True)
    dataset_document = _read_object(dataset_manifest, yaml_format=False)
    smoke_document = _read_object(smoke_test_plan, yaml_format=True)
    for document in (model_document, dataset_document, smoke_document):
        _assert_no_host_paths(document)

    if model_document.get("onboarding_profile") != "pathmnist_official_resnet18_v1":
        raise ValueError("model onboarding profile is not approved")
    if dataset_document.get("onboarding_profile") != "pathmnist_public_v1":
        raise ValueError("dataset onboarding profile is not approved")
    if model_document.get("entrypoint_id") != PATHMNIST_ENTRYPOINT:
        raise ValueError("PathMNIST entrypoint is not the fixed platform entrypoint")
    if model_document.get("inference_only") is not True:
        raise ValueError("model must be inference-only")
    if model_document.get("network_access") is not False:
        raise ValueError("network_access must be false")
    if model_document.get("device") != "cpu":
        raise ValueError("PathMNIST onboarding is CPU-only")
    if model_document.get("weights_rights") != "not_specified_by_source":
        raise ValueError("weight rights boundary must remain explicit")
    if model_document.get("non_clinical") is not True:
        raise ValueError("model requires a non-clinical declaration")
    if dataset_document.get("license") != "CC BY 4.0":
        raise ValueError("PathMNIST license declaration mismatch")
    if dataset_document.get("split") != "test" or dataset_document.get("case_count") != 7180:
        raise ValueError("PathMNIST test split declaration mismatch")
    if dataset_document.get("non_clinical") is not True:
        raise ValueError("dataset requires a non-clinical declaration")
    if model_document.get("model_digest") != PATHMNIST_MODEL_DIGEST:
        raise ValueError("model manifest is not the frozen PathMNIST weight")
    if dataset_document.get("manifest_digest") != PATHMNIST_DATASET_DIGEST:
        raise ValueError("dataset manifest is not the frozen PathMNIST archive")
    if model_document.get("dependency_lock_digest") != PATHMNIST_DEPENDENCY_LOCK_DIGEST:
        raise ValueError("dependency lock is not the frozen reviewed runtime")
    if model_document.get("model_digest") != _sha256_file(model_asset):
        raise ValueError("model asset digest mismatch")
    if dataset_document.get("manifest_digest") != _sha256_file(dataset_asset):
        raise ValueError("dataset asset digest mismatch")
    if model_document.get("dependency_lock_digest") != _sha256_file(dependency_lock):
        raise ValueError("dependency lock digest mismatch")

    indices = smoke_document.get("test_indices")
    if (
        not isinstance(indices, list)
        or len(indices) != 20
        or len(set(indices)) != 20
        or any(not isinstance(index, int) or not 0 <= index < 7180 for index in indices)
    ):
        raise ValueError("smoke plan must contain 20 unique PathMNIST test indices")
    if tuple(indices) != PATHMNIST_TEST_INDICES:
        raise ValueError("smoke plan indices differ from the frozen selection")
    if set(smoke_document.get("expected_outputs", [])) != PATHMNIST_OUTPUT_FILES:
        raise ValueError("smoke output allowlist mismatch")
    if smoke_document.get("network_access") is not False:
        raise ValueError("smoke plan must disable network access")
    if smoke_document.get("external_release") is not False:
        raise ValueError("smoke plan must prohibit external release")

    model = ModelRegistry().register(model_document)
    dataset = DatasetRegistry().register(dataset_document)
    compatibility_digest = InputManifestValidator().validate(
        model=model, dataset=dataset, requested_use="model_validation"
    )
    if set(model.allowed_output_files) != PATHMNIST_OUTPUT_FILES:
        raise ValueError("model output file allowlist mismatch")
    return {
        "ready": True,
        "model_registration_digest": model.registration_digest,
        "dataset_registration_digest": dataset.registration_digest,
        "compatibility_digest": compatibility_digest,
        "smoke_plan_digest": _sha256_file(smoke_test_plan),
        "scope": "reviewed_public_pathmnist_inference_20",
    }


def run_preflight(model_manifest: Path, dataset_manifest: Path, *, fixture_root: Path) -> dict[str, str | bool]:
    if not _inside(model_manifest, fixture_root) or not _inside(dataset_manifest, fixture_root):
        raise ValueError("this phase accepts repository onboarding fixtures only")
    model_document = yaml.safe_load(model_manifest.read_text(encoding="utf-8"))
    dataset_document = json.loads(dataset_manifest.read_text(encoding="utf-8"))
    if not isinstance(model_document, dict) or not isinstance(dataset_document, dict):
        raise ValueError("manifest root must be an object")
    if model_document.get("source_declaration") != "platform synthetic self-test":
        raise ValueError("model source declaration is not approved for this phase")
    if dataset_document.get("authorization_declaration") != "synthetic fixture only":
        raise ValueError("dataset authorization declaration is not approved for this phase")
    if model_document.get("non_clinical") is not True or dataset_document.get("non_clinical") is not True:
        raise ValueError("non-clinical declaration is required")
    model_fields = dict(model_document)
    dataset_fields = dict(dataset_document)
    for key in ("source_declaration", "non_clinical"):
        model_fields.pop(key, None)
    for key in ("authorization_declaration", "non_clinical"):
        dataset_fields.pop(key, None)
    model = ModelRegistry().register(model_fields)
    dataset = DatasetRegistry().register(dataset_fields)
    compatibility_digest = InputManifestValidator().validate(
        model=model, dataset=dataset, requested_use="ai_training"
    )
    return {
        "ready": True,
        "model_registration_digest": model.registration_digest,
        "dataset_registration_digest": dataset.registration_digest,
        "compatibility_digest": compatibility_digest,
        "scope": "synthetic_fixture_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=("synthetic-fixture", "pathmnist-reviewed"),
        default="synthetic-fixture",
    )
    parser.add_argument("--model-manifest", required=True, type=Path)
    parser.add_argument("--dataset-manifest", required=True, type=Path)
    parser.add_argument("--smoke-test-plan", type=Path)
    parser.add_argument("--model-asset", type=Path)
    parser.add_argument("--dataset-asset", type=Path)
    parser.add_argument("--dependency-lock", type=Path)
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--smoke-plan-root", type=Path)
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "onboarding",
    )
    args = parser.parse_args()
    try:
        if args.profile == "synthetic-fixture":
            result = run_preflight(
                args.model_manifest, args.dataset_manifest, fixture_root=args.fixture_root
            )
        else:
            required = (
                args.smoke_test_plan,
                args.model_asset,
                args.dataset_asset,
                args.dependency_lock,
                args.registry_root,
                args.smoke_plan_root,
            )
            if any(value is None for value in required):
                raise ValueError("PathMNIST reviewed preflight arguments are incomplete")
            result = run_pathmnist_preflight(
                args.model_manifest,
                args.dataset_manifest,
                args.smoke_test_plan,
                model_asset=args.model_asset,
                dataset_asset=args.dataset_asset,
                dependency_lock=args.dependency_lock,
                registry_root=args.registry_root,
                smoke_plan_root=args.smoke_plan_root,
            )
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
