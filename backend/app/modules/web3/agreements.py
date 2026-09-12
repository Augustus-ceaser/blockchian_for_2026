"""Fail-closed Phase 5.4 orchestration for the agreement registry.

This module deliberately does not perform JSON-RPC calls.  A controller must
first call :func:`app.modules.web3.rpc.verify_transaction_receipt` with a
server-configured transport and pass the returned frozen object here.  The
functions below only flush; the caller owns the surrounding database
transaction and must commit or roll it back as one unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import AuditCommandContext
from app.modules.contracts.models import (
    Contract,
    ContractParty,
    ContractRevision,
    ContractSignature,
)
from app.modules.contracts.services import (
    activate_contract_revision,
    canonical_document_digest,
    sign_verified_evm_contract_receipt,
)
from app.modules.web3.evm_codec import (
    EVENT_TOPICS,
    decode_medtrust_event,
    digest_to_bytes32,
    encode_contract_call,
    normalize_address,
    stable_bytes32,
)
from app.modules.web3.models import (
    ChainEventReceipt,
    ContractChainAnchor,
    WalletIdentityBinding,
)
from app.modules.web3.rpc import ReceiptStatus, ReceiptVerification


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HASH_RE = re.compile(r"0x[0-9a-f]{64}\Z")
_EVENT_NAMES = {
    "AgreementRegistered",
    "AgreementConfirmed",
    "AgreementActivated",
}
_PARTY_ORDER = (
    "data_requester",
    "data_provider",
    "model_provider",
    "operator_witness",
)
_PARTY_TO_WALLET_ROLE = {
    "data_requester": "data_requester",
    "data_provider": "data_provider",
    "model_provider": "model_provider",
    "operator_witness": "space_operator",
}
_PARTY_BITS = {
    "data_requester": 1,
    "data_provider": 2,
    "model_provider": 4,
    "operator_witness": 8,
}


class AgreementOrchestrationError(ValueError):
    """The chain event or current MedTrust state cannot authorize the action."""


@dataclass(frozen=True, slots=True)
class PreparedAgreementCall:
    chain_id: int
    to: str
    method: str
    arguments: tuple[str | int, ...]
    data: str
    expected_event: str
    from_wallet: str | None = None
    needs_submission: bool = True


@dataclass(frozen=True, slots=True)
class AgreementAnchorPreparation:
    anchor_id: UUID
    agreement_key: str
    content_digest: str
    participant_wallets: dict[str, str]
    call: PreparedAgreementCall


@dataclass(frozen=True, slots=True)
class AgreementConfirmationPreparation:
    anchor_id: UUID
    contract_party_id: UUID
    wallet_binding_id: UUID
    party_role: str
    expected_confirmation_bitmap: int
    call: PreparedAgreementCall


@dataclass(frozen=True, slots=True)
class AgreementReceiptApplication:
    chain_event_receipt_id: UUID
    event_name: str
    anchor_status: str
    confirmation_bitmap: int
    idempotent_replay: bool
    contract_signature_id: UUID | None = None


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _now(value: datetime | None) -> datetime:
    return _utc(value or datetime.now(timezone.utc))


def _uint64_timestamp(value: datetime | None, field_name: str) -> int:
    if value is None:
        raise AgreementOrchestrationError(f"{field_name} is required on-chain")
    timestamp = int(_utc(value).timestamp())
    if not 0 <= timestamp < 2**64:
        raise AgreementOrchestrationError(f"{field_name} is outside uint64 range")
    return timestamp


def _require_digest(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise AgreementOrchestrationError(
            f"{field_name} must be sha256:<64 lowercase hex>"
        )
    return value


def _party_role(binding_role: str) -> str | None:
    return (
        "operator_witness" if binding_role == "space_operator" else binding_role
    ) if binding_role in set(_PARTY_TO_WALLET_ROLE.values()) else None


def _validate_revision(revision: ContractRevision) -> None:
    if revision.status != "proposed":
        raise AgreementOrchestrationError("only a proposed revision can be anchored")
    if revision.signing_mode != "multi_party":
        raise AgreementOrchestrationError("on-chain confirmation requires multi_party mode")
    _require_digest(revision.content_digest, "content_digest")
    valid_from = _uint64_timestamp(revision.effective_from, "effective_from")
    valid_until = _uint64_timestamp(revision.effective_until, "effective_until")
    if valid_until <= valid_from:
        raise AgreementOrchestrationError("effective_until must be after effective_from")


def _validate_party_set(parties: list[ContractParty]) -> dict[str, ContractParty]:
    required = [party for party in parties if party.is_required]
    if (
        len(parties) != 4
        or len(required) != 4
        or {party.party_role for party in required} != set(_PARTY_ORDER)
    ):
        raise AgreementOrchestrationError(
            "the chain agreement requires exactly the four fixed required parties"
        )
    by_role = {party.party_role: party for party in required}
    operator_order = by_role["operator_witness"].signing_order
    if any(
        party.party_role != "operator_witness" and party.signing_order >= operator_order
        for party in required
    ):
        raise AgreementOrchestrationError("the operator witness must have the last signing order")
    return by_role


def _validate_binding(
    binding: WalletIdentityBinding,
    *,
    party: ContractParty,
    space_id: UUID,
    chain_id: int,
    credential_contract_address: str,
    at: datetime,
) -> None:
    expected_role = _PARTY_TO_WALLET_ROLE[party.party_role]
    snapshot_scope = (
        party.identity_snapshot.get("credential_scope_digest")
        if isinstance(party.identity_snapshot, dict)
        else None
    )
    expires_at = (
        _utc(binding.credential_expires_at)
        if binding.credential_expires_at is not None
        else None
    )
    if (
        binding.status != "active"
        or binding.space_id != space_id
        or binding.organization_id != party.organization_id
        or binding.role_code != expected_role
        or binding.chain_id != chain_id
        or normalize_address(binding.wallet_address) != binding.wallet_address
        or binding.credential_contract_address != credential_contract_address
        or binding.credential_token_id is None
        or binding.credential_scope_digest is None
        or expires_at is None
        or expires_at <= at
        or (snapshot_scope is not None and snapshot_scope != binding.credential_scope_digest)
    ):
        raise AgreementOrchestrationError(
            f"party {party.party_role} lacks the exact active wallet credential"
        )


def _select_party_bindings(
    *,
    parties: dict[str, ContractParty],
    bindings: list[WalletIdentityBinding],
    space_id: UUID,
    chain_id: int,
    credential_contract_address: str,
    at: datetime,
) -> dict[str, WalletIdentityBinding]:
    selected: dict[str, WalletIdentityBinding] = {}
    for role in _PARTY_ORDER:
        party = parties[role]
        candidates = [
            binding
            for binding in bindings
            if binding.organization_id == party.organization_id
            and binding.role_code == _PARTY_TO_WALLET_ROLE[role]
        ]
        snapshot_binding_id = (
            party.identity_snapshot.get("wallet_binding_id")
            if isinstance(party.identity_snapshot, dict)
            else None
        )
        if snapshot_binding_id is not None:
            candidates = [
                binding for binding in candidates if str(binding.id) == snapshot_binding_id
            ]
        if len(candidates) != 1:
            raise AgreementOrchestrationError(
                f"party {role} must resolve to exactly one wallet binding"
            )
        binding = candidates[0]
        _validate_binding(
            binding,
            party=party,
            space_id=space_id,
            chain_id=chain_id,
            credential_contract_address=credential_contract_address,
            at=at,
        )
        selected[role] = binding
    wallets = [binding.wallet_address for binding in selected.values()]
    if len(set(wallets)) != 4:
        raise AgreementOrchestrationError("each agreement party needs a distinct wallet")
    return selected


async def _load_contract_graph(
    session: AsyncSession, contract_revision_id: UUID
) -> tuple[ContractRevision, Contract, list[ContractParty]]:
    revision = await session.get(ContractRevision, contract_revision_id)
    if revision is None:
        raise AgreementOrchestrationError("contract revision does not exist")
    contract = await session.get(Contract, revision.contract_id)
    if contract is None:
        raise AgreementOrchestrationError("revision contract does not exist")
    parties = list(
        (
            await session.scalars(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    return revision, contract, parties


async def prepare_agreement_anchor(
    session: AsyncSession,
    *,
    contract_revision_id: UUID,
    chain_id: int,
    registry_address: str,
    credential_contract_address: str,
    prepared_by_user_id: UUID,
    expected_space_id: UUID | None = None,
    prepared_at: datetime | None = None,
) -> AgreementAnchorPreparation:
    """Create/reuse an anchor and return deterministic registration calldata."""

    if isinstance(chain_id, bool) or not isinstance(chain_id, int) or chain_id < 1:
        raise AgreementOrchestrationError("chain_id must be a positive integer")
    registry = normalize_address(registry_address)
    credential_contract = normalize_address(credential_contract_address)
    at = _now(prepared_at)
    revision, contract, party_rows = await _load_contract_graph(
        session, contract_revision_id
    )
    if expected_space_id is not None and contract.space_id != expected_space_id:
        raise AgreementOrchestrationError("contract revision belongs to another space")
    _validate_revision(revision)
    parties = _validate_party_set(party_rows)
    bindings = list(
        (
            await session.scalars(
                select(WalletIdentityBinding).where(
                    WalletIdentityBinding.space_id == contract.space_id,
                    WalletIdentityBinding.chain_id == chain_id,
                    WalletIdentityBinding.status == "active",
                    WalletIdentityBinding.organization_id.in_(
                        [party.organization_id for party in parties.values()]
                    ),
                )
            )
        ).all()
    )
    selected = _select_party_bindings(
        parties=parties,
        bindings=bindings,
        space_id=contract.space_id,
        chain_id=chain_id,
        credential_contract_address=credential_contract,
        at=at,
    )
    if selected["operator_witness"].user_id != prepared_by_user_id:
        raise AgreementOrchestrationError(
            "only the designated operator wallet user can prepare the anchor"
        )
    agreement_key = stable_bytes32("contract-revision", revision.id)
    digest = _require_digest(revision.content_digest, "content_digest")
    valid_from = _uint64_timestamp(revision.effective_from, "effective_from")
    valid_until = _uint64_timestamp(revision.effective_until, "effective_until")
    if valid_until <= int(at.timestamp()):
        raise AgreementOrchestrationError("agreement effective window has ended")
    arguments: tuple[str | int, ...] = (
        agreement_key,
        digest_to_bytes32(digest),
        selected["data_requester"].wallet_address,
        selected["data_provider"].wallet_address,
        selected["model_provider"].wallet_address,
        selected["operator_witness"].wallet_address,
        valid_from,
        valid_until,
    )

    anchor = await session.scalar(
        select(ContractChainAnchor).where(
            ContractChainAnchor.contract_revision_id == revision.id
        )
    )
    conflicting_signature_id = await session.scalar(
        select(ContractSignature.id).where(
            ContractSignature.contract_revision_id == revision.id,
            (
                ContractSignature.signature_type != "evm_receipt"
                if anchor is not None
                else ContractSignature.id.is_not(None)
            ),
        )
    )
    if conflicting_signature_id is not None:
        raise AgreementOrchestrationError(
            "contract revision already uses a different signature flow"
        )
    if anchor is None:
        anchor = ContractChainAnchor(
            space_id=contract.space_id,
            contract_id=contract.id,
            contract_revision_id=revision.id,
            chain_id=chain_id,
            registry_address=registry,
            agreement_key=agreement_key,
            content_digest=digest,
            status="prepared",
            required_confirmation_bitmap=15,
            confirmation_bitmap=0,
            prepared_by=prepared_by_user_id,
            created_at=at,
            updated_at=at,
        )
        session.add(anchor)
        await session.flush()
    elif (
        anchor.space_id != contract.space_id
        or anchor.contract_id != contract.id
        or anchor.chain_id != chain_id
        or anchor.registry_address != registry
        or anchor.agreement_key != agreement_key
        or anchor.content_digest != digest
        or anchor.required_confirmation_bitmap != 15
    ):
        raise AgreementOrchestrationError("existing chain anchor conflicts with revision")

    call = PreparedAgreementCall(
        chain_id=chain_id,
        to=registry,
        method="registerAgreement",
        arguments=arguments,
        data=encode_contract_call("registerAgreement", arguments),
        expected_event="AgreementRegistered",
        from_wallet=selected["operator_witness"].wallet_address,
        needs_submission=anchor.status == "prepared",
    )
    return AgreementAnchorPreparation(
        anchor_id=anchor.id,
        agreement_key=agreement_key,
        content_digest=digest,
        participant_wallets={
            role: selected[role].wallet_address for role in _PARTY_ORDER
        },
        call=call,
    )


async def prepare_agreement_confirmation(
    session: AsyncSession,
    *,
    chain_anchor_id: UUID,
    current_user_id: UUID,
    credential_contract_address: str,
    expected_space_id: UUID | None = None,
    prepared_at: datetime | None = None,
) -> AgreementConfirmationPreparation:
    """Return confirmation calldata only for the authenticated user's binding."""

    credential_contract = normalize_address(credential_contract_address)
    at = _now(prepared_at)
    anchor = await session.get(ContractChainAnchor, chain_anchor_id)
    if anchor is None:
        raise AgreementOrchestrationError("chain anchor does not exist")
    if expected_space_id is not None and anchor.space_id != expected_space_id:
        raise AgreementOrchestrationError("chain anchor belongs to another space")
    if anchor.status not in {"registered", "confirming"}:
        raise AgreementOrchestrationError("agreement is not open for confirmations")
    revision, contract, party_rows = await _load_contract_graph(
        session, anchor.contract_revision_id
    )
    _validate_revision(revision)
    if _uint64_timestamp(revision.effective_until, "effective_until") <= int(
        at.timestamp()
    ):
        raise AgreementOrchestrationError("agreement effective window has ended")
    parties = _validate_party_set(party_rows)
    candidates = list(
        (
            await session.scalars(
                select(WalletIdentityBinding).where(
                    WalletIdentityBinding.space_id == contract.space_id,
                    WalletIdentityBinding.user_id == current_user_id,
                    WalletIdentityBinding.chain_id == anchor.chain_id,
                    WalletIdentityBinding.status == "active",
                )
            )
        ).all()
    )
    matches: list[tuple[ContractParty, WalletIdentityBinding]] = []
    for binding in candidates:
        role = _party_role(binding.role_code)
        party = parties.get(role or "")
        if party is None or party.organization_id != binding.organization_id:
            continue
        _validate_binding(
            binding,
            party=party,
            space_id=contract.space_id,
            chain_id=anchor.chain_id,
            credential_contract_address=credential_contract,
            at=at,
        )
        matches.append((party, binding))
    if len(matches) != 1:
        raise AgreementOrchestrationError(
            "current user must resolve to exactly one required agreement party"
        )
    party, binding = matches[0]
    bit = _PARTY_BITS[party.party_role]
    if anchor.confirmation_bitmap & bit:
        raise AgreementOrchestrationError("this party has already confirmed")
    if party.party_role == "operator_witness" and anchor.confirmation_bitmap != 7:
        raise AgreementOrchestrationError("the operator must confirm after the other parties")
    expected_bitmap = anchor.confirmation_bitmap | bit
    arguments: tuple[str | int, ...] = (anchor.agreement_key,)
    return AgreementConfirmationPreparation(
        anchor_id=anchor.id,
        contract_party_id=party.id,
        wallet_binding_id=binding.id,
        party_role=party.party_role,
        expected_confirmation_bitmap=expected_bitmap,
        call=PreparedAgreementCall(
            chain_id=anchor.chain_id,
            to=anchor.registry_address,
            method="confirm",
            arguments=arguments,
            data=encode_contract_call("confirm", arguments),
            expected_event="AgreementConfirmed",
            from_wallet=binding.wallet_address,
        ),
    )


