from types import SimpleNamespace

import pytest

from app.modules.external_catalog.productization import (
    ExternalProductDraftError,
    _redistribution_status,
    _validate_inputs,
)


def _review(dimension: str, decision: str, digest: str):
    return SimpleNamespace(
        review_dimension=dimension,
        decision=decision,
        decision_payload={"redistribution": "true"},
        source_record_digest=digest,
        evidence_reference="https://example.test/official",
    )


def test_redistribution_status_keeps_unknown_license_restricted():
    assert _redistribution_status(_review("license", "permissive", "digest")) == "allowed"
    restricted = _review("license", "custom_terms", "digest")
    restricted.decision_payload = {"redistribution": "unknown"}
    assert _redistribution_status(restricted) == "restricted"
    prohibited = _review("license", "permissive", "digest")
    prohibited.decision_payload = {"redistribution": "false"}
    assert _redistribution_status(prohibited) == "prohibited"


def test_validate_inputs_requires_current_version_and_four_governance_reviews():
    digest = "a" * 64
    record = SimpleNamespace(
        id="record-id",
        raw_record_digest=digest,
        official_source_name=None,
    )
    profile = SimpleNamespace(
        productization_eligible=True,
        primary_status="eligible_for_draft",
        duplicate_review_status="not_duplicate",
    )
    version = SimpleNamespace(record_id="record-id", is_current=True)
    reviews = {
        key: _review(key, "approved" if key == "productization" else "permissive", digest)
        for key in ("source", "license", "access", "productization")
    }
    reviews["source"].decision = "official_source_confirmed"
    reviews["source"].decision_payload = {
        "official_source_url": "https://example.test/official"
    }
    result = _validate_inputs(
        record=record, version=version, profile=profile, latest=reviews
    )
    assert result[1] == "https://example.test/official"
    with pytest.raises(ExternalProductDraftError, match="current external dataset version"):
        _validate_inputs(record=record, version=None, profile=profile, latest=reviews)


def test_validate_inputs_rejects_stale_governance_snapshot():
    record = SimpleNamespace(id="record-id", raw_record_digest="a" * 64, official_source_name=None)
    profile = SimpleNamespace(
        productization_eligible=True,
        primary_status="eligible_for_draft",
        duplicate_review_status="not_duplicate",
    )
    version = SimpleNamespace(record_id="record-id", is_current=True)
    reviews = {
        key: _review(key, "official_source_confirmed" if key == "source" else "approved", "b" * 64)
        for key in ("source", "license", "access", "productization")
    }
    with pytest.raises(ExternalProductDraftError, match="different source digest"):
        _validate_inputs(record=record, version=version, profile=profile, latest=reviews)


@pytest.mark.parametrize(
    ("dimension", "decision", "message"),
    (
        ("source", "source_missing", "official source"),
        ("license", "unknown", "license review"),
        ("license", "unverified", "license review"),
        ("access", "unknown", "access review"),
    ),
)
def test_validate_inputs_rejects_unverified_publication_evidence(
    dimension: str, decision: str, message: str
):
    digest = "a" * 64
    record = SimpleNamespace(
        id="record-id",
        raw_record_digest=digest,
        official_source_name="Upstream holder",
    )
    profile = SimpleNamespace(
        productization_eligible=True,
        primary_status="eligible_for_draft",
        duplicate_review_status="not_duplicate",
    )
    version = SimpleNamespace(record_id="record-id", is_current=True)
    reviews = {
        key: _review(key, "approved", digest)
        for key in ("source", "license", "access", "productization")
    }
    reviews["source"].decision = "official_source_confirmed"
    reviews["source"].decision_payload = {
        "official_source_url": "https://example.test/official"
    }
    reviews[dimension].decision = decision

    with pytest.raises(ExternalProductDraftError, match=message):
        _validate_inputs(
            record=record,
            version=version,
            profile=profile,
            latest=reviews,
        )
