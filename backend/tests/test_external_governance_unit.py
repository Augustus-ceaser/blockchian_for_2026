from datetime import datetime, timezone
from uuid import uuid4

from app.modules.external_catalog.governance import calculate_profile
from app.modules.external_catalog.models import (
    ExternalDatasetGovernanceReview,
    ExternalDatasetRecord,
)


def _record(**overrides):
    values = {
        "id": uuid4(),
        "source_id": uuid4(),
        "external_id": "dataset-1",
        "canonical_name": "Example dataset",
        "source_catalog": "example",
        "official_source_name": "Official source",
        "official_source_url": "https://example.test/dataset",
        "modalities": ["Pathology"],
        "disease_areas": ["Cancer"],
        "organs": [],
        "task_types": [],
        "species": "human",
        "sample_count": 10,
        "patient_count": 8,
        "file_count": 10,
        "approximate_size_bytes": 1024,
        "data_formats": ["png"],
        "license_status": "research_only",
        "access_level": "open_download",
        "dataset_version": "1.0",
        "link_status": "syntactically_valid_https",
        "quality_flags": [],
        "raw_record_digest": "a" * 64,
        "first_seen_at": datetime.now(timezone.utc),
        "last_seen_at": datetime.now(timezone.utc),
        "status": "active",
    }
    values.update(overrides)
    return ExternalDatasetRecord(**values)


def _review(record, dimension, decision, *, decision_payload=None, evidence_reference=None):
    return ExternalDatasetGovernanceReview(
        id=uuid4(),
        record_id=record.id,
        review_dimension=dimension,
        decision=decision,
        decision_payload=decision_payload or {},
        evidence_type="metadata",
        evidence_reference=evidence_reference,
        evidence_note="Controlled unit-test evidence",
        reviewer_user_id=uuid4(),
        reviewer_organization_id=uuid4(),
        reviewed_at=datetime.now(timezone.utc),
        source_record_digest=record.raw_record_digest,
        idempotency_digest=f"sha256:{'b' * 64}",
    )


def test_unreviewed_catalog_metadata_requires_license_review():
    profile = calculate_profile(
        _record(license_status="unknown", access_level="unknown", dataset_version=None),
        [],
    )

    assert profile["primary_status"] == "needs_license_review"
    assert profile["productization_eligible"] is False


def test_unresolved_duplicate_takes_priority_over_license_review():
    profile = calculate_profile(_record(duplicate_group_id="duplicate-1"), [])

    assert profile["primary_status"] == "duplicate_pending"
    assert profile["duplicate_review_status"] == "duplicate_unresolved"


def test_productization_requires_all_explicit_reviews():
    record = _record()
    reviews = [
        _review(record, "source", "official_source_confirmed"),
        _review(record, "license", "research_only"),
        _review(record, "access", "open_download"),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_profile(record, reviews)

    assert profile["primary_status"] == "eligible_for_draft"
    assert profile["productization_eligible"] is True


def test_hard_blocker_overrides_approval():
    record = _record(link_status="malformed")
    reviews = [
        _review(record, "source", "official_source_confirmed"),
        _review(record, "license", "research_only"),
        _review(record, "access", "open_download"),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_profile(record, reviews)

    assert profile["primary_status"] == "blocked"
    assert profile["productization_eligible"] is False
    assert "link_malformed" in profile["blocking_reasons"]


def test_review_evidence_can_supply_official_url_without_mutating_source_record():
    record = _record(official_source_name=None, official_source_url=None)
    reviews = [
        _review(
            record,
            "source",
            "official_source_confirmed",
            decision_payload={
                "official_source_url": "https://official.example.test/dataset"
            },
        ),
        _review(record, "license", "permissive"),
        _review(record, "access", "open_download"),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_profile(record, reviews)

    assert record.official_source_url is None
    assert "official_source" not in profile["metadata_missing_fields"]
    assert profile["primary_status"] == "eligible_for_draft"
    assert profile["productization_eligible"] is True


def test_unverified_source_evidence_does_not_supply_official_url():
    record = _record(official_source_name=None, official_source_url=None)
    reviews = [
        _review(
            record,
            "source",
            "aggregator_only",
            evidence_reference="https://catalog.example.test/dataset",
        ),
        _review(record, "license", "permissive"),
        _review(record, "access", "open_download"),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_profile(record, reviews)

    assert "official_source" in profile["metadata_missing_fields"]
    assert profile["productization_eligible"] is False


def test_eligible_status_takes_priority_over_noncritical_metadata_warning():
    record = _record(dataset_version=None, sample_count=None)
    reviews = [
        _review(record, "source", "official_source_confirmed"),
        _review(record, "license", "permissive"),
        _review(record, "access", "open_download"),
        _review(record, "productization", "approved"),
    ]

    profile = calculate_profile(record, reviews)

    assert profile["primary_status"] == "eligible_for_draft"
    assert profile["productization_eligible"] is True
    assert "metadata_incomplete" in profile["warning_reasons"]