async def prepare_agreement_activation(
    session: AsyncSession,
    *,
    chain_anchor_id: UUID,
    expected_space_id: UUID | None = None,
    prepared_at: datetime | None = None,
) -> PreparedAgreementCall:
    """Prepare the permissionless activation call for a future-dated agreement.

    Normally the fourth confirmation activates atomically.  This separate call
    is only needed when all confirmations arrived before ``effective_from``.
    """

    at = _now(prepared_at)
    anchor = await session.get(ContractChainAnchor, chain_anchor_id)
    if anchor is None:
        raise AgreementOrchestrationError("chain anchor does not exist")
    if expected_space_id is not None and anchor.space_id != expected_space_id:
        raise AgreementOrchestrationError("chain anchor belongs to another space")
    if anchor.status != "confirming" or anchor.confirmation_bitmap != 15:
        raise AgreementOrchestrationError("activation requires four confirmations")
    revision = await session.get(ContractRevision, anchor.contract_revision_id)
    if revision is None or revision.status != "signed":
        raise AgreementOrchestrationError("activation requires the signed revision mirror")
    valid_from = _utc(revision.effective_from) if revision.effective_from else None
    valid_until = _utc(revision.effective_until) if revision.effective_until else None
    if valid_from is None or valid_until is None or not valid_from <= at < valid_until:
        raise AgreementOrchestrationError("agreement is outside its effective window")
    arguments: tuple[str | int, ...] = (anchor.agreement_key,)
    return PreparedAgreementCall(
        chain_id=anchor.chain_id,
        to=anchor.registry_address,
        method="activate",
        arguments=arguments,
        data=encode_contract_call("activate", arguments),
        expected_event="AgreementActivated",
    )


