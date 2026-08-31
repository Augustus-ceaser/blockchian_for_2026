from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.applications import ApplicationDraftRequest
from app.modules.applications.lifecycle import _base_parameters
from app.modules.audit import canonical_json_digest_v1


def _payload() -> dict:
    data_version_id = str(uuid4())
    model_version_id = str(uuid4())
    return {
        "schema_version": "phase5.3/application-request/v1",
        "data_version_id": data_version_id,
        "model_version_id": model_version_id,
        "profile": {
            "demand_name": "Explicitly selected Agent recommendation",
            "project_type": "model_external_validation",
            "project_summary": "Validate one explicitly selected data and model version pair.",
            "project_lead": "Research lead",
            "contact": "Research validation team",
            "is_demo": True,
            "purpose_code": "model_validation",
            "research_purpose": "Evaluate aggregate technical performance for the selected pair.",
            "use_background": "The pair was compared in the governed role assistant.",
            "expected_value": "Preserve the selection rationale for multiparty review.",
            "clinical_diagnosis": False,
            "research_publication": False,
            "commercial_validation": False,
            "ethics_or_approval_statement": "Public demonstration assets only.",
            "project_reference": "AGENT-PAIR-1",
            "data_minimization": "Use only the fixed approved demonstration scope and aggregate outputs.",
        },
        "data_scope": {
            "scope_type": "all_approved_demo_data",
            "subset_description": "Fixed approved demonstration scope.",
            "sample_count": 20,
            "selection_criteria": "Use the immutable published scope.",
        },
        "execution": {
            "run_count": 1,
            "valid_days": 30,
            "environment_requirements": "Fixed CPU executor.",
            "internet_required": False,
            "fixed_data_version": True,
            "fixed_model_version": True,
            "requested_outputs": ["aggregate_metrics"],
        },
        "review_requirements": {
            "hospital_egress_review": True,
            "model_technical_confirmation": True,
            "result_review_notes": "Review aggregate outputs before release.",
            "output_recipient": "Research validation team",
        },
        "declarations": {
            "no_raw_data_download": True,
            "no_model_weight_download": True,
            "approved_purpose_only": True,
            "accept_multiparty_review": True,
            "accept_result_isolation": True,
            "accept_full_audit": True,
        },
        "recommendation_context": {
            "source": "role_assistant",
            "selected_by_user": True,
            "selected_pair_key": f"{data_version_id}:{model_version_id}",
            "data_version_id": data_version_id,
            "model_version_id": model_version_id,
            "rank": 1,
            "score": 91,
            "score_max": 100,
            "ruleset_version": "orthopedic-match-v1",
            "pair_schema_version": "medtrust.data-model-match/v1",
            "stage": "application_candidate",
            "hard_gate_status": "pass",
            "reasons": ["疾病、模态与任务匹配"],
            "limitations": ["仍需服务端兼容性复核"],
        },
    }


def test_recommendation_context_is_normalized_as_unverified_client_snapshot() -> None:
    payload = _payload()

    validated = ApplicationDraftRequest.model_validate(payload).model_dump(mode="json")

    assert validated["recommendation_context"]["selected_pair_key"] == payload["recommendation_context"]["selected_pair_key"]
    assert validated["recommendation_context"]["selected_by_user"] is True
    assert validated["recommendation_context"]["score"] == 91
    assert validated["recommendation_context"]["evidence_kind"] == "client_selection_snapshot"
    assert validated["recommendation_context"]["verification_status"] == "client_asserted_unverified"
    assert validated["recommendation_context"]["authority"] == "client_assertion_only"


def test_recommendation_context_must_match_selected_versions() -> None:
    payload = deepcopy(_payload())
    payload["recommendation_context"]["model_version_id"] = str(uuid4())
    payload["recommendation_context"]["selected_pair_key"] = (
        f'{payload["recommendation_context"]["data_version_id"]}:'
        f'{payload["recommendation_context"]["model_version_id"]}'
    )

    with pytest.raises(ValidationError, match="must match the selected versions"):
        ApplicationDraftRequest.model_validate(payload)


def test_recommendation_context_remains_optional_for_manual_drafts() -> None:
    payload = _payload()
    payload.pop("recommendation_context")

    validated = ApplicationDraftRequest.model_validate(payload)

    assert validated.recommendation_context is None


def test_client_cannot_claim_platform_verification() -> None:
    payload = _payload()
    payload["recommendation_context"]["verification_status"] = "platform_verified"

    with pytest.raises(ValidationError, match="client_asserted_unverified"):
        ApplicationDraftRequest.model_validate(payload)


def test_client_selection_claim_must_be_internally_consistent() -> None:
    payload = _payload()
    payload["recommendation_context"]["selected_pair_key"] = "forged-pair-key"

    with pytest.raises(ValidationError, match="pair key must match"):
        ApplicationDraftRequest.model_validate(payload)

    payload = _payload()
    payload["recommendation_context"]["score"] = 91
    payload["recommendation_context"]["score_max"] = 50

    with pytest.raises(ValidationError, match="must not exceed score_max"):
        ApplicationDraftRequest.model_validate(payload)


def test_server_receipt_digests_only_the_normalized_client_snapshot() -> None:
    payload = _payload()
    payload["recommendation_context"]["received_at"] = "forged"
    payload["recommendation_context"]["snapshot_digest"] = "forged"
    document = ApplicationDraftRequest.model_validate(payload).model_dump(mode="json")

    parameters = _base_parameters(document)
    snapshot = parameters["request"]["recommendation_context"]
    receipt = parameters["client_selection_snapshot_receipt"]

    assert "received_at" not in snapshot
    assert "snapshot_digest" not in snapshot
    assert receipt["schema_version"] == "phase5.14/client-selection-receipt/v1"
    assert receipt["snapshot_digest"] == canonical_json_digest_v1(snapshot)
    assert receipt["verification_status"] == "not_platform_verified"
    assert receipt["authority"] == "receipt_only"
    assert receipt["eligibility_authority"] == "server_compatibility_report"
    assert receipt["received_at"].endswith("+00:00")


def test_manual_draft_has_no_client_selection_receipt() -> None:
    payload = _payload()
    payload.pop("recommendation_context")
    document = ApplicationDraftRequest.model_validate(payload).model_dump(mode="json")

    parameters = _base_parameters(document)

    assert "client_selection_snapshot_receipt" not in parameters
