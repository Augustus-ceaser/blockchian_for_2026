from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from app.execution.pathmnist import PathMNISTAssetBinding, run_pathmnist_smoke


ROOT = Path(os.getenv("MEDTRUST_FIXED_RUNTIME_ROOT", "/runtime-sandboxes"))
DATASET = Path("/assets/data/pathmnist.npz")
MODEL = Path("/assets/model/resnet18_28_1.pth")
IMAGE_DIGEST = os.environ["MEDTRUST_FIXED_EXECUTION_IMAGE_DIGEST"]
DATASET_DIGEST = (
    "sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72"
)
MODEL_DIGEST = (
    "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
)
INDICES = (
    126, 345, 449, 561, 670, 1296, 2416, 2920, 3085, 3500,
    3513, 4188, 4444, 5047, 5090, 5278, 5439, 5642, 5770, 6108,
)
OUTPUT_FILES = (
    "aggregate_metrics.json", "confusion_matrix.csv", "execution_summary.json",
)


def canonical_digest(value: dict[str, Any]) -> str:
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def validate_request(payload: dict[str, Any], sandbox_id: str) -> None:
    expected_keys = {
        "schema_version", "runtime_session_id", "sandbox_id", "task_manifest",
        "task_digest", "input_manifest", "input_digest", "request_digest",
    }
    if set(payload) != expected_keys:
        raise ValueError("REQUEST_SCHEMA_INVALID")
    unsigned = {key: value for key, value in payload.items() if key != "request_digest"}
    if canonical_digest(unsigned) != payload["request_digest"]:
        raise ValueError("REQUEST_DIGEST_INVALID")
    if (
        payload["schema_version"] != "phase5.13E-2B-1/worker-request/v1"
        or payload["sandbox_id"] != sandbox_id
    ):
        raise ValueError("REQUEST_BINDING_INVALID")
    task = payload["task_manifest"]
    if canonical_digest(task) != payload["task_digest"]:
        raise ValueError("TASK_DIGEST_INVALID")
    expected_task = {
        "schema_version": "phase5.13E-2B-1/task-manifest/v1",
        "task_type": "PATHMNIST_REFERENCE_V1",
        "task_version": "1",
        "image_digest": IMAGE_DIGEST,
        "model_reference": "registered://models/pathmnist-resnet18/v1",
        "model_digest": MODEL_DIGEST,
        "dataset_reference": "registered://datasets/pathmnist/v1",
        "dataset_digest": DATASET_DIGEST,
        "input_schema": "pathmnist-rgb-28x28/v1",
        "output_schema": "pathmnist-aggregate-inference/v1",
        "resource_policy": {
            "cpu_cores": 2, "disk_mb": 1024, "memory_mb": 2048,
            "processes": 64, "timeout_seconds": 900,
        },
        "output_allowlist": list(OUTPUT_FILES),
        "network_mode": "none",
        "rootless": True,
        "non_clinical": True,
    }
    if task != expected_task:
        raise ValueError("TASK_NOT_ALLOWLISTED")
    input_manifest = payload["input_manifest"]
    if canonical_digest(input_manifest) != payload["input_digest"]:
        raise ValueError("INPUT_DIGEST_INVALID")
    expected_input = {
        "schema_version": "phase5.13E-2B-1/input-manifest/v1",
        "asset_version_id": "registered://datasets/pathmnist/v1",
        "metadata_digest": DATASET_DIGEST,
        "sample_count": 20,
        "schema_digest": canonical_digest({
            "input_schema": "pathmnist-rgb-28x28/v1",
            "shape": [20, 28, 28, 3], "dtype": "uint8",
        }),
        "fixed_indices": list(INDICES),
        "fixed_indices_digest": canonical_digest(
            {"fixed_indices": list(INDICES)}
        ),
    }
    if input_manifest != expected_input:
        raise ValueError("INPUT_NOT_ALLOWLISTED")


def result_document(
    *, payload: dict[str, Any], status: str, started_at: str,
    completed_at: str, output_manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    document = {
        "schema_version": "phase5.13E-2B-1/worker-result/v1",
        "runtime_session_id": payload["runtime_session_id"],
        "request_digest": payload["request_digest"],
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "output_manifest": output_manifest,
    }
    document["result_digest"] = canonical_digest(document)
    return document


def process_workspace(workspace: Path) -> bool:
    runtime_dir = workspace / "runtime"
    request_path = runtime_dir / "request.json"
    result_path = runtime_dir / "result.json"
    claimed_path = runtime_dir / "request.claimed.json"
    if not request_path.is_file() or result_path.exists() or claimed_path.exists():
        return False
    request_path.replace(claimed_path)
    started_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    payload: dict[str, Any] = {"runtime_session_id": "invalid", "request_digest": ""}
    try:
        payload = json.loads(claimed_path.read_text(encoding="utf-8"))
        validate_request(payload, workspace.name)
        output_dir = workspace / "output"
        if output_dir.is_symlink() or any(output_dir.iterdir()):
            raise ValueError("OUTPUT_WORKSPACE_NOT_EMPTY")
        result = run_pathmnist_smoke(
            binding=PathMNISTAssetBinding(
                dataset_path=DATASET,
                model_path=MODEL,
                dataset_digest=DATASET_DIGEST,
                model_digest=MODEL_DIGEST,
            ),
            test_indices=INDICES,
            output_dir=output_dir,
            verify_reproducibility=True,
        )
        by_name = {item["name"]: dict(item) for item in result.output_manifest}
        manifest = [by_name[name] for name in OUTPUT_FILES]
        status = "completed"
    except Exception:
        for child in (workspace / "output").iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        manifest, status = [], "failed"
    completed_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    document = result_document(
        payload=payload, status=status, started_at=started_at,
        completed_at=completed_at, output_manifest=manifest,
    )
    temporary = runtime_dir / "result.json.tmp"
    temporary.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(result_path)
    return True


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        for workspace in ROOT.glob("sbx-*"):
            if workspace.is_dir() and workspace.parent.resolve() == ROOT.resolve():
                process_workspace(workspace)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