def _validate_frozen_receipt(
    *,
    anchor: ContractChainAnchor,
    receipt: ReceiptVerification,
    event_name: str,
    minimum_confirmations: int,
) -> None:
    if not isinstance(receipt, ReceiptVerification):
        raise AgreementOrchestrationError("a canonical ReceiptVerification is required")
    if event_name not in _EVENT_NAMES:
        raise AgreementOrchestrationError("unsupported agreement event")
    if (
        receipt.status is not ReceiptStatus.CONFIRMED
        or not receipt.confirmed
        or receipt.chain_id != anchor.chain_id
        or receipt.expected_contract_address != anchor.registry_address
        or receipt.expected_event_topic != EVENT_TOPICS[event_name]
        or receipt.minimum_confirmations != minimum_confirmations
        or receipt.confirmations < minimum_confirmations
        or receipt.block_number is None
        or receipt.block_hash is None
        or not receipt.event_topics
        or receipt.event_topics[0] != EVENT_TOPICS[event_name]
        or receipt.event_data is None
        or _HASH_RE.fullmatch(receipt.tx_hash) is None
        or _HASH_RE.fullmatch(receipt.block_hash) is None
    ):
        raise AgreementOrchestrationError(
            "receipt is not the expected finalized canonical agreement event"
        )


