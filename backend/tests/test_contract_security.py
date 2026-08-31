from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.modules.contracts.security import (
    CONTRACT_CANONICAL_V2,
    build_contract_canonical_document_v2,
    build_contract_security_decision,
    build_policy_digest_document,
)
from app.modules.contracts.services import canonical_document_digest


def _constraint(name: str, operator: str, value: object, position: int, unit=None):
    return SimpleNamespace(
        constraint_name=name,
        operator=operator,
        value=value,
        unit=unit,
        position_no=position,
    )


def _policy():
    return SimpleNamespace(
        policy_code="permit-controlled-compute",
        policy_type="permission",
        effect="permit",
        subject_contract_party_id=uuid4(),
        contract_object_id=uuid4(),
        action_code="execute_controlled_compute",
        priority=100,
        constraints=[
            _constraint("purpose_code", "in", ["model_validation"], 1),
            _constraint("run_count", "lte", 1, 2, "count"),
        ],
    )


def test_policy_v2_digest_binds_subject_object_and_constraints() -> None:
    policy = _policy()
    baseline = canonical_document_digest(build_policy_digest_document(policy))

    policy.subject_contract_party_id = uuid4()
    assert canonical_document_digest(build_policy_digest_document(policy)) != baseline
    policy = _policy()
    baseline = canonical_document_digest(build_policy_digest_document(policy))
    policy.contract_object_id = uuid4()
    assert canonical_document_digest(build_policy_digest_document(policy)) != baseline
    policy = _policy()
    baseline = canonical_document_digest(build_policy_digest_document(policy))
    policy.constraints[1].value = 2
    assert canonical_document_digest(build_policy_digest_document(policy)) != baseline


def _canonical_inputs() -> dict:
    return {
        "contract_id": str(uuid4()),
        "revision_no": 1,
        "signing_mode": "multi_party",
        "supersedes_revision_id": None,
        "effective_from": "2026-08-29T08:00:00+00:00",
        "effective_until": "2026-09-28T08:00:00+00:00",
        "terms_digest": "sha256:" + "1" * 64,
        "eligibility_digest": "sha256:" + "2" * 64,
        "handoff_guard_digest": "sha256:" + "3" * 64,
        "parties": [
            {
                "id": str(uuid4()),
                "organization_id": str(uuid4()),
                "role": "data_requester",
                "signing_order": 1,
                "required": True,
                "identity_snapshot_digest": "sha256:" + "4" * 64,
            }
        ],
        "data_objects": [
            {
                "id": str(uuid4()),
                "data_product_version_id": str(uuid4()),
                "product_snapshot_digest": "sha256:" + "5" * 64,
                "authorized_scope_digest": "sha256:" + "6" * 64,
            }
        ],
        "model_object": {
            "id": str(uuid4()),
            "model_version_id": str(uuid4()),
            "model_snapshot_digest": "sha256:" + "7" * 64,
            "authorized_scope_digest": "sha256:" + "8" * 64,
        },
        "policies": [
            {"id": str(uuid4()), "digest": "sha256:" + "9" * 64}
        ],
        "binding_specs": [
            {
                "policy_id": str(uuid4()),
                "connector_id": str(uuid4()),
                "execution_role": "compute_executor",
                "required_capability_code": "controlled_compute_execution",
                "required_capability_version": "1.0",
                "is_required": True,
            }
        ],
    }


def test_contract_canonical_v2_binds_scope_party_policy_and_binding() -> None:
    baseline_inputs = _canonical_inputs()
    baseline_document = build_contract_canonical_document_v2(**baseline_inputs)
    assert baseline_document["schema_version"] == CONTRACT_CANONICAL_V2
    baseline = canonical_document_digest(baseline_document)

    for path, replacement in (
        (("parties", 0, "organization_id"), str(uuid4())),
        (("data_objects", 0, "authorized_scope_digest"), "sha256:" + "a" * 64),
        (("policies", 0, "digest"), "sha256:" + "b" * 64),
        (("binding_specs", 0, "connector_id"), str(uuid4())),
    ):
        changed = deepcopy(baseline_inputs)
        collection, index, key = path
        changed[collection][index][key] = replacement
        assert canonical_document_digest(
            build_contract_canonical_document_v2(**changed)
        ) != baseline

    for field, replacement in (
        ("signing_mode", "single_party"),
        ("supersedes_revision_id", str(uuid4())),
        ("effective_from", "2026-08-30T08:00:00+00:00"),
        ("effective_until", "2026-09-29T08:00:00+00:00"),
    ):
        changed = deepcopy(baseline_inputs)
        changed[field] = replacement
        assert canonical_document_digest(
            build_contract_canonical_document_v2(**changed)
        ) != baseline


def test_security_decision_is_fail_closed_and_digest_is_stable() -> None:
    checked_at = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
    base = {
        "stage": "display",
        "revision_id": str(uuid4()),
        "content_digest": "sha256:" + "c" * 64,
        "summary": {"purpose_code": "model_validation", "run_count": 1},
    }
    pass_check = {"code": "terms_integrity", "result": "PASS", "message": "ok"}
    pending_check = {
        "code": "signature_binding",
        "result": "PENDING",
        "message": "waiting",
    }
    blocked_check = {
        "code": "execution_binding",
        "result": "BLOCKER",
        "message": "offline",
    }

    pending = build_contract_security_decision(
        **base, checks=[pass_check, pending_check], checked_at=checked_at
    )
    blocked = build_contract_security_decision(
        **base,
        checks=[pass_check, pending_check, blocked_check],
        checked_at=checked_at,
    )
    later = build_contract_security_decision(
        **base,
        checks=[pass_check, pending_check],
        checked_at=checked_at.replace(hour=9),
    )

    assert pending["overall"] == "PENDING"
    assert blocked["overall"] == "BLOCKER"
    assert pending["snapshot_digest"] == later["snapshot_digest"]
    assert pending["decision_id"] == later["decision_id"]
