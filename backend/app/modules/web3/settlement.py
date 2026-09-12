from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import re
from typing import Any, Mapping
from uuid import UUID

from app.modules.audit import canonical_json_digest_v1


SETTLEMENT_PROOF_SCHEMA_VERSION = "medtrust.web3.settlement-proof/v2"

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_FIELDS = (
    "order_id",
    "contract_id",
    "contract_revision_id",
    "run_id",
    "artifact_id",
    "package_id",
)
_DIGEST_FIELDS = (
    "quote_digest",
    "contract_content_digest",
    "entitlement_digest",
    "package_digest",
    "review_evidence_digest",
)
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        *_ID_FIELDS,
        *_DIGEST_FIELDS,
        "deadline",
        "nonce",
    }
)
_UINT64_MAX = (1 << 64) - 1
_UINT256_MAX = (1 << 256) - 1


class SettlementProofError(ValueError):
    """Raised when a settlement proof is incomplete, stale or inconsistent."""


def _now_epoch_seconds(now: datetime | None) -> int:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise SettlementProofError("now must be timezone-aware")
    return int(value.astimezone(timezone.utc).timestamp())


def _deadline_epoch_seconds(value: datetime | int) -> int:
    if isinstance(value, bool):
        raise SettlementProofError("deadline must be a UTC timestamp")
    if isinstance(value, int):
        result = value
    elif isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise SettlementProofError("deadline must be timezone-aware")
        result = int(value.astimezone(timezone.utc).timestamp())
    else:
        raise SettlementProofError("deadline must be a UTC timestamp")
    if not 0 < result <= _UINT64_MAX:
        raise SettlementProofError("deadline is outside the uint64 range")
    return result


def _uuid_text(value: UUID | str, field_name: str) -> str:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SettlementProofError(f"{field_name} must be a UUID") from exc
    if parsed.int == 0:
        raise SettlementProofError(f"{field_name} cannot be the nil UUID")
    return str(parsed)


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise SettlementProofError(
            f"{field_name} must be sha256:<64 lowercase hex>"
        )
    return value


def _nonce(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettlementProofError("nonce must be a uint256 integer")
    if not 0 <= value <= _UINT256_MAX:
        raise SettlementProofError("nonce is outside the uint256 range")
    return value


@dataclass(frozen=True, slots=True)
class SettlementProof:
    """Canonical, off-chain evidence committed to an on-chain escrow action.

    Only stable identifiers, content digests, a deadline and a replay nonce are
    included.  Object-store locations, download tokens and medical content must
    never be placed in this proof.
    """

    order_id: str
    contract_id: str
    contract_revision_id: str
    run_id: str
    artifact_id: str
    package_id: str
    quote_digest: str
    contract_content_digest: str
    entitlement_digest: str
    package_digest: str
    review_evidence_digest: str
    deadline: int
    nonce: int

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SETTLEMENT_PROOF_SCHEMA_VERSION,
            "order_id": self.order_id,
            "contract_id": self.contract_id,
            "contract_revision_id": self.contract_revision_id,
            "run_id": self.run_id,
            "artifact_id": self.artifact_id,
            "package_id": self.package_id,
            "quote_digest": self.quote_digest,
            "contract_content_digest": self.contract_content_digest,
            "entitlement_digest": self.entitlement_digest,
            "package_digest": self.package_digest,
            "review_evidence_digest": self.review_evidence_digest,
            "deadline": self.deadline,
            "nonce": self.nonce,
        }

    @property
    def digest(self) -> str:
        return canonical_json_digest_v1(self.to_document())


def build_settlement_proof(
    *,
    order_id: UUID | str,
    contract_id: UUID | str,
    contract_revision_id: UUID | str,
    run_id: UUID | str,
    artifact_id: UUID | str,
    package_id: UUID | str,
    quote_digest: str,
    contract_content_digest: str,
    entitlement_digest: str,
    package_digest: str,
    review_evidence_digest: str,
    deadline: datetime | int,
    nonce: int,
    now: datetime | None = None,
) -> SettlementProof:
    """Build and fail-closed validate one canonical settlement proof."""

    deadline_epoch = _deadline_epoch_seconds(deadline)
    if deadline_epoch <= _now_epoch_seconds(now):
        raise SettlementProofError("settlement proof has expired")
    return SettlementProof(
        order_id=_uuid_text(order_id, "order_id"),
        contract_id=_uuid_text(contract_id, "contract_id"),
        contract_revision_id=_uuid_text(
            contract_revision_id, "contract_revision_id"
        ),
        run_id=_uuid_text(run_id, "run_id"),
        artifact_id=_uuid_text(artifact_id, "artifact_id"),
        package_id=_uuid_text(package_id, "package_id"),
        quote_digest=_digest(quote_digest, "quote_digest"),
        contract_content_digest=_digest(
            contract_content_digest, "contract_content_digest"
        ),
        entitlement_digest=_digest(entitlement_digest, "entitlement_digest"),
        package_digest=_digest(package_digest, "package_digest"),
        review_evidence_digest=_digest(
            review_evidence_digest, "review_evidence_digest"
        ),
        deadline=deadline_epoch,
        nonce=_nonce(nonce),
    )


def validate_settlement_proof(
    document: Mapping[str, object],
    *,
    expected_digest: str | None = None,
    now: datetime | None = None,
) -> SettlementProof:
    """Parse a fixed-shape document and optionally verify its expected digest."""

    if not isinstance(document, Mapping):
        raise SettlementProofError("settlement proof must be an object")
    keys = frozenset(document)
    missing = sorted(_REQUIRED_FIELDS - keys)
    unexpected = sorted(keys - _REQUIRED_FIELDS)
    if missing:
        raise SettlementProofError(
            "settlement proof is missing required fields: " + ", ".join(missing)
        )
    if unexpected:
        raise SettlementProofError(
            "settlement proof contains unexpected fields: " + ", ".join(unexpected)
        )
    if document["schema_version"] != SETTLEMENT_PROOF_SCHEMA_VERSION:
        raise SettlementProofError("unsupported settlement proof schema version")

    proof = build_settlement_proof(
        order_id=document["order_id"],
        contract_id=document["contract_id"],
        contract_revision_id=document["contract_revision_id"],
        run_id=document["run_id"],
        artifact_id=document["artifact_id"],
        package_id=document["package_id"],
        quote_digest=document["quote_digest"],
        contract_content_digest=document["contract_content_digest"],
        entitlement_digest=document["entitlement_digest"],
        package_digest=document["package_digest"],
        review_evidence_digest=document["review_evidence_digest"],
        deadline=document["deadline"],
        nonce=document["nonce"],
        now=now,
    )
    if expected_digest is not None:
        normalized_expected = _digest(expected_digest, "expected_digest")
        if not hmac.compare_digest(proof.digest, normalized_expected):
            raise SettlementProofError("settlement proof digest mismatch")
    return proof
