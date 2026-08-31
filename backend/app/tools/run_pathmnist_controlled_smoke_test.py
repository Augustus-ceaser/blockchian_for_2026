from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from app.tools.preflight_model_onboarding import run_pathmnist_preflight


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--dataset-asset", required=True, type=Path)
    parser.add_argument("--model-asset", required=True, type=Path)
    parser.add_argument("--result-path", required=True, type=Path)
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[3]
    )
    args = parser.parse_args()
    root = args.repository_root.resolve(strict=True)
    registry_root = root / "registered_assets"
    smoke_root = root / "smoke_test_plans"
    model_manifest = registry_root / "pathmnist_resnet18_v1" / "model_manifest.yaml"
    dataset_manifest = registry_root / "pathmnist_v1" / "dataset_manifest.json"
    dependency_lock = (
        registry_root / "pathmnist_resnet18_v1" / "runtime_requirements.lock"
    )
    smoke_plan = smoke_root / "pathmnist_resnet18_20.yaml"
    result_path = args.result_path.resolve()
    if result_path.exists():
        print(json.dumps({"ready": False, "error": "result path already exists"}))
        return 2
    try:
        result_path.relative_to(root)
    except ValueError:
        print(json.dumps({"ready": False, "error": "result path must stay in workspace"}))
        return 2

    try:
        preflight = run_pathmnist_preflight(
            model_manifest,
            dataset_manifest,
            smoke_plan,
            model_asset=args.model_asset,
            dataset_asset=args.dataset_asset,
            dependency_lock=dependency_lock,
            registry_root=registry_root,
            smoke_plan_root=smoke_root,
        )
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if preflight.get("ready") is not True:
        print(json.dumps({"ready": False, "error": "preflight did not pass"}))
        return 1

    os.environ.update(
        {
            "MEDTRUST_TEST_DATABASE_URL": args.database_url,
            "MEDTRUST_PATHMNIST_DATASET_PATH": str(args.dataset_asset.resolve(strict=True)),
            "MEDTRUST_PATHMNIST_MODEL_PATH": str(args.model_asset.resolve(strict=True)),
            "MEDTRUST_PATHMNIST_DATASET_MANIFEST": str(dataset_manifest.resolve(strict=True)),
            "MEDTRUST_PATHMNIST_MODEL_MANIFEST": str(model_manifest.resolve(strict=True)),
            "MEDTRUST_PATHMNIST_SMOKE_PLAN": str(smoke_plan.resolve(strict=True)),
            "MEDTRUST_PATHMNIST_RESULT_PATH": str(result_path),
        }
    )
    import pytest

    test_file = (
        root
        / "backend"
        / "tests"
        / "integration"
        / "test_pathmnist_controlled_smoke_postgresql.py"
    )
    base_temp = root / "tmp" / f"{result_path.stem}-pytest"
    exit_code = pytest.main(
        [
            str(test_file),
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(base_temp),
        ]
    )
    if exit_code == 0:
        print(
            json.dumps(
                {
                    "ready": True,
                    "preflight": preflight,
                    "result_digest_scope": "aggregate_non_clinical_smoke_only",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())
