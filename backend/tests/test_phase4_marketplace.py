from __future__ import annotations

import hashlib
import io
import json
import zipfile
from uuid import uuid4

import pytest

from app.demo.phase4 import Phase4DemoError, build_phase4_safe_files
from app.modules.marketplace.services import (
    MarketplaceServiceError,
    build_safe_result_archive,
)


def _write_trusted_outputs(tmp_path, *, tamper_summary: bool = False):
    run_id = uuid4()
    output = tmp_path / ".runtime" / "phase4-pathmnist-workspaces" / str(run_id) / "output"
    output.mkdir(parents=True)
    metrics = {
        "sample_count": 20,
        "accuracy": 0.8,
        "confusion_matrix": [[1 if row == column else 0 for column in range(9)] for row in range(9)],
    }
    summary = {
        "entrypoint_id": "pathmnist_resnet18_v1",
        "sample_count": 20,
        "split": "test",
        "model_digest": f"sha256:{'a' * 64}",
        "dataset_digest": f"sha256:{'b' * 64}",
        "prediction_digest": f"sha256:{'c' * 64}",
        "resource_usage": {"cpu_seconds": 0.2},
        "host_path": "D:/must-not-leak",
    }
    payloads = {
        "aggregate_metrics.json": json.dumps(metrics).encode(),
        "execution_summary.json": json.dumps(summary).encode(),
    }
    for name, payload in payloads.items():
        (output / name).write_bytes(payload)
    manifest = {
        "outputs": [
            {
                "name": name,
                "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
            }
            for name, payload in payloads.items()
        ]
    }
    (output / "output_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if tamper_summary:
        (output / "execution_summary.json").write_text("{}", encoding="utf-8")
    return run_id


def test_phase4_safe_files_are_aggregate_only_and_sanitized(tmp_path) -> None:
    run_id = _write_trusted_outputs(tmp_path)

    safe_files = build_phase4_safe_files(workspace=tmp_path, run_id=run_id)

    assert set(safe_files) == {
        "aggregate_metrics.json",
        "confusion_matrix.csv",
        "execution_summary.json",
    }
    safe_summary = json.loads(safe_files["execution_summary.json"])
    assert "host_path" not in safe_summary
    assert safe_summary["hard_isolation"] is False
    assert safe_summary["non_clinical"] is True


def test_phase4_safe_files_reject_manifest_digest_mismatch(tmp_path) -> None:
    run_id = _write_trusted_outputs(tmp_path, tamper_summary=True)

    with pytest.raises(Phase4DemoError, match="digest mismatch"):
        build_phase4_safe_files(workspace=tmp_path, run_id=run_id)


def test_safe_result_archive_contains_only_explicit_allowlist() -> None:
    files = {
        "aggregate_metrics.json": b"{}",
        "confusion_matrix.csv": b"actual/predicted\n",
        "execution_summary.json": b"{}",
    }

    archive, manifest = build_safe_result_archive(files)

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.namelist() == sorted(files)
    assert [item["name"] for item in manifest["files"]] == sorted(files)
    assert manifest["contains_raw_data"] is False
    assert manifest["contains_patient_level_results"] is False
    assert manifest["contains_model_weights"] is False
    with pytest.raises(MarketplaceServiceError, match="non-whitelisted"):
        build_safe_result_archive({**files, "model_weights.pth": b"forbidden"})
