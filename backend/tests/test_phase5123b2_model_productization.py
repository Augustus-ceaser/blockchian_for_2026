import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.external_catalog.eligibility import (
    ExternalModelProductEligibilityError,
    MODEL_PRODUCT_NOT_MATERIALIZED,
    require_materialized_model_product,
)
from app.modules.external_catalog.model_productization import (
    ExternalModelProductDraftError,
    REQUIRED_REVIEWS,
    _publication_evidence,
    _validate,
    approve_and_publish_external_model_metadata_product,
    return_external_model_metadata_product,
    submit_external_model_metadata_product,
)
from app.modules.external_catalog.models import (
    ExternalModelGovernanceProfile,
    ExternalModelGovernanceReview,
    ExternalModelRecord,
    ExternalModelVersion,
)


def _graph():
    record_id = uuid4()
    version_id = uuid4()
    digest = "a" * 64
    now = datetime.now(timezone.utc)
    record = ExternalModelRecord(
        id=record_id,
        source_id=uuid4(),
        external_model_id="test/model",
        current_version_id=version_id,
        canonical_name="Test model",
        source_catalog="test",
        model_categories=[],
        modalities=[],
        task_types=[],
        disease_areas=[],
        organs=[],
        species=[],
        training_dataset_references=[],
        evaluation_dataset_references=[],
        metrics_summary=[],
        weights_files=[],
        execution_status="not_materialized",
        quality_flags=[],
        raw_record_digest=digest,
        first_seen_at=now,
        last_seen_at=now,
        status="active",
        model_card_url="https://example.test/model",
    )
    version = ExternalModelVersion(
        id=version_id,
        record_id=record_id,
        catalog_version="v1",
        record_digest=digest,
        normalized_payload={},
        source_evidence=[],
        observed_at=now,
        is_current=True,
    )
    profile = ExternalModelGovernanceProfile(
        id=uuid4(),
        record_id=record_id,
        primary_status="eligible_for_model_draft",
        source_review_status="official_source_confirmed",
        paper_review_status="official_paper_confirmed",
        repository_review_status="official_repository_confirmed",
        model_card_review_status="official_model_card_confirmed",
        license_review_status="permissive",
        weight_review_status="public_available",
        revision_review_status="commit_pinned",
        technical_contract_score=100,
        technical_missing_fields=[],
        clinical_boundary_status="research_only",
        security_review_status="cleared",
        security_risk_flags=[],
        model_family_status="none",
        productization_eligible=True,
        blocking_reasons=[],
        warning_reasons=[],
    )
    decisions = {
        "source": "official_source_confirmed",
        "paper": "official_paper_confirmed",
        "repository": "official_repository_confirmed",
        "model_card": "official_model_card_confirmed",
        "license": "permissive",
        "weights": "public_available",
        "revision": "commit_pinned",
        "technical_contract": "complete",
        "clinical_boundary": "research_only",
        "security": "cleared",
        "model_family": "none",
        "productization": "approved",
    }
    reviews = [
        ExternalModelGovernanceReview(
            id=uuid4(),
            record_id=record_id,
            review_dimension=dimension,
            decision=decisions[dimension],
            decision_payload=(
                {"official_source_url": "https://example.test/model"}
                if dimension == "source" else {}
            ),
            evidence_type="official_page",
            evidence_note="reviewed",
            reviewer_user_id=uuid4(),
            reviewer_organization_id=uuid4(),
            reviewed_at=now,
            source_record_digest=digest,
            idempotency_digest=f"sha256:{dimension:0<64}"[:71],
            created_at=now,
        )
        for dimension in REQUIRED_REVIEWS
    ]
    return record, version, profile, reviews


def test_eligible_graph_is_accepted():
    record, version, profile, reviews = _graph()
    validated, _, latest, official_url = _validate(record, version, profile, reviews)
    assert validated.id == version.id
    assert set(latest) == set(REQUIRED_REVIEWS)
    assert official_url == "https://example.test/model"


@pytest.mark.parametrize(
    ("dimension", "decision"),
    [
        ("license", "unknown"),
        ("weights", "not_released"),
        ("revision", "unpinned"),
        ("security", "blocked"),
        ("productization", "rejected"),
    ],
)
def test_blocking_review_is_rejected(dimension, decision):
    record, version, profile, reviews = _graph()
    next(review for review in reviews if review.review_dimension == dimension).decision = decision
    with pytest.raises(ExternalModelProductDraftError):
        _validate(record, version, profile, reviews)


def test_changed_source_digest_is_rejected():
    record, version, profile, reviews = _graph()
    reviews[0].source_record_digest = "b" * 64
    with pytest.raises(ExternalModelProductDraftError, match="another source digest"):
        _validate(record, version, profile, reviews)


def test_metadata_only_external_model_is_not_selectable():
    session = AsyncMock()
    session.scalar.return_value = type(
        "Link",
        (),
        {
            "materialization_status": "metadata_only",
            "execution_readiness": "not_ready",
            "platform_validation": "not_validated",
        },
    )()
    async def scenario():
        with pytest.raises(
            ExternalModelProductEligibilityError, match=MODEL_PRODUCT_NOT_MATERIALIZED
        ):
            await require_materialized_model_product(session, uuid4())

    asyncio.run(scenario())


def test_native_model_without_external_link_is_unchanged():
    session = AsyncMock()
    session.scalar.return_value = None
    asyncio.run(require_materialized_model_product(session, uuid4()))


@pytest.mark.parametrize(
    ("command", "role", "message"),
    [
        (
            submit_external_model_metadata_product,
            "space_operator",
            "independent catalog curator",
        ),
        (
            return_external_model_metadata_product,
            "catalog_curator",
            "platform operator",
        ),
        (
            approve_and_publish_external_model_metadata_product,
            "catalog_curator",
            "platform operator",
        ),
    ],
)
def test_metadata_publication_roles_are_separated(command, role, message):
    kwargs = {
        "session": AsyncMock(),
        "record_id": uuid4(),
        "actor": SimpleNamespace(role=role),
        "raw_key": "phase5124-role-boundary",
    }
    if command is not submit_external_model_metadata_product:
        kwargs["review"] = {"allow_catalog": True}

    async def scenario():
        with pytest.raises(ExternalModelProductDraftError, match=message):
            await command(**kwargs)

    asyncio.run(scenario())


def test_metadata_publication_evidence_is_explicitly_non_executable():
    evidence = _publication_evidence(
        product=SimpleNamespace(id=uuid4()),
        link=SimpleNamespace(
            source_record_digest="a" * 64,
            governance_snapshot_digest="sha256:" + "b" * 64,
            upstream_provider="Upstream model publisher",
        ),
        record=SimpleNamespace(id=uuid4(), external_model_id="test/model"),
        task=SimpleNamespace(
            id=uuid4(),
            submitter_organization_id=uuid4(),
            submitter_user_id=uuid4(),
        ),
    )

    assert evidence["materialization_status"] == "metadata_only"
    assert evidence["weight_holder_status"] == "external_upstream"
    assert evidence["executor_registered"] is False
    assert evidence["execution_readiness"] == "not_ready"
    assert evidence["platform_validation"] == "not_validated"
    assert evidence["application_eligibility"] is False
    assert evidence["compute_eligibility"] is False
