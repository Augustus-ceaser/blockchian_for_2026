from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from app.execution.pathmnist import PathMNISTAssetBinding, run_pathmnist_smoke, sha256_file


def main() -> None:
    root = Path("/workspace")
    dataset_manifest = json.loads(
        (root / "registered_assets/pathmnist_v1/dataset_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    model_manifest = yaml.safe_load(
        (root / "registered_assets/pathmnist_resnet18_v1/model_manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    plan = yaml.safe_load(
        (root / "smoke_test_plans/pathmnist_resnet18_20.yaml").read_text(
            encoding="utf-8"
        )
    )
    dataset = Path(os.environ["MEDTRUST_PATHMNIST_DATASET_PATH"])
    model = Path(os.environ["MEDTRUST_PATHMNIST_MODEL_PATH"])
    before = {"dataset": sha256_file(dataset), "model": sha256_file(model)}
    binding = PathMNISTAssetBinding(
        dataset_path=dataset,
        model_path=model,
        dataset_digest=dataset_manifest["manifest_digest"],
        model_digest=model_manifest["model_digest"],
    )
    with TemporaryDirectory(dir="/var/lib/medtrust/workspaces") as directory:
        result = run_pathmnist_smoke(
            binding=binding,
            test_indices=tuple(int(value) for value in plan["test_indices"]),
            output_dir=Path(directory),
            verify_reproducibility=True,
        )
    after = {"dataset": sha256_file(dataset), "model": sha256_file(model)}
    assert before == after
    summary = result.execution_summary
    print(
        json.dumps(
            {
                "sample_count": summary["sample_count"],
                "correct_predictions": summary["correct_predictions"],
                "accuracy": summary["accuracy"],
                "mean_confidence": summary["mean_confidence"],
                "device": summary["resource_usage"]["device"],
                "dataset_digest_verified": before["dataset"]
                == dataset_manifest["manifest_digest"],
                "model_digest_verified": before["model"] == model_manifest["model_digest"],
                "assets_unchanged": before == after,
                "outputs": sorted(item["name"] for item in result.output_manifest),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
