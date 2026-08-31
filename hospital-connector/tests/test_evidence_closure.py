from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.registry import (
    inspect_reference_artifact_output,
    migrate,
    record_evidence_bundle_delivery,
    review_authorized_local_artifact,
)


def canonical_digest(value: dict) -> str:
    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_evidence_closure_schema_is_append_only() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    tables = {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "local_authorized_artifact_scan_reports",
        "local_authorized_artifact_review_decisions",
        "local_artifact_causal_validations",
        "local_execution_evidence_bundles",
    } <= tables
    assert db.execute(
        "SELECT 1 FROM local_schema_migrations "
        "WHERE version='phase5.13E_0011'"
    ).fetchone()

    db.execute(
        """INSERT INTO local_authorized_artifact_scan_reports
           VALUES('scan-1','artifact-1','v1','passed','[]','[]',
                  'sha256:scan','2026-07-30T00:00:00+00:00')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE local_authorized_artifact_scan_reports "
            "SET decision='failed' WHERE id='scan-1'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(
            "DELETE FROM local_authorized_artifact_scan_reports "
            "WHERE id='scan-1'"
        )


def test_failed_evidence_delivery_can_only_reuse_the_immutable_bundle() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    db.execute(
        """INSERT INTO local_execution_evidence_bundles
           (id,artifact_id,review_id,causal_validation_id,bundle_version,
            schema_version,payload_json,bundle_digest,signing_key_id,signature,
            delivery_status,response_code,created_at)
           VALUES('bundle-1','artifact-1','review-1','validation-1',1,'v1',
                  '{}','sha256:bundle','key-1','signature','pending',NULL,
                  '2026-07-30T00:00:00+00:00')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_evidence_bundles
               SET response_code=102 WHERE id='bundle-1'"""
        )
    record_evidence_bundle_delivery(
        db, bundle_id="bundle-1", delivered=False,
        response_code=503, central_receipt_id=None,
    )
    record_evidence_bundle_delivery(
        db, bundle_id="bundle-1", delivered=False,
        response_code=504, central_receipt_id=None,
    )
    assert db.execute(
        "SELECT response_code FROM local_execution_evidence_bundles"
    ).fetchone()[0] == 503
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_evidence_bundles
               SET response_code=504 WHERE id='bundle-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_evidence_bundles
               SET payload_json='tampered' WHERE id='bundle-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_evidence_bundles
               SET delivery_status='pending' WHERE id='bundle-1'"""
        )
    db.execute(
        """UPDATE local_execution_evidence_bundles
           SET delivery_status='delivered',response_code=200,
               central_receipt_id='central-receipt-1',
               delivered_at='2026-07-30T00:01:00+00:00'
           WHERE id='bundle-1'"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_evidence_bundles
               SET central_receipt_id='replacement' WHERE id='bundle-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_evidence_bundles
               SET delivery_status='delivered' WHERE id='bundle-1'"""
        )