async def _existing_chain_receipt(
    session: AsyncSession, receipt: ReceiptVerification
) -> ChainEventReceipt | None:
    return await session.scalar(
        select(ChainEventReceipt).where(
            ChainEventReceipt.chain_id == receipt.chain_id,
            ChainEventReceipt.transaction_hash == receipt.tx_hash,
            ChainEventReceipt.log_index == receipt.expected_log_index,
        )
    )


def _assert_existing_receipt(
    existing: ChainEventReceipt,
    *,
    anchor: ContractChainAnchor,
    receipt: ReceiptVerification,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    if (
        existing.status == "orphaned"
        or existing.space_id != anchor.space_id
        or existing.chain_id != receipt.chain_id
        or existing.contract_address != anchor.registry_address
        or existing.transaction_hash != receipt.tx_hash
        or existing.log_index != receipt.expected_log_index
        or existing.block_number != receipt.block_number
        or existing.block_hash != receipt.block_hash
        or existing.event_name != event_name
        or existing.subject_type != "contract_revision"
        or existing.subject_key != str(anchor.contract_revision_id)
        or existing.payload_snapshot != payload
        or existing.payload_digest != canonical_document_digest(payload)
    ):
        raise AgreementOrchestrationError("stored chain receipt conflicts with canonical event")


def _base_payload(
    *,
    anchor: ContractChainAnchor,
    event_name: str,
    decoded_values: dict[str, str | int],
) -> dict[str, Any]:
    return {
        "schema_version": "medtrust.agreement-chain-event/v1",
        "event_name": event_name,
        "contract_revision_id": str(anchor.contract_revision_id),
        "agreement_key": anchor.agreement_key,
        "content_digest": anchor.content_digest,
        "decoded": decoded_values,
    }


def _new_chain_receipt(
    *,
    anchor: ContractChainAnchor,
    receipt: ReceiptVerification,
    event_name: str,
    actor_wallet: str | None,
    payload: dict[str, Any],
    at: datetime,
) -> ChainEventReceipt:
    return ChainEventReceipt(
        space_id=anchor.space_id,
        chain_id=receipt.chain_id,
        contract_address=anchor.registry_address,
        transaction_hash=receipt.tx_hash,
        log_index=receipt.expected_log_index,
        block_number=receipt.block_number,
        block_hash=receipt.block_hash,
        event_name=event_name,
        subject_type="contract_revision",
        subject_key=str(anchor.contract_revision_id),
        actor_wallet=actor_wallet,
        payload_snapshot=payload,
        payload_digest=canonical_document_digest(payload),
        confirmations=receipt.confirmations,
        status="finalized",
        first_seen_at=at,
        finalized_at=at,
    )


async def apply_agreement_receipt(
    session: AsyncSession,
    *,
    chain_anchor_id: UUID,
    event_name: str,
    receipt: ReceiptVerification,
    credential_contract_address: str,
    minimum_confirmations: int,
    expected_space_id: UUID | None = None,
    activation_audit_command: AuditCommandContext | None = None,
    applied_at: datetime | None = None,
) -> AgreementReceiptApplication:
    """Apply one already verified event and advance only its matching state.

    The browser supplies at most a transaction hash and log index to the RPC
    layer.  Event values used here come exclusively from ``receipt`` and the
    fixed contract ABI decoder.
    """

    if (
        isinstance(minimum_confirmations, bool)
        or not isinstance(minimum_confirmations, int)
        or minimum_confirmations < 1
    ):
        raise AgreementOrchestrationError("minimum_confirmations must be positive")
    credential_contract = normalize_address(credential_contract_address)
    at = _now(applied_at)
    anchor = await session.get(ContractChainAnchor, chain_anchor_id)
    if anchor is None:
        raise AgreementOrchestrationError("chain anchor does not exist")
    if expected_space_id is not None and anchor.space_id != expected_space_id:
        raise AgreementOrchestrationError("chain anchor belongs to another space")
    _validate_frozen_receipt(
        anchor=anchor,
        receipt=receipt,
        event_name=event_name,
        minimum_confirmations=minimum_confirmations,
    )
    decoded = decode_medtrust_event(
        event_name, receipt.event_topics, receipt.event_data or ""
    )
    if (
        decoded.values.get("agreement_key") != anchor.agreement_key
        or decoded.values.get("terms_digest")
        != digest_to_bytes32(_require_digest(anchor.content_digest, "content_digest"))
    ):
        raise AgreementOrchestrationError("event is bound to another agreement revision")

    revision, contract, party_rows = await _load_contract_graph(
        session, anchor.contract_revision_id
    )
    if contract.id != anchor.contract_id or contract.space_id != anchor.space_id:
        raise AgreementOrchestrationError("anchor contract scope is inconsistent")
    parties = _validate_party_set(party_rows)
    payload = _base_payload(
        anchor=anchor,
        event_name=event_name,
        decoded_values=dict(decoded.values),
    )
    actor_wallet: str | None = None
    party: ContractParty | None = None
    binding: WalletIdentityBinding | None = None

    existing = await _existing_chain_receipt(session, receipt)
    is_new_receipt = existing is None
    if event_name == "AgreementConfirmed":
        actor_wallet = str(decoded.values["party_wallet"])
        if existing is not None:
            stored_party_id = existing.payload_snapshot.get("contract_party_id")
            stored_binding_id = existing.payload_snapshot.get("wallet_binding_id")
            try:
                party_id = UUID(str(stored_party_id))
                binding_id = UUID(str(stored_binding_id))
            except (TypeError, ValueError) as exc:
                raise AgreementOrchestrationError(
                    "stored confirmation receipt lacks a valid party binding"
                ) from exc
            party = await session.get(ContractParty, party_id)
            binding = await session.get(WalletIdentityBinding, binding_id)
        else:
            binding = await session.scalar(
                select(WalletIdentityBinding).where(
                    WalletIdentityBinding.space_id == contract.space_id,
                    WalletIdentityBinding.chain_id == anchor.chain_id,
                    WalletIdentityBinding.wallet_address == actor_wallet,
                    WalletIdentityBinding.status == "active",
                )
            )
            role = _party_role(binding.role_code) if binding is not None else None
            party = parties.get(role or "")
        if binding is None or party is None:
            raise AgreementOrchestrationError("confirmation wallet is not a required party")
        canonical_party = parties.get(party.party_role)
        if (
            canonical_party is None
            or canonical_party.id != party.id
            or binding.space_id != contract.space_id
            or binding.organization_id != party.organization_id
            or binding.role_code != _PARTY_TO_WALLET_ROLE[party.party_role]
            or binding.chain_id != anchor.chain_id
            or binding.wallet_address != actor_wallet
        ):
            raise AgreementOrchestrationError("confirmation actor does not match binding")
        if existing is None or existing.status != "applied":
            _validate_binding(
                binding,
                party=party,
                space_id=contract.space_id,
                chain_id=anchor.chain_id,
                credential_contract_address=credential_contract,
                at=at,
            )
        payload["contract_party_id"] = str(party.id)
        payload["wallet_binding_id"] = str(binding.id)

    if existing is not None:
        _assert_existing_receipt(
            existing,
            anchor=anchor,
            receipt=receipt,
            event_name=event_name,
            payload=payload,
        )
        if existing.actor_wallet != actor_wallet:
            raise AgreementOrchestrationError("stored receipt actor conflicts with event")
        if existing.status == "applied":
            if event_name == "AgreementRegistered" and anchor.status == "prepared":
                raise AgreementOrchestrationError("applied registration did not advance anchor")
            if event_name == "AgreementConfirmed" and party is not None:
                if not anchor.confirmation_bitmap & _PARTY_BITS[party.party_role]:
                    raise AgreementOrchestrationError(
                        "applied confirmation did not advance anchor"
                    )
            if event_name == "AgreementActivated" and (
                anchor.status != "active" or revision.status != "active"
            ):
                raise AgreementOrchestrationError("applied activation did not activate revision")
            return AgreementReceiptApplication(
                chain_event_receipt_id=existing.id,
                event_name=event_name,
                anchor_status=anchor.status,
                confirmation_bitmap=anchor.confirmation_bitmap,
                idempotent_replay=True,
            )
        chain_receipt = existing
        chain_receipt.status = "finalized"
        chain_receipt.confirmations = max(
            chain_receipt.confirmations, receipt.confirmations
        )
        chain_receipt.finalized_at = chain_receipt.finalized_at or at
    else:
        chain_receipt = _new_chain_receipt(
            anchor=anchor,
            receipt=receipt,
            event_name=event_name,
            actor_wallet=actor_wallet,
            payload=payload,
            at=at,
        )

    if event_name in {"AgreementRegistered", "AgreementConfirmed"} and (
        existing is None or existing.status != "applied"
    ):
        _validate_revision(revision)

    if event_name == "AgreementRegistered":
        if anchor.status != "prepared" or anchor.confirmation_bitmap != 0:
            raise AgreementOrchestrationError("registration event is out of sequence")
        bindings = list(
            (
                await session.scalars(
                    select(WalletIdentityBinding).where(
                        WalletIdentityBinding.space_id == contract.space_id,
                        WalletIdentityBinding.chain_id == anchor.chain_id,
                        WalletIdentityBinding.status == "active",
                    )
                )
            ).all()
        )
        selected = _select_party_bindings(
            parties=parties,
            bindings=bindings,
            space_id=contract.space_id,
            chain_id=anchor.chain_id,
            credential_contract_address=credential_contract,
            at=at,
        )
        expected = {
            "requester": selected["data_requester"].wallet_address,
            "data_provider": selected["data_provider"].wallet_address,
            "model_provider": selected["model_provider"].wallet_address,
            "operator": selected["operator_witness"].wallet_address,
            "valid_from": _uint64_timestamp(revision.effective_from, "effective_from"),
            "valid_until": _uint64_timestamp(revision.effective_until, "effective_until"),
        }
        if any(decoded.values.get(key) != value for key, value in expected.items()):
            raise AgreementOrchestrationError("registered participants or window do not match")
        if is_new_receipt:
            session.add(chain_receipt)
            await session.flush()
        anchor.status = "registered"
        anchor.registration_tx_hash = receipt.tx_hash
        anchor.registration_block_number = receipt.block_number
        anchor.registration_block_hash = receipt.block_hash
        anchor.updated_at = at
        anchor.row_version += 1
        chain_receipt.status = "applied"
        chain_receipt.applied_at = at
        await session.flush()
        signature_id = None
    elif event_name == "AgreementConfirmed":
        assert party is not None and binding is not None
        if anchor.status not in {"registered", "confirming"}:
            raise AgreementOrchestrationError("confirmation event is out of sequence")
        bit = _PARTY_BITS[party.party_role]
        bitmap = int(decoded.values["confirmation_bitmap"])
        if anchor.confirmation_bitmap & bit:
            raise AgreementOrchestrationError("party confirmation is duplicated")
        if party.party_role == "operator_witness" and anchor.confirmation_bitmap != 7:
            raise AgreementOrchestrationError("operator confirmation is not last")
        if bitmap != anchor.confirmation_bitmap | bit:
            raise AgreementOrchestrationError("confirmation bitmap is not the next valid state")
        existing_signature_id = await session.scalar(
            select(ContractSignature.id).where(
                ContractSignature.contract_party_id == party.id,
                ContractSignature.signed_content_digest == revision.content_digest,
            )
        )
        if existing_signature_id is not None:
            raise AgreementOrchestrationError("party already has a mirrored signature")
        if is_new_receipt:
            session.add(chain_receipt)
            await session.flush()
        anchor.confirmation_bitmap = bitmap
        anchor.status = "confirming"
        anchor.updated_at = at
        anchor.row_version += 1
        await session.flush()
        signature = await sign_verified_evm_contract_receipt(
            session,
            revision,
            contract_party_id=party.id,
            signer_user_id=binding.user_id,
            wallet_binding_id=binding.id,
            chain_event_receipt_id=chain_receipt.id,
            signed_at=at,
        )
        signature_id = signature.id
    else:
        if activation_audit_command is None:
            raise AgreementOrchestrationError(
                "AgreementActivated requires a server audit command"
            )
        if (
            anchor.confirmation_bitmap != anchor.required_confirmation_bitmap
            or revision.status != "signed"
        ):
            raise AgreementOrchestrationError(
                "activation requires all four finalized mirrored confirmations"
            )
        if is_new_receipt:
            session.add(chain_receipt)
            await session.flush()
        await activate_contract_revision(
            session,
            revision,
            activated_at=at,
            audit_command=activation_audit_command,
        )
        anchor.status = "active"
        anchor.activation_tx_hash = receipt.tx_hash
        anchor.activation_block_number = receipt.block_number
        anchor.activation_block_hash = receipt.block_hash
        anchor.finalized_at = at
        anchor.updated_at = at
        anchor.row_version += 1
        chain_receipt.status = "applied"
        chain_receipt.applied_at = at
        await session.flush()
        signature_id = None

    return AgreementReceiptApplication(
        chain_event_receipt_id=chain_receipt.id,
        event_name=event_name,
        anchor_status=anchor.status,
        confirmation_bitmap=anchor.confirmation_bitmap,
        idempotent_replay=False,
        contract_signature_id=signature_id,
    )
