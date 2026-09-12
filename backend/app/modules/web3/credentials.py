"""Canonical role-credential issuance and finalized event application.

The chain stores opaque hashes only.  Wallet ownership, organization status,
membership and role eligibility remain server-side checks performed by
``approve_wallet_binding``; an SBT is evidence of that review, not legal KYC.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import canonical_json_digest_v1
from app.modules.identity.models import User
from app.modules.web3.evm_codec import (
    EVENT_TOPICS,
    EvmCodecError,
    decode_medtrust_event,
    digest_to_bytes32,
    encode_contract_call,
    normalize_address,
    stable_bytes32,
)
from app.modules.web3.identity_service import (
    VerifiedCredentialReceipt,
    approve_wallet_binding,
    binding_scope_digest,
)
from app.modules.web3.models import ChainEventReceipt, WalletIdentityBinding
from app.modules.web3.rpc import ReceiptStatus, ReceiptVerification


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HASH_RE = re.compile(r"0x[0-9a-f]{64}\Z")
_DATA_RE = re.compile(r"0x(?:[0-9a-f]{2})*\Z")
_ZERO_ADDRESS = "0x" + "0" * 40
_UINT64_MAX = 2**64 - 1
MIN_CREDENTIAL_LIFETIME = timedelta(hours=1)
MAX_CREDENTIAL_LIFETIME = timedelta(days=366)

# keccak256 of the exact UTF-8 role labels used by the Solidity contracts.
# These values are intentionally fixed instead of being accepted from clients.
ROLE_HASHES = MappingProxyType(
    {
        "data_requester": "0x4ed76b89a0904b3be2092ca0165f73e258b8d32a08f83e56de2e977def71c9ab",
        "data_provider": "0xb356ad87e296e97224845d86b97fce26123c5ea3eef41d97b092edf5153b40d9",
        "model_provider": "0x0d514e5ddafce1ed34408d04a640c3471a33d3979a3d335490ffc64772059cac",
        "space_operator": "0x2e7ad114562f4d7249fe36ff3c80e664a6c66c10da9a5a0f1703a8f2ba51b512",
    }
)


class CredentialIssuanceError(ValueError):
    """A prepared call, finalized receipt, or local mirror is inconsistent."""


def _fail(message: str) -> NoReturn:
    raise CredentialIssuanceError(message)


@dataclass(frozen=True, slots=True)
class PreparedCredentialIssueCall:
    binding_id: UUID
    chain_id: int
    to: str
    method: str
    arguments: tuple[str | int, ...]
    data: str
    expected_event: str
    role_code: str
    credential_scope_digest: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PreparedCredentialRevokeCall:
    binding_id: UUID
    chain_id: int
    to: str
    method: str
    arguments: tuple[str | int, ...]
    data: str
    expected_event: str
    token_id: str
    role_code: str
    reason: str
    reason_digest: str


@dataclass(frozen=True, slots=True)
class CredentialReceiptApplication:
    chain_event_receipt_id: UUID
    binding_id: UUID
    token_id: str
    binding_status: str
    idempotent_replay: bool


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        _fail(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        # SQLite loses timezone metadata. Persisted production values are UTC.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_expiry(
    expires_at: datetime,
    *,
    now: datetime,
    enforce_minimum: bool,
) -> datetime:
    normalized = _as_utc(expires_at, field_name="expires_at").replace(microsecond=0)
    current = _as_utc(now, field_name="now")
    lifetime = normalized - current
    if lifetime <= timedelta(0):
        _fail("credential expiry must be in the future")
    if enforce_minimum and lifetime < MIN_CREDENTIAL_LIFETIME:
        _fail("credential lifetime must be at least one hour")
    if lifetime > MAX_CREDENTIAL_LIFETIME:
        _fail("credential lifetime cannot exceed 366 days")
    timestamp = int(normalized.timestamp())
    if timestamp <= 0 or timestamp > _UINT64_MAX:
        _fail("credential expiry is outside the uint64 range")
    return normalized


def _binding_values(
    binding: WalletIdentityBinding,
) -> tuple[str, str, str, str, str, str]:
    if not isinstance(binding, WalletIdentityBinding):
        _fail("a WalletIdentityBinding is required")
    if not isinstance(binding.id, UUID) or binding.id.int == 0:
        _fail("binding ID is invalid")
    if not isinstance(binding.organization_id, UUID) or binding.organization_id.int == 0:
        _fail("binding organization is invalid")
    if (
        isinstance(binding.chain_id, bool)
        or not isinstance(binding.chain_id, int)
        or binding.chain_id < 1
        or binding.chain_id >= 2**256
    ):
        _fail("binding chain ID is invalid")
    try:
        holder = normalize_address(binding.wallet_address)
    except EvmCodecError as exc:
        raise CredentialIssuanceError("binding wallet address is invalid") from exc
    if holder == _ZERO_ADDRESS:
        _fail("binding wallet cannot be the zero address")
    if not isinstance(binding.did_uri, str) or not binding.did_uri.startswith("did:"):
        _fail("binding DID is invalid")
    if binding.role_code not in ROLE_HASHES:
        _fail("binding role is not supported by the credential contract")
    if (
        not isinstance(binding.identity_evidence_digest, str)
        or _DIGEST_RE.fullmatch(binding.identity_evidence_digest) is None
    ):
        _fail("binding identity evidence digest is invalid")

    scope_digest = binding_scope_digest(binding)
    if _DIGEST_RE.fullmatch(scope_digest) is None:
        _fail("binding scope digest is invalid")
    return (
        holder,
        stable_bytes32("wallet-did", binding.did_uri),
        stable_bytes32("organization", binding.organization_id),
        ROLE_HASHES[binding.role_code],
        # One digest commits to the complete server-defined binding scope.  No
        # DID, organization, user, or evidence plaintext is put on chain.
        digest_to_bytes32(scope_digest),
        scope_digest,
    )


def _build_issue_call(
    binding: WalletIdentityBinding,
    *,
    credential_contract_address: str,
    expires_at: datetime,
) -> PreparedCredentialIssueCall:
    try:
        contract_address = normalize_address(credential_contract_address)
    except EvmCodecError as exc:
        raise CredentialIssuanceError("credential contract address is invalid") from exc
    if contract_address == _ZERO_ADDRESS:
        _fail("credential contract cannot be the zero address")
    holder, did_hash, organization_digest, role_hash, evidence_digest, scope_digest = (
        _binding_values(binding)
    )
    expiry_timestamp = int(expires_at.timestamp())
    arguments: tuple[str | int, ...] = (
        holder,
        did_hash,
        organization_digest,
        role_hash,
        evidence_digest,
        expiry_timestamp,
    )
    return PreparedCredentialIssueCall(
        binding_id=binding.id,
        chain_id=binding.chain_id,
        to=contract_address,
        method="issue",
        arguments=arguments,
        data=encode_contract_call("issue", arguments),
        expected_event="CredentialIssued",
        role_code=binding.role_code,
        credential_scope_digest=scope_digest,
        expires_at=expires_at,
    )


def prepare_credential_issue(
    binding: WalletIdentityBinding,
    *,
    credential_contract_address: str,
    expires_at: datetime,
    now: datetime | None = None,
) -> PreparedCredentialIssueCall:
    """Build the sole canonical ``issue`` call for a pending reviewed scope."""

    if not isinstance(binding, WalletIdentityBinding) or binding.status != "pending":
        _fail("only a pending wallet binding can be prepared for issuance")
    if any(
        value is not None
        for value in (
            binding.credential_contract_address,
            binding.credential_token_id,
            binding.credential_scope_digest,
            binding.credential_expires_at,
            binding.verified_at,
            binding.verified_by,
        )
    ):
        _fail("pending wallet binding already contains credential state")
    current = _as_utc(now or datetime.now(timezone.utc), field_name="now")
    expiry = _canonical_expiry(expires_at, now=current, enforce_minimum=True)
    return _build_issue_call(
        binding,
        credential_contract_address=credential_contract_address,
        expires_at=expiry,
    )


def prepare_credential_revocation(
    binding: WalletIdentityBinding,
    *,
    credential_contract_address: str,
    reason: str,
) -> PreparedCredentialRevokeCall:
    """Build the canonical on-chain revoke call before changing server state."""

    if not isinstance(binding, WalletIdentityBinding) or binding.status != "active":
        _fail("only an active wallet binding can be revoked")
    return _build_credential_revocation(
        binding,
        credential_contract_address=credential_contract_address,
        reason=reason,
    )


def reconstruct_credential_revocation(
    binding: WalletIdentityBinding,
    *,
    credential_contract_address: str,
    reason: str,
) -> PreparedCredentialRevokeCall:
    """Rebuild an already-applied revocation solely for idempotency checks."""

    if not isinstance(binding, WalletIdentityBinding) or binding.status != "revoked":
        _fail("only a revoked wallet binding can reconstruct a revocation")
    return _build_credential_revocation(
        binding,
        credential_contract_address=credential_contract_address,
        reason=reason,
    )


def _build_credential_revocation(
    binding: WalletIdentityBinding,
    *,
    credential_contract_address: str,
    reason: str,
) -> PreparedCredentialRevokeCall:
    try:
        contract_address = normalize_address(credential_contract_address)
    except EvmCodecError as exc:
        raise CredentialIssuanceError("credential contract address is invalid") from exc
    if binding.credential_contract_address != contract_address:
        _fail("binding credential contract does not match configuration")
    clean_reason = reason.strip() if isinstance(reason, str) else ""
    if not 3 <= len(clean_reason) <= 500 or any(
        ord(character) < 32 for character in clean_reason
    ):
        _fail("revocation reason must be 3 to 500 characters")
    try:
        token_id = int(binding.credential_token_id or "", 10)
    except ValueError as exc:
        raise CredentialIssuanceError("credential token ID is invalid") from exc
    if token_id < 1 or token_id >= 2**256:
        _fail("credential token ID is invalid")
    if binding.role_code not in ROLE_HASHES:
        _fail("binding role is not supported by the credential contract")
    reason_digest = canonical_json_digest_v1(
        {
            "schema_version": "medtrust.role-credential-revocation-reason/v1",
            "binding_id": str(binding.id),
            "credential_token_id": str(token_id),
            "reason": clean_reason,
        }
    )
    arguments: tuple[str | int, ...] = (
        token_id,
        digest_to_bytes32(reason_digest),
    )
    return PreparedCredentialRevokeCall(
        binding_id=binding.id,
        chain_id=binding.chain_id,
        to=contract_address,
        method="revokeCredential",
        arguments=arguments,
        data=encode_contract_call("revokeCredential", arguments),
        expected_event="CredentialRevoked",
        token_id=str(token_id),
        role_code=binding.role_code,
        reason=clean_reason,
        reason_digest=reason_digest,
    )


def _validate_prepared_call(
    binding: WalletIdentityBinding,
    prepared_call: PreparedCredentialIssueCall,
    *,
    now: datetime,
) -> PreparedCredentialIssueCall:
    if type(prepared_call) is not PreparedCredentialIssueCall:
        _fail("a canonical PreparedCredentialIssueCall is required")
    if prepared_call.binding_id != binding.id:
        _fail("prepared credential call belongs to another binding")
    expiry = _canonical_expiry(
        prepared_call.expires_at,
        now=now,
        enforce_minimum=False,
    )
    expected = _build_issue_call(
        binding,
        credential_contract_address=prepared_call.to,
        expires_at=expiry,
    )
    if prepared_call != expected:
        _fail("prepared credential call is not canonical for the binding")
    return expected


def _validate_frozen_receipt(
    receipt: ReceiptVerification,
    *,
    prepared_call: PreparedCredentialIssueCall,
    minimum_confirmations: int,
) -> None:
    # Exact type plus tuple topics avoids accepting a look-alike object or a
    # frozen wrapper containing mutable event fields.
    if type(receipt) is not ReceiptVerification or type(receipt.event_topics) is not tuple:
        _fail("a frozen canonical ReceiptVerification is required")
    if (
        isinstance(minimum_confirmations, bool)
        or not isinstance(minimum_confirmations, int)
        or minimum_confirmations < 1
    ):
        _fail("minimum confirmations must be a positive integer")
    if (
        receipt.status is not ReceiptStatus.CONFIRMED
        or not receipt.confirmed
        or receipt.chain_id != prepared_call.chain_id
        or receipt.expected_contract_address != prepared_call.to
        or receipt.expected_event_topic != EVENT_TOPICS["CredentialIssued"]
        or receipt.minimum_confirmations != minimum_confirmations
        or receipt.confirmations < minimum_confirmations
        or receipt.block_number is None
        or receipt.block_number < 0
        or receipt.block_hash is None
        or receipt.head_block_number is None
        or receipt.head_block_number < receipt.block_number
        or receipt.confirmations != receipt.head_block_number - receipt.block_number + 1
        or isinstance(receipt.expected_log_index, bool)
        or receipt.expected_log_index < 0
        or _HASH_RE.fullmatch(receipt.tx_hash) is None
        or _HASH_RE.fullmatch(receipt.block_hash) is None
        or not receipt.event_topics
        or receipt.event_topics[0] != EVENT_TOPICS["CredentialIssued"]
        or any(_HASH_RE.fullmatch(topic) is None for topic in receipt.event_topics)
        or receipt.event_data is None
        or _DATA_RE.fullmatch(receipt.event_data) is None
    ):
        _fail("receipt is not the expected finalized canonical credential event")


def _decode_and_match(
    receipt: ReceiptVerification,
    prepared_call: PreparedCredentialIssueCall,
    *,
    issuer_address: str,
) -> tuple[str, dict[str, str | int]]:
    try:
        decoded = decode_medtrust_event(
            "CredentialIssued",
            receipt.event_topics,
            receipt.event_data or "",
        )
    except EvmCodecError as exc:
        raise CredentialIssuanceError("CredentialIssued log does not match its ABI") from exc
    holder, did_hash, organization_digest, role_hash, evidence_digest, expiry = (
        prepared_call.arguments
    )
    token_id = decoded.values.get("token_id")
    if isinstance(token_id, bool) or not isinstance(token_id, int) or token_id < 1:
        _fail("CredentialIssued token ID must be a positive uint256")
    expected = {
        "holder": holder,
        "issuer": issuer_address,
        "did_hash": did_hash,
        "organization_digest": organization_digest,
        "role": role_hash,
        "evidence_digest": evidence_digest,
        "expires_at": expiry,
    }
    if any(decoded.values.get(key) != value for key, value in expected.items()):
        _fail("CredentialIssued log belongs to another binding or credential scope")
    return str(token_id), dict(decoded.values)


def _rfc3339_utc(value: datetime) -> str:
    return _as_utc(value, field_name="credential expiry").isoformat().replace(
        "+00:00", "Z"
    )


def _receipt_payload(
    binding: WalletIdentityBinding,
    prepared_call: PreparedCredentialIssueCall,
    *,
    token_id: str,
    issuer_address: str,
) -> dict[str, str]:
    return {
        "schema_version": "medtrust.role-credential-issued/v1",
        "binding_id": str(binding.id),
        "token_id": token_id,
        "holder_address": str(prepared_call.arguments[0]),
        "issuer_address": issuer_address,
        "role_code": binding.role_code,
        "credential_scope_digest": prepared_call.credential_scope_digest,
        "credential_expires_at": _rfc3339_utc(prepared_call.expires_at),
    }


def _assert_existing_receipt(
    existing: ChainEventReceipt,
    *,
    binding: WalletIdentityBinding,
    prepared_call: PreparedCredentialIssueCall,
    receipt: ReceiptVerification,
    payload: dict[str, str],
    issuer_address: str,
) -> None:
    payload_digest = canonical_json_digest_v1(payload)
    if (
        existing.status not in {"finalized", "applied"}
        or existing.space_id != binding.space_id
        or existing.chain_id != receipt.chain_id
        or existing.contract_address != prepared_call.to
        or existing.transaction_hash != receipt.tx_hash
        or existing.log_index != receipt.expected_log_index
        or existing.block_number != receipt.block_number
        or existing.block_hash != receipt.block_hash
        or existing.event_name != "CredentialIssued"
        or existing.subject_type != "wallet_identity_binding"
        or existing.subject_key != str(binding.id)
        or existing.actor_wallet != issuer_address
        or existing.payload_snapshot != payload
        or not hmac.compare_digest(existing.payload_digest, payload_digest)
        or existing.confirmations < 1
        or existing.finalized_at is None
        or (existing.status == "applied" and existing.applied_at is None)
        or (existing.status == "finalized" and existing.applied_at is not None)
    ):
        _fail("stored chain receipt conflicts with canonical CredentialIssued event")


def _assert_applied_binding(
    binding: WalletIdentityBinding,
    *,
    prepared_call: PreparedCredentialIssueCall,
    token_id: str,
) -> None:
    if (
        binding.status not in {"active", "revoked"}
        or binding.credential_contract_address != prepared_call.to
        or binding.credential_token_id != token_id
        or binding.credential_scope_digest != prepared_call.credential_scope_digest
        or binding.credential_expires_at is None
        or _as_utc(
            binding.credential_expires_at,
            field_name="stored credential expiry",
        )
        != prepared_call.expires_at
        or binding.verified_at is None
        or binding.verified_by is None
    ):
        _fail("applied credential receipt did not produce the canonical binding mirror")


async def apply_credential_issued_receipt(
    session: AsyncSession,
    *,
    binding_id: UUID,
    prepared_call: PreparedCredentialIssueCall,
    operator_user: User,
    issuer_address: str,
    receipt: ReceiptVerification,
    minimum_confirmations: int,
    applied_at: datetime | None = None,
) -> CredentialReceiptApplication:
    """Persist and apply one finality-verified ``CredentialIssued`` log.

    The caller must keep this function inside the same database transaction;
    it flushes the finalized receipt before invoking the existing eligibility
    gate and marks that receipt applied only after the binding is activated.
    """

    at = _as_utc(applied_at or datetime.now(timezone.utc), field_name="applied_at")
    binding = await session.scalar(
        select(WalletIdentityBinding)
        .where(WalletIdentityBinding.id == binding_id)
        .with_for_update()
    )
    if binding is None:
        _fail("wallet binding does not exist")
    try:
        canonical_issuer = normalize_address(issuer_address)
    except EvmCodecError as exc:
        raise CredentialIssuanceError("credential issuer address is invalid") from exc
    if canonical_issuer == _ZERO_ADDRESS:
        _fail("credential issuer cannot be the zero address")
    canonical_call = _validate_prepared_call(binding, prepared_call, now=at)
    _validate_frozen_receipt(
        receipt,
        prepared_call=canonical_call,
        minimum_confirmations=minimum_confirmations,
    )
    if (
        binding.credential_issuance_tx_hash is not None
        and binding.credential_issuance_tx_hash != receipt.tx_hash
    ):
        _fail("credential receipt conflicts with the persisted issuance transaction")
    binding.credential_issuance_tx_hash = receipt.tx_hash
    token_id, _ = _decode_and_match(
        receipt,
        canonical_call,
        issuer_address=canonical_issuer,
    )
    payload = _receipt_payload(
        binding,
        canonical_call,
        token_id=token_id,
        issuer_address=canonical_issuer,
    )

    existing = await session.scalar(
        select(ChainEventReceipt)
        .where(
            ChainEventReceipt.chain_id == receipt.chain_id,
            ChainEventReceipt.transaction_hash == receipt.tx_hash,
            ChainEventReceipt.log_index == receipt.expected_log_index,
        )
        .with_for_update()
    )
    if existing is not None:
        _assert_existing_receipt(
            existing,
            binding=binding,
            prepared_call=canonical_call,
            receipt=receipt,
            payload=payload,
            issuer_address=canonical_issuer,
        )
        existing.confirmations = max(existing.confirmations, receipt.confirmations)
        if existing.status == "applied":
            _assert_applied_binding(
                binding,
                prepared_call=canonical_call,
                token_id=token_id,
            )
            await session.flush()
            return CredentialReceiptApplication(
                chain_event_receipt_id=existing.id,
                binding_id=binding.id,
                token_id=token_id,
                binding_status=binding.status,
                idempotent_replay=True,
            )
        chain_receipt = existing
    else:
        if binding.status != "pending":
            _fail("new credential receipt cannot be applied to a non-pending binding")
        chain_receipt = ChainEventReceipt(
            space_id=binding.space_id,
            chain_id=receipt.chain_id,
            contract_address=canonical_call.to,
            transaction_hash=receipt.tx_hash,
            log_index=receipt.expected_log_index,
            block_number=receipt.block_number,
            block_hash=receipt.block_hash,
            event_name="CredentialIssued",
            subject_type="wallet_identity_binding",
            subject_key=str(binding.id),
            actor_wallet=canonical_issuer,
            payload_snapshot=payload,
            payload_digest=canonical_json_digest_v1(payload),
            confirmations=receipt.confirmations,
            status="finalized",
            first_seen_at=at,
            finalized_at=at,
        )
        session.add(chain_receipt)
        await session.flush()

    if binding.status != "pending":
        _fail("finalized credential receipt cannot reactivate this binding")
    verified = VerifiedCredentialReceipt(
        binding_id=binding.id,
        chain_id=receipt.chain_id,
        contract_address=canonical_call.to,
        token_id=token_id,
        holder_address=str(canonical_call.arguments[0]),
        issuer_address=canonical_issuer,
        role_code=binding.role_code,
        credential_scope_digest=canonical_call.credential_scope_digest,
        credential_expires_at=canonical_call.expires_at,
        transaction_hash=receipt.tx_hash,
        block_number=receipt.block_number,
        block_hash=receipt.block_hash,
        log_index=receipt.expected_log_index,
        confirmations=receipt.confirmations,
        event_name="CredentialIssued",
        status="finalized",
    )
    activated = await approve_wallet_binding(
        session,
        binding_id=binding.id,
        operator_user=operator_user,
        receipt=verified,
        minimum_confirmations=minimum_confirmations,
        now=at,
    )
    chain_receipt.status = "applied"
    chain_receipt.applied_at = at
    await session.flush()
    return CredentialReceiptApplication(
        chain_event_receipt_id=chain_receipt.id,
        binding_id=activated.id,
        token_id=token_id,
        binding_status=activated.status,
        idempotent_replay=False,
    )
