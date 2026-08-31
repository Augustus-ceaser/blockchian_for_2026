from datetime import datetime, timezone
from uuid import uuid4

from app.modules.external_catalog.model_governance import calculate_model_profile
from app.modules.external_catalog.models import (
    ExternalModelGovernanceReview,
    ExternalModelRecord,
)


def _record(**overrides):
    values = {
        "id": uuid4(), "source_id": uuid4(), "external_model_id": "model-1",
        "canonical_name": "Example model", "source_catalog": "example",
        "model_categories": [], "modalities": ["Pathology"],
        "task_types": ["classification"], "disease_areas": [], "organs": [],
        "species": ["human"], "paper_title": None, "paper_doi": None,
        "paper_url": None, "code_repository_url": None, "model_card_url": None,
        "upstream_provider": None, "framework": None, "library_name": None,
        "architecture": None, "pipeline_tag": None, "input_schema": None,
        "output_schema": None, "preprocessing_summary": None,
        "training_dataset_references": [], "evaluation_dataset_references": [],
        "metrics_summary": [], "license_name": None, "license_url": None,
        "license_status": "unknown", "access_status": "unknown",
        "weights_status": "unknown", "weights_files": [],
        "estimated_weights_size_bytes": None, "revision": None,
        "commit_sha": None, "release_tag": None, "gated": None,
        "clinical_use_status": "not_assessed", "intended_use_summary": None,
        "limitations_summary": None, "execution_status": "not_materialized",
        "quality_flags": [], "raw_record_digest": "a" * 64,
        "first_seen_at": datetime.now(timezone.utc),
        "last_seen_at": datetime.now(timezone.utc), "status": "active",
    }
    values.update(overrides)
    return ExternalModelRecord(**values)


def _review(record, dimension, decision, payload=None):
    return ExternalModelGovernanceReview(
        id=uuid4(), record_id=record.id, review_dimension=dimension,
        decision=decision, decision_payload=payload or {}, evidence_type="manual_record",
        evidence_note="Controlled fixture", reviewer_user_id=uuid4(),
        reviewer_organization_id=uuid4(), reviewed_at=datetime.now(timezone.utc),
        source_record_digest=record.raw_record_digest,
        idempotency_digest=f"sha256:{uuid4().hex * 2}",
    )


def test_initial_profile_only_reports_missing_and_unreviewed_facts():
    record = _record()
    before = record.raw_record_digest

    profile = calculate_model_profile(record, [])

    assert profile["productization_eligible"] is False
    assert profile["primary_status"] == "security_review_required"
    assert {"input_schema", "output_schema", "preprocessing"} <= set(
        profile["technical_missing_fields"]
    )
    assert "weight_integrity_unknown" in profile["security_risk_flags"]
    assert record.raw_record_digest == before
    assert record.execution_status == "not_materialized"


def test_public_catalog_weight_metadata_is_not_local_materialization():
    record = _record(weights_status="public_available")
    profile = calculate_model_profile(record, [])

    assert profile["weight_review_status"] == "unknown"
    assert profile["productization_eligible"] is False
    assert record.execution_status == "not_materialized"


def test_eligibility_requires_complete_human_governance():
    record = _record(
        framework="pytorch", architecture="resnet", input_schema="tensor",
        output_schema="class probabilities", preprocessing_summary="normalize",
        training_dataset_references=["declared"], evaluation_dataset_references=["declared"],
        revision="v1", weights_files=[{"name": "weights.bin"}],
        license_name="research terms", limitations_summary="research only",
        clinical_use_status="research_only",
    )
    reviews = [
        _review(record, "source", "official_source_confirmed"),
        _review(record, "paper", "official_paper_confirmed"),
        _review(record, "repository", "official_repository_confirmed"),
        _review(record, "model_card", "official_model_card_confirmed"),
        _review(record, "license", "research_only"),
        _review(record, "weights", "public_available"),
        _review(record, "revision", "model_revision_pinned"),
        _review(record, "clinical_boundary", "research_only"),
        _review(record, "security", "cleared"),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_model_profile(record, reviews)

    assert profile["primary_status"] == "eligible_for_model_draft"
    assert profile["productization_eligible"] is True


def test_unresolved_family_precedes_other_review_queues():
    profile = calculate_model_profile(_record(), [], family_status="potential")

    assert profile["primary_status"] == "family_resolution_pending"
    assert profile["productization_eligible"] is False


def test_unexpected_execution_state_is_blocked():
    profile = calculate_model_profile(
        _record(execution_status="materialized"), []
    )

    assert profile["primary_status"] == "blocked"
    assert "unexpected_execution_status" in profile["blocking_reasons"]


def test_evidence_overlay_completes_metadata_without_mutating_raw_record():
    record = _record(
        framework="pytorch", architecture="resnet", input_schema="tensor",
        output_schema="class probabilities", training_dataset_references=["declared"],
        evaluation_dataset_references=["declared"], license_name="custom terms",
        limitations_summary="research only", clinical_use_status="research_only",
    )
    reviews = [
        _review(record, "source", "official_source_confirmed"),
        _review(record, "paper", "official_paper_confirmed"),
        _review(record, "repository", "official_repository_confirmed"),
        _review(record, "model_card", "official_model_card_confirmed"),
        _review(record, "license", "research_only"),
        _review(
            record, "weights", "gated",
            {"files": [{"name": "model.bin", "size": 123}], "integrity_metadata_present": True},
        ),
        _review(record, "revision", "model_revision_pinned"),
        _review(
            record, "technical_contract", "accepted",
            {"confirmed_fields": ["preprocessing", "revision", "weight_file_metadata"]},
        ),
        _review(record, "clinical_boundary", "research_only"),
        _review(
            record, "security", "cleared",
            {"resolved_flags": ["preprocessing_missing", "dependency_unpinned"]},
        ),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_model_profile(record, reviews)

    assert profile["productization_eligible"] is True
    assert profile["primary_status"] == "eligible_for_model_draft"
    assert record.preprocessing_summary is None
    assert record.weights_files == []


def test_eligible_primary_status_keeps_nonblocking_missing_field_warning():
    record = _record(
        framework="pytorch",
        architecture="vit",
        input_schema="image tensor",
        output_schema="embedding tensor",
        preprocessing_summary="resize and normalize",
        training_dataset_references=["declared"],
        evaluation_dataset_references=[],
        license_name="research terms",
        limitations_summary="research only",
        clinical_use_status="research_only",
    )
    reviews = [
        _review(record, "source", "official_source_confirmed"),
        _review(record, "paper", "official_paper_confirmed"),
        _review(record, "repository", "official_repository_confirmed"),
        _review(record, "model_card", "official_model_card_confirmed"),
        _review(record, "license", "research_only"),
        _review(record, "weights", "gated", {"integrity_metadata_present": True}),
        _review(record, "revision", "model_revision_pinned"),
        _review(
            record,
            "technical_contract",
            "accepted",
            {"confirmed_fields": ["revision", "weight_file_metadata"]},
        ),
        _review(record, "clinical_boundary", "research_only"),
        _review(record, "security", "cleared"),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_model_profile(record, reviews)

    assert profile["productization_eligible"] is True
    assert profile["primary_status"] == "eligible_for_model_draft"
    assert "evaluation_references" in profile["technical_missing_fields"]
    assert "technical_contract_incomplete" in profile["warning_reasons"]