def test_scanner_accepts_only_consistent_three_file_result(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    matrix = [[0] * 9 for _ in range(9)]
    for index, value in enumerate((4, 0, 2, 1, 1, 2, 2, 2, 5)):
        matrix[index][index] = value
    matrix[2][2] = 2
    matrix[2][5] = 1
    metrics = {
        "schema_version": "pathmnist-aggregate-metrics/v1",
        "sample_count": 20,
        "accuracy": "0.95",
        "mean_confidence": "0.96",
        "confusion_matrix": matrix,
        "prediction_digest": "sha256:" + "1" * 64,
    }
    summary = {
        "schema_version": "pathmnist-execution-summary/v1",
        "entrypoint_id": "pathmnist_resnet18_v1",
        "sample_count": 20,
        "processed_count": 20,
        "failed_count": 0,
        "correct_predictions": 19,
        "accuracy": "0.95",
        "mean_confidence": "0.96",
        "split": "test",
        "model_digest": "sha256:" + "2" * 64,
        "dataset_digest": "sha256:" + "3" * 64,
        "dataset_digest_after": "sha256:" + "3" * 64,
        "dataset_digest_unchanged": True,
        "model_digest_verified": True,
        "prediction_digest": "sha256:" + "1" * 64,
        "network_access": False,
        "inference_only": True,
        "non_clinical": True,
        "unexpected_output_count": 0,
        "resource_usage": {"device": "cpu", "hard_isolation": False},
    }
    (output / "aggregate_metrics.json").write_text(
        json.dumps(metrics), encoding="utf-8"
    )
    (output / "execution_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    labels = [f"class-{index}" for index in range(9)]
    rows = [["expected/predicted", *labels]]
    rows.extend(
        [labels[index], *(str(value) for value in matrix[index])]
        for index in range(9)
    )
    (output / "confusion_matrix.csv").write_text(
        "\n".join(",".join(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    manifest = [
        {
            "name": path.name,
            "media_type": (
                "text/csv" if path.suffix == ".csv" else "application/json"
            ),
            "size_bytes": path.stat().st_size,
            "digest": "sha256:"
            + __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output.iterdir())
    ]
    findings, result = inspect_reference_artifact_output(
        output=output, manifest=manifest
    )
    assert findings == []
    assert result is not None
    assert result["sample_count"] == 20
    assert result["correct_count"] == 19
    assert result["accuracy"] == "0.95"

    (output / "unexpected.json").write_text("{}", encoding="utf-8")
    findings, _ = inspect_reference_artifact_output(
        output=output, manifest=manifest
    )
    assert "FILE_ALLOWLIST_MISMATCH" in findings


def test_authorized_review_requires_independent_role_and_passed_scan() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    stamp = "2026-07-30T00:00:00+00:00"
    for user_id, role in (
        ("admin", "connector_local_admin"),
        ("operator", "local_execution_operator"),
        ("reviewer", "local_artifact_reviewer"),
    ):
        db.execute(
            """INSERT INTO local_users
               (id,username,display_name,password_hash,role,status,
                created_at,updated_at)
               VALUES(?,?,?,?,?,'active',?,?)""",
            (user_id, user_id, user_id, "hash", role, stamp, stamp),
        )
    db.execute(
        """INSERT INTO local_authorized_execution_artifacts
           VALUES('artifact-1','execution-1','snapshot-1','{}',
                  'sandbox/sbx/output','[]','sha256:artifact',
                  'quarantined',?)""",
        (stamp,),
    )
    with pytest.raises(ValueError, match="REVIEWER_ROLE_REQUIRED"):
        review_authorized_local_artifact(
            db, artifact_id="artifact-1", reviewer_id="operator",
            decision="APPROVE_FOR_EVIDENCE_CANDIDACY",
            reason="independent review completed",
            canonical_digest=canonical_digest,
        )
    with pytest.raises(ValueError, match="SCAN_NOT_PASSED"):
        review_authorized_local_artifact(
            db, artifact_id="artifact-1", reviewer_id="reviewer",
            decision="APPROVE_FOR_EVIDENCE_CANDIDACY",
            reason="independent review completed",
            canonical_digest=canonical_digest,
        )
    db.execute(
        """INSERT INTO local_authorized_artifact_scan_reports
           VALUES('scan-1','artifact-1','v2','passed','[]','[]',
                  'sha256:scan',?)""",
        (stamp,),
    )
    result = review_authorized_local_artifact(
        db, artifact_id="artifact-1", reviewer_id="reviewer",
        decision="APPROVE_FOR_EVIDENCE_CANDIDACY",
        reason="independent review completed",
        canonical_digest=canonical_digest,
    )
    assert result["decision"] == "APPROVE_FOR_EVIDENCE_CANDIDACY"
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_authorized_artifact_review_decisions
               SET decision='REJECT' WHERE id=?""",
            (result["id"],),
        )
