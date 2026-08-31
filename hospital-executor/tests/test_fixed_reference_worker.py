from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path

import pytest


IMAGE_DIGEST = (
    "sha256:3c26323fa51cc80da9459c1ef9e7f4fe1c7f9f36cab110d7388706e0d3060df1"
)
os.environ["MEDTRUST_FIXED_EXECUTION_IMAGE_DIGEST"] = IMAGE_DIGEST
module_path = Path(__file__).parents[1] / "fixed_reference_worker.py"
spec = importlib.util.spec_from_file_location("fixed_reference_worker", module_path)
worker = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(worker)


def valid_request() -> dict:
    task = {
        "schema_version": "phase5.13E-2B-1/task-manifest/v1",
        "task_type": "PATHMNIST_REFERENCE_V1",
        "task_version": "1",
        "image_digest": IMAGE_DIGEST,
        "model_reference": "registered://models/pathmnist-resnet18/v1",
        "model_digest": worker.MODEL_DIGEST,
        "dataset_reference": "registered://datasets/pathmnist/v1",
        "dataset_digest": worker.DATASET_DIGEST,
        "input_schema": "pathmnist-rgb-28x28/v1",
        "output_schema": "pathmnist-aggregate-inference/v1",
        "resource_policy": {
            "cpu_cores": 2, "disk_mb": 1024, "memory_mb": 2048,
            "processes": 64, "timeout_seconds": 900,
        },
        "output_allowlist": list(worker.OUTPUT_FILES),
        "network_mode": "none",
        "rootless": True,
        "non_clinical": True,
    }
    input_manifest = {
        "schema_version": "phase5.13E-2B-1/input-manifest/v1",
        "asset_version_id": "registered://datasets/pathmnist/v1",
        "metadata_digest": worker.DATASET_DIGEST,
        "sample_count": 20,
        "schema_digest": worker.canonical_digest({
            "input_schema": "pathmnist-rgb-28x28/v1",
            "shape": [20, 28, 28, 3], "dtype": "uint8",
        }),
        "fixed_indices": list(worker.INDICES),
        "fixed_indices_digest": worker.canonical_digest(
            {"fixed_indices": list(worker.INDICES)}
        ),
    }
    payload = {
        "schema_version": "phase5.13E-2B-1/worker-request/v1",
        "runtime_session_id": "runtime-fixture",
        "sandbox_id": "sbx-fixture",
        "task_manifest": task,
        "task_digest": worker.canonical_digest(task),
        "input_manifest": input_manifest,
        "input_digest": worker.canonical_digest(input_manifest),
    }
    payload["request_digest"] = worker.canonical_digest(payload)
    return payload


def resign(payload: dict) -> dict:
    payload["task_digest"] = worker.canonical_digest(payload["task_manifest"])
    payload["input_digest"] = worker.canonical_digest(payload["input_manifest"])
    unsigned = {key: value for key, value in payload.items() if key != "request_digest"}
    payload["request_digest"] = worker.canonical_digest(unsigned)
    return payload


def test_exact_fixed_request_is_accepted() -> None:
    worker.validate_request(valid_request(), "sbx-fixture")


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("task_manifest", "task_type", "OTHER_TASK"),
        ("task_manifest", "image_digest", "sha256:" + "0" * 64),
        ("task_manifest", "model_reference", "uploaded://model"),
        ("task_manifest", "network_mode", "bridge"),
        ("task_manifest", "rootless", False),
        ("task_manifest", "resource_policy", {
            "cpu_cores": 8, "disk_mb": 1024, "memory_mb": 2048,
            "processes": 64, "timeout_seconds": 900,
        }),
        ("task_manifest", "output_allowlist", ["raw_predictions.csv"]),
        ("input_manifest", "sample_count", 21),
        ("input_manifest", "asset_version_id", "file:///patient/data"),
        ("input_manifest", "schema_digest", "sha256:" + "0" * 64),
    ],
)
def test_tampered_allowlist_fields_are_rejected(
    section: str, field: str, value: object,
) -> None:
    payload = copy.deepcopy(valid_request())
    payload[section][field] = value
    resign(payload)
    with pytest.raises(ValueError, match="NOT_ALLOWLISTED"):
        worker.validate_request(payload, "sbx-fixture")


def test_request_digest_and_sandbox_binding_are_enforced() -> None:
    payload = valid_request()
    payload["request_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="REQUEST_DIGEST_INVALID"):
        worker.validate_request(payload, "sbx-fixture")
    with pytest.raises(ValueError, match="REQUEST_BINDING_INVALID"):
        worker.validate_request(valid_request(), "sbx-other")
