from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.connector_control import HospitalEvidenceBundleRequest
from app.modules.connector_control.services import (
    EVIDENCE_BUNDLE_SCHEMA,
    _contains_prohibited_evidence_field,
)


def digest(label: str) -> str:
    return "sha256:" + __import__("hashlib").sha256(label.encode()).hexdigest()


def payload() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "schema_version": EVIDENCE_BUNDLE_SCHEMA,
        "bundle_id": str(uuid4()),
        "bundle_version": 1,
        "connector_id": str(uuid4()),
        "organization_id": str(uuid4()),
        "task_type": "PATHMNIST_REFERENCE_V1",
        "local_artifact_ref": str(uuid4()),
        "artifact_digest": digest("artifact"),
        "policy_bundle_id": str(uuid4()),
        "policy_bundle_version_id": str(uuid4()),
        "policy_digest": digest("policy"),
        "execution_order_id": str(uuid4()),
        "execution_order_digest": digest("order"),
        "authorization_snapshot_id": str(uuid4()),
        "authorization_snapshot_digest": digest("snapshot"),
        "consumption_receipt_digest": digest("consumption"),
        "task_manifest_id": str(uuid4()),
        "task_manifest_digest": digest("task"),
        "runtime_session_id": str(uuid4()),
        "runtime_digest": digest("runtime"),
        "reference_execution_id": str(uuid4()),
        "execution_result_digest": digest("result"),
        "image_digest": digest("image"),
        "model_reference_digest": digest("model"),
        "dataset_digest": digest("dataset"),
        "output_schema_digest": digest("output"),
        "output_manifest": [
            {
                "name": name, "media_type": media, "size_bytes": 1,
                "digest": digest(name),
            }
            for name, media in (
                ("aggregate_metrics.json", "application/json"),
                ("confusion_matrix.csv", "text/csv"),
                ("execution_summary.json", "application/json"),
            )
        ],
        "result_summary": {
            "sample_count": 20, "correct_count": 19, "accuracy": "0.95",
            "non_clinical": True, "hard_isolation": False,
        },
        "scan_report_id": str(uuid4()),
        "scan_digest": digest("scan"),
        "review_id": str(uuid4()),
        "review_digest": digest("review"),
        "review_decision": "APPROVE_FOR_EVIDENCE_CANDIDACY",
        "reviewer_role": "local_artifact_reviewer",
        "causal_validation_id": str(uuid4()),
        "causal_validation_digest": digest("causal"),
        "local_audit_head": digest("audit"),
        "execution_started_at": now,
        "execution_completed_at": now,
        "quality_limitations": ["Non-clinical engineering reference."],
        "security_boundaries": {
            "network_access": False, "raw_data_transfer": False,
            "model_transfer": False, "artifact_auto_egress": False,
            "hard_isolation": False,
        },
        "generated_at": now,
        "signing_key_id": "connector-signing-key",
        "nonce": "evidence-nonce-00000000000001",
        "bundle_digest": digest("bundle"),
        "signature": "c2lnbmF0dXJl" * 4,
    }


def test_evidence_schema_rejects_unknown_or_disallowed_claims() -> None:
    document = payload()
    parsed = HospitalEvidenceBundleRequest.model_validate(document)
    assert parsed.schema_version == EVIDENCE_BUNDLE_SCHEMA
    with pytest.raises(ValidationError):
        HospitalEvidenceBundleRequest.model_validate(
            {**document, "download_url": "https://example.invalid"}
        )
    with pytest.raises(ValidationError):
        HospitalEvidenceBundleRequest.model_validate(
            {**document, "review_decision": "approved"}
        )


@pytest.mark.parametrize(
    "value",
    [
        {"local_path": r"D:\hospital\artifact"},
        {"nested": {"patient_id": "patient-1"}},
        {"nested": [{"model_weights": "bytes"}]},
        {"value": "../quarantine/output"},
    ],
)
def test_evidence_boundary_rejects_sensitive_or_local_content(value) -> None:
    assert _contains_prohibited_evidence_field(value)


def test_evidence_boundary_accepts_aggregate_summary() -> None:
    document = payload()
    assert not _contains_prohibited_evidence_field(document)
