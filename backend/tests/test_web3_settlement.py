from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.modules.web3.settlement import (
    SETTLEMENT_PROOF_SCHEMA_VERSION,
    SettlementProofError,
    build_settlement_proof,
    validate_settlement_proof,
)


NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


def _inputs() -> dict[str, object]:
    return {
        "order_id": UUID("11111111-1111-4111-8111-111111111111"),
        "contract_id": UUID("22222222-2222-4222-8222-222222222222"),
        "contract_revision_id": UUID("33333333-3333-4333-8333-333333333333"),
        "run_id": UUID("44444444-4444-4444-8444-444444444444"),
        "artifact_id": UUID("55555555-5555-4555-8555-555555555555"),
        "package_id": UUID("66666666-6666-4666-8666-666666666666"),
        "quote_digest": "sha256:" + "1" * 64,
        "contract_content_digest": "sha256:" + "5" * 64,
        "entitlement_digest": "sha256:" + "2" * 64,
        "package_digest": "sha256:" + "3" * 64,
        "review_evidence_digest": "sha256:" + "4" * 64,
        "deadline": NOW + timedelta(hours=1),
        "nonce": 7,
        "now": NOW,
    }


def _document() -> dict[str, object]:
    return build_settlement_proof(**_inputs()).to_document()


def test_settlement_proof_is_canonical_and_deterministic() -> None:
    first = build_settlement_proof(**_inputs())
    second_inputs = _inputs()
    for field in (
        "order_id",
        "contract_id",
        "contract_revision_id",
        "run_id",
        "artifact_id",
        "package_id",
    ):
        second_inputs[field] = str(second_inputs[field]).upper()
    second = build_settlement_proof(**second_inputs)

    assert first.to_document() == second.to_document()
    assert first.digest == second.digest
    assert first.to_document() == {
        "schema_version": SETTLEMENT_PROOF_SCHEMA_VERSION,
        "order_id": "11111111-1111-4111-8111-111111111111",
        "contract_id": "22222222-2222-4222-8222-222222222222",
        "contract_revision_id": "33333333-3333-4333-8333-333333333333",
        "run_id": "44444444-4444-4444-8444-444444444444",
        "artifact_id": "55555555-5555-4555-8555-555555555555",
        "package_id": "66666666-6666-4666-8666-666666666666",
        "quote_digest": "sha256:" + "1" * 64,
        "contract_content_digest": "sha256:" + "5" * 64,
        "entitlement_digest": "sha256:" + "2" * 64,
        "package_digest": "sha256:" + "3" * 64,
        "review_evidence_digest": "sha256:" + "4" * 64,
        "deadline": int((NOW + timedelta(hours=1)).timestamp()),
        "nonce": 7,
    }


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("order_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("contract_id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("contract_revision_id", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        ("run_id", "dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        ("artifact_id", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        ("package_id", "ffffffff-ffff-4fff-8fff-ffffffffffff"),
        ("quote_digest", "sha256:" + "a" * 64),
        ("contract_content_digest", "sha256:" + "e" * 64),
        ("entitlement_digest", "sha256:" + "b" * 64),
        ("package_digest", "sha256:" + "c" * 64),
        ("review_evidence_digest", "sha256:" + "d" * 64),
        ("deadline", int((NOW + timedelta(hours=2)).timestamp())),
        ("nonce", 8),
    ],
)
def test_every_bound_field_is_tamper_sensitive(
    field: str, replacement: object
) -> None:
    baseline = _document()
    changed = deepcopy(baseline)
    changed[field] = replacement

    changed_proof = validate_settlement_proof(changed, now=NOW)
    assert changed_proof.digest != validate_settlement_proof(
        baseline, now=NOW
    ).digest
    with pytest.raises(SettlementProofError, match="digest mismatch"):
        validate_settlement_proof(
            changed,
            expected_digest=validate_settlement_proof(
                baseline, now=NOW
            ).digest,
            now=NOW,
        )


def test_missing_and_unexpected_fields_fail_closed() -> None:
    missing = _document()
    missing.pop("package_id")
    with pytest.raises(SettlementProofError, match="missing required fields: package_id"):
        validate_settlement_proof(missing, now=NOW)

    unexpected = _document()
    unexpected["object_storage_url"] = "https://example.invalid/private.zip"
    with pytest.raises(SettlementProofError, match="unexpected fields"):
        validate_settlement_proof(unexpected, now=NOW)


@pytest.mark.parametrize(
    "field",
    [
        "quote_digest",
        "contract_content_digest",
        "entitlement_digest",
        "package_digest",
        "review_evidence_digest",
    ],
)
def test_non_sha256_digest_is_rejected(field: str) -> None:
    document = _document()
    document[field] = "sha256:" + "A" * 64
    with pytest.raises(SettlementProofError, match="64 lowercase hex"):
        validate_settlement_proof(document, now=NOW)


def test_expired_proof_is_rejected() -> None:
    document = _document()
    document["deadline"] = int(NOW.timestamp())
    with pytest.raises(SettlementProofError, match="expired"):
        validate_settlement_proof(document, now=NOW)


def test_digest_validation_accepts_exact_digest_and_rejects_malformed_digest() -> None:
    proof = build_settlement_proof(**_inputs())
    assert (
        validate_settlement_proof(
            proof.to_document(), expected_digest=proof.digest, now=NOW
        )
        == proof
    )
    with pytest.raises(SettlementProofError, match="expected_digest"):
        validate_settlement_proof(
            proof.to_document(), expected_digest="not-a-digest", now=NOW
        )


def test_naive_deadline_and_boolean_nonce_are_rejected() -> None:
    inputs = _inputs()
    inputs["deadline"] = datetime(2026, 9, 12, 9, 0)
    with pytest.raises(SettlementProofError, match="timezone-aware"):
        build_settlement_proof(**inputs)

    inputs = _inputs()
    inputs["nonce"] = True
    with pytest.raises(SettlementProofError, match="uint256"):
        build_settlement_proof(**inputs)
