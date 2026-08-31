from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, make_transient_to_detached

from app.modules.audit.models import AUDIT_EVENT_TYPES, AUDIT_SUBJECT_TYPES
from app.modules.audit.services import EVENT_SHAPES
from app.modules.service_access.models import (
    ServiceAccessInvariantError,
    ServiceAccessRequest,
    guard_service_access_mutations,
)
from app.modules.service_access.services import (
    ServiceAccessError,
    operator_status_after,
    provider_status_after,
    validate_request_type,
)


def test_only_delivery_and_artifact_license_request_types_are_accepted() -> None:
    assert (
        validate_request_type("data", "deidentified_data_delivery") == "data"
    )
    assert validate_request_type("model", "model_artifact_license") == "model"

    with pytest.raises(ServiceAccessError, match="does not match"):
        validate_request_type("data", "controlled_compute")
    with pytest.raises(ServiceAccessError, match="does not match"):
        validate_request_type("model", "deidentified_data_delivery")


def test_review_transitions_stop_before_contract_or_fulfillment() -> None:
    assert provider_status_after("submitted", "approve") == "provider_approved"
    assert provider_status_after("submitted", "reject") == "rejected"
    assert (
        operator_status_after("provider_approved", "approve")
        == "approved_pending_contract"
    )
    assert operator_status_after("provider_approved", "reject") == "rejected"

    with pytest.raises(ServiceAccessError, match="submitted"):
        provider_status_after("provider_approved", "approve")
    with pytest.raises(ServiceAccessError, match="provider approval"):
        operator_status_after("submitted", "approve")


def test_service_access_audit_vocabulary_is_registered() -> None:
    expected = {
        "service_access.request.created": ("service_access_request", "success"),
        "service_access.provider.approved": (
            "service_access_request",
            "success",
        ),
        "service_access.provider.rejected": (
            "service_access_request",
            "denied",
        ),
        "service_access.operator.approved": (
            "service_access_request",
            "success",
        ),
        "service_access.operator.rejected": (
            "service_access_request",
            "denied",
        ),
    }
    assert {name: EVENT_SHAPES[name] for name in expected} == expected
    assert expected.keys() <= set(AUDIT_EVENT_TYPES)
    assert "service_access_request" in AUDIT_SUBJECT_TYPES


def test_service_access_model_keeps_required_evidence_fields() -> None:
    columns = ServiceAccessRequest.__table__.columns
    required = {
        "product_snapshot",
        "product_snapshot_digest",
        "request_digest",
        "create_idempotency_digest",
        "provider_decision_idempotency_digest",
        "operator_decision_idempotency_digest",
    }
    assert required <= set(columns.keys())
    assert columns.product_snapshot.nullable is False
    assert columns.product_snapshot_digest.nullable is False
    assert columns.request_digest.nullable is False


def _detached_request() -> ServiceAccessRequest:
    now = datetime.now(timezone.utc)
    request = ServiceAccessRequest(
        id=uuid4(),
        space_id=uuid4(),
        request_number="SAR-UNIT",
        requester_organization_id=uuid4(),
        requester_user_id=uuid4(),
        provider_organization_id=uuid4(),
        product_kind="data",
        product_id=uuid4(),
        version_id=uuid4(),
        service_mode="deidentified_data_delivery",
        purpose="unit test",
        intended_use="unit-test authorization request",
        requested_duration_days=30,
        status="submitted",
        product_snapshot={"name": "unit"},
        product_snapshot_digest="sha256:" + "0" * 64,
        request_digest="sha256:" + "1" * 64,
        create_idempotency_digest="sha256:" + "2" * 64,
        requested_at=now,
        updated_at=now,
        row_version=1,
    )
    make_transient_to_detached(request)
    return request


def test_orm_guard_allows_validated_transition_but_freezes_snapshot() -> None:
    session = Session()
    request = _detached_request()
    session.add(request)
    request.status = "provider_approved"
    request.row_version = 2
    request._transition_validated = True
    guard_service_access_mutations(session, None, None)

    request.product_snapshot = {"name": "mutated"}
    with pytest.raises(ServiceAccessInvariantError, match="immutable"):
        guard_service_access_mutations(session, None, None)
    session.close()
