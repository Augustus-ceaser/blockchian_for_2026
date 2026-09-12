from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.audit import AuditCommandContext
from app.modules.contracts.models import Contract, ContractParty, ContractRevision
from app.modules.contracts.services import (
    ContractInvariantError,
    sign_contract_revision,
    sign_verified_evm_contract_receipt,
)
from app.modules.web3 import agreements
from app.modules.web3.agreements import (
    AgreementOrchestrationError,
    apply_agreement_receipt,
    prepare_agreement_activation,
    prepare_agreement_anchor,
    prepare_agreement_confirmation,
)
from app.modules.web3.evm_codec import EVENT_TOPICS, digest_to_bytes32, stable_bytes32
from app.modules.web3.models import (
    ChainEventReceipt,
    ContractChainAnchor,
    WalletIdentityBinding,
)
from app.modules.web3.rpc import ReceiptStatus, ReceiptVerification


CHAIN_ID = 31337
REGISTRY = "0x" + "11" * 20
CREDENTIAL = "0x" + "22" * 20
CONTENT_DIGEST = "sha256:" + "ab" * 32
TX_HASH = "0x" + "33" * 32
BLOCK_HASH = "0x" + "44" * 32
NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _FakeSession:
    def __init__(self, *, gets=None, scalar_values=None, scalar_rows=None):
        self.gets = gets or {}
        self.scalar_values = list(scalar_values or [])
        self.scalar_rows = list(scalar_rows or [])
        self.added = []

    async def get(self, model, key):
        return self.gets.get((model, key))

    async def scalar(self, _statement):
        if not self.scalar_values:
            raise AssertionError("unexpected scalar query")
        return self.scalar_values.pop(0)

    async def scalars(self, _statement):
        if not self.scalar_rows:
            raise AssertionError("unexpected scalars query")
        return _Rows(self.scalar_rows.pop(0))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()


def _graph():
    space_id = uuid4()
    contract_id = uuid4()
    revision_id = uuid4()
    revision = SimpleNamespace(
        id=revision_id,
        contract_id=contract_id,
        status="proposed",
        signing_mode="multi_party",
        content_digest=CONTENT_DIGEST,
        effective_from=NOW - timedelta(minutes=1),
        effective_until=NOW + timedelta(days=1),
    )
    contract = SimpleNamespace(id=contract_id, space_id=space_id)
    roles = (
        ("data_requester", 1),
        ("data_provider", 2),
        ("model_provider", 3),
        ("operator_witness", 4),
    )
    parties = []
    bindings = []
    for index, (role, signing_order) in enumerate(roles, start=1):
        organization_id = uuid4()
        binding_role = "space_operator" if role == "operator_witness" else role
        binding = SimpleNamespace(
            id=uuid4(),
            space_id=space_id,
            user_id=uuid4(),
            organization_id=organization_id,
            role_code=binding_role,
            chain_id=CHAIN_ID,
            wallet_address="0x" + f"{index:02x}" * 20,
            did_uri=f"did:pkh:eip155:{CHAIN_ID}:0x{index:040x}",
            status="active",
            credential_contract_address=CREDENTIAL,
            credential_token_id=str(index),
            credential_scope_digest="sha256:" + f"{index:02x}" * 32,
            credential_expires_at=NOW + timedelta(days=2),
        )
        party = SimpleNamespace(
            id=uuid4(),
            contract_revision_id=revision_id,
            organization_id=organization_id,
            party_role=role,
            signing_order=signing_order,
            is_required=True,
            identity_snapshot={"wallet_binding_id": str(binding.id)},
        )
        parties.append(party)
        bindings.append(binding)
    return revision, contract, parties, bindings


def _anchor(revision, contract, *, bitmap=0, status="registered"):
    return SimpleNamespace(
        id=uuid4(),
        space_id=contract.space_id,
        contract_id=contract.id,
        contract_revision_id=revision.id,
        chain_id=CHAIN_ID,
        registry_address=REGISTRY,
        agreement_key=stable_bytes32("contract-revision", revision.id),
        content_digest=CONTENT_DIGEST,
        status=status,
        required_confirmation_bitmap=15,
        confirmation_bitmap=bitmap,
        registration_tx_hash=None,
        registration_block_number=None,
        registration_block_hash=None,
        activation_tx_hash=None,
        activation_block_number=None,
        activation_block_hash=None,
        finalized_at=None,
        updated_at=NOW,
        row_version=1,
    )


def _word(value: int) -> str:
    return f"{value:064x}"


def _topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address[2:]


def _receipt(
    event_name: str,
    anchor,
    *,
    log_index=1,
    bitmap=None,
    wallet=None,
    participant_wallets=None,
    valid_from=None,
    valid_until=None,
):
    if event_name == "AgreementConfirmed":
        topics = (
            EVENT_TOPICS[event_name],
            anchor.agreement_key,
            digest_to_bytes32(anchor.content_digest),
            _topic_address(wallet),
        )
        data = "0x" + _word(bitmap)
    elif event_name == "AgreementRegistered":
        topics = (
            EVENT_TOPICS[event_name],
            anchor.agreement_key,
            digest_to_bytes32(anchor.content_digest),
        )
        ordered = [
            participant_wallets[role]
            for role in (
                "data_requester",
                "data_provider",
                "model_provider",
                "operator_witness",
            )
        ]
        data = "0x" + "".join(
            "0" * 24 + address[2:] for address in ordered
        ) + _word(valid_from) + _word(valid_until)
    else:
        topics = (
            EVENT_TOPICS[event_name],
            anchor.agreement_key,
            digest_to_bytes32(anchor.content_digest),
        )
        data = "0x"
    return ReceiptVerification(
        status=ReceiptStatus.CONFIRMED,
        chain_id=CHAIN_ID,
        tx_hash=TX_HASH,
        expected_contract_address=REGISTRY,
        expected_event_topic=EVENT_TOPICS[event_name],
        expected_log_index=log_index,
        minimum_confirmations=2,
        block_number=100,
        block_hash=BLOCK_HASH,
        head_block_number=101,
        confirmations=2,
        event_topics=topics,
        event_data=data,
    )


def test_registration_plan_is_stable_and_anchor_creation_is_idempotent() -> None:
    asyncio.run(_registration_plan_is_stable())


async def _registration_plan_is_stable() -> None:
    revision, contract, parties, bindings = _graph()
    session = _FakeSession(
        gets={(ContractRevision, revision.id): revision, (Contract, contract.id): contract},
        scalar_values=[None, None],
        scalar_rows=[parties, bindings],
    )
    first = await prepare_agreement_anchor(
        session,
        contract_revision_id=revision.id,
        chain_id=CHAIN_ID,
        registry_address=REGISTRY.upper().replace("0X", "0x"),
        credential_contract_address=CREDENTIAL,
        prepared_by_user_id=bindings[-1].user_id,
        prepared_at=NOW,
    )
    created = next(item for item in session.added if isinstance(item, ContractChainAnchor))
    assert first.agreement_key == stable_bytes32("contract-revision", revision.id)
    assert first.call.method == "registerAgreement"
    assert first.call.data.startswith("0xfb9fc43b")
    assert first.call.arguments[1] == digest_to_bytes32(CONTENT_DIGEST)
    assert first.call.needs_submission is True

    replay = _FakeSession(
        gets={(ContractRevision, revision.id): revision, (Contract, contract.id): contract},
        scalar_values=[created, None],
        scalar_rows=[parties, bindings],
    )
    second = await prepare_agreement_anchor(
        replay,
        contract_revision_id=revision.id,
        chain_id=CHAIN_ID,
        registry_address=REGISTRY,
        credential_contract_address=CREDENTIAL,
        prepared_by_user_id=bindings[-1].user_id,
        prepared_at=NOW,
    )
    assert second.anchor_id == first.anchor_id
    assert second.call.data == first.call.data
    assert replay.added == []


def test_registration_rejects_a_revision_already_signed_in_legacy_mode() -> None:
    asyncio.run(_registration_rejects_legacy_signatures())


async def _registration_rejects_legacy_signatures() -> None:
    revision, contract, parties, bindings = _graph()
    session = _FakeSession(
        gets={(ContractRevision, revision.id): revision, (Contract, contract.id): contract},
        scalar_values=[None, uuid4()],
        scalar_rows=[parties, bindings],
    )
    with pytest.raises(AgreementOrchestrationError, match="different signature flow"):
        await prepare_agreement_anchor(
            session,
            contract_revision_id=revision.id,
            chain_id=CHAIN_ID,
            registry_address=REGISTRY,
            credential_contract_address=CREDENTIAL,
            prepared_by_user_id=bindings[-1].user_id,
            prepared_at=NOW,
        )


def test_legacy_signature_rejects_an_existing_chain_anchor() -> None:
    asyncio.run(_legacy_signature_rejects_chain_anchor())


async def _legacy_signature_rejects_chain_anchor() -> None:
    revision, _contract, _parties, _bindings = _graph()
    session = _FakeSession(scalar_values=[uuid4(), None])
    with pytest.raises(ContractInvariantError, match="cannot be mixed"):
        await sign_contract_revision(
            session,
            revision,
            contract_party_id=uuid4(),
            signer_organization_id=uuid4(),
            signer_user_id=uuid4(),
            signature_value_ref="demo-signature",
            signed_at=NOW,
        )


def test_web3_signature_rejects_an_existing_legacy_signature() -> None:
    asyncio.run(_web3_signature_rejects_legacy_signature())


async def _web3_signature_rejects_legacy_signature() -> None:
    revision, _contract, _parties, _bindings = _graph()
    session = _FakeSession(scalar_values=[uuid4()])
    with pytest.raises(ContractInvariantError, match="cannot be mixed"):
        await sign_verified_evm_contract_receipt(
            session,
            revision,
            contract_party_id=uuid4(),
            signer_user_id=uuid4(),
            wallet_binding_id=uuid4(),
            chain_event_receipt_id=uuid4(),
            signed_at=NOW,
        )


def test_operator_confirmation_is_blocked_until_bitmap_seven() -> None:
    asyncio.run(_operator_must_be_last())


async def _operator_must_be_last() -> None:
    revision, contract, parties, bindings = _graph()
    operator_binding = bindings[-1]
    anchor = _anchor(revision, contract, bitmap=3)
    session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_rows=[parties, [operator_binding]],
    )
    with pytest.raises(AgreementOrchestrationError, match="operator must confirm"):
        await prepare_agreement_confirmation(
            session,
            chain_anchor_id=anchor.id,
            current_user_id=operator_binding.user_id,
            credential_contract_address=CREDENTIAL,
            prepared_at=NOW,
        )

    anchor.confirmation_bitmap = 7
    session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_rows=[parties, [operator_binding]],
    )
    result = await prepare_agreement_confirmation(
        session,
        chain_anchor_id=anchor.id,
        current_user_id=operator_binding.user_id,
        credential_contract_address=CREDENTIAL,
        prepared_at=NOW,
    )
    assert result.party_role == "operator_witness"
    assert result.expected_confirmation_bitmap == 15
    assert result.call.from_wallet == operator_binding.wallet_address
    assert result.call.data.startswith("0x797af627")


def test_registration_receipt_must_match_all_four_server_side_bindings() -> None:
    asyncio.run(_registration_receipt_matches_bindings())


async def _registration_receipt_matches_bindings() -> None:
    revision, contract, parties, bindings = _graph()
    anchor = _anchor(revision, contract, bitmap=0, status="prepared")
    wallets = {
        role: binding.wallet_address
        for role, binding in zip(
            (
                "data_requester",
                "data_provider",
                "model_provider",
                "operator_witness",
            ),
            bindings,
            strict=True,
        )
    }
    receipt = _receipt(
        "AgreementRegistered",
        anchor,
        participant_wallets=wallets,
        valid_from=int(revision.effective_from.timestamp()),
        valid_until=int(revision.effective_until.timestamp()),
    )
    session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_values=[None],
        scalar_rows=[parties, bindings],
    )
    result = await apply_agreement_receipt(
        session,
        chain_anchor_id=anchor.id,
        event_name="AgreementRegistered",
        receipt=receipt,
        credential_contract_address=CREDENTIAL,
        minimum_confirmations=2,
        applied_at=NOW,
    )
    assert result.anchor_status == "registered"
    assert anchor.registration_tx_hash == TX_HASH
    stored = next(item for item in session.added if isinstance(item, ChainEventReceipt))
    assert stored.status == "applied"

    wrong_wallets = {**wallets, "data_provider": "0x" + "ee" * 20}
    wrong_anchor = _anchor(revision, contract, bitmap=0, status="prepared")
    wrong_receipt = _receipt(
        "AgreementRegistered",
        wrong_anchor,
        participant_wallets=wrong_wallets,
        valid_from=int(revision.effective_from.timestamp()),
        valid_until=int(revision.effective_until.timestamp()),
    )
    wrong_session = _FakeSession(
        gets={
            (ContractChainAnchor, wrong_anchor.id): wrong_anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_values=[None],
        scalar_rows=[parties, bindings],
    )
    with pytest.raises(AgreementOrchestrationError, match="participants"):
        await apply_agreement_receipt(
            wrong_session,
            chain_anchor_id=wrong_anchor.id,
            event_name="AgreementRegistered",
            receipt=wrong_receipt,
            credential_contract_address=CREDENTIAL,
            minimum_confirmations=2,
            applied_at=NOW,
        )
    assert wrong_session.added == []


def test_confirmed_receipt_is_strictly_decoded_mirrored_and_replay_safe(monkeypatch) -> None:
    asyncio.run(_confirmed_receipt_is_strict(monkeypatch))


async def _confirmed_receipt_is_strict(monkeypatch) -> None:
    revision, contract, parties, bindings = _graph()
    requester_party = parties[0]
    requester_binding = bindings[0]
    anchor = _anchor(revision, contract, bitmap=0)
    receipt = _receipt(
        "AgreementConfirmed",
        anchor,
        bitmap=1,
        wallet=requester_binding.wallet_address,
    )
    session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_values=[None, requester_binding, None],
        scalar_rows=[parties],
    )

    async def mirror_signature(_session, _revision, **kwargs):
        chain_receipt = next(
            item for item in session.added if isinstance(item, ChainEventReceipt)
        )
        assert kwargs["contract_party_id"] == requester_party.id
        assert kwargs["wallet_binding_id"] == requester_binding.id
        assert chain_receipt.payload_snapshot["content_digest"] == CONTENT_DIGEST
        chain_receipt.status = "applied"
        chain_receipt.applied_at = NOW
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(agreements, "sign_verified_evm_contract_receipt", mirror_signature)
    applied = await apply_agreement_receipt(
        session,
        chain_anchor_id=anchor.id,
        event_name="AgreementConfirmed",
        receipt=receipt,
        credential_contract_address=CREDENTIAL,
        minimum_confirmations=2,
        applied_at=NOW,
    )
    stored = next(item for item in session.added if isinstance(item, ChainEventReceipt))
    assert applied.contract_signature_id is not None
    assert anchor.confirmation_bitmap == 1
    assert stored.actor_wallet == requester_binding.wallet_address
    assert stored.status == "applied"

    replay_session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
            (ContractParty, requester_party.id): requester_party,
            (WalletIdentityBinding, requester_binding.id): requester_binding,
        },
        scalar_values=[stored],
        scalar_rows=[parties],
    )
    replay = await apply_agreement_receipt(
        replay_session,
        chain_anchor_id=anchor.id,
        event_name="AgreementConfirmed",
        receipt=receipt,
        credential_contract_address=CREDENTIAL,
        minimum_confirmations=2,
        applied_at=NOW,
    )
    assert replay.idempotent_replay is True
    assert replay.chain_event_receipt_id == stored.id

    wrong_digest_topics = list(receipt.event_topics)
    wrong_digest_topics[2] = "0x" + "ff" * 32
    tampered = replace(receipt, event_topics=tuple(wrong_digest_topics))
    with pytest.raises(AgreementOrchestrationError, match="another agreement"):
        await apply_agreement_receipt(
            replay_session,
            chain_anchor_id=anchor.id,
            event_name="AgreementConfirmed",
            receipt=tampered,
            credential_contract_address=CREDENTIAL,
            minimum_confirmations=2,
            applied_at=NOW,
        )


def test_activation_waits_for_four_signatures_and_replays_idempotently(monkeypatch) -> None:
    asyncio.run(_activation_waits_for_four(monkeypatch))


async def _activation_waits_for_four(monkeypatch) -> None:
    revision, contract, parties, _bindings = _graph()
    revision.status = "signed"
    anchor = _anchor(revision, contract, bitmap=15, status="confirming")
    receipt = _receipt("AgreementActivated", anchor, log_index=5)
    session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_values=[None],
        scalar_rows=[parties],
    )
    command = AuditCommandContext(
        command_id=uuid4(),
        idempotency_key="sha256:" + "55" * 32,
        correlation_id=uuid4(),
        actor_type="system",
        actor_service_code="medtrust.web3.agreements",
    )

    activation_call = await prepare_agreement_activation(
        _FakeSession(gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
        }),
        chain_anchor_id=anchor.id,
        prepared_at=NOW,
    )
    assert activation_call.data.startswith("0x59db6e85")
    assert activation_call.expected_event == "AgreementActivated"

    async def activate(_session, target, **kwargs):
        assert kwargs["audit_command"] is command
        target.status = "active"

    monkeypatch.setattr(agreements, "activate_contract_revision", activate)
    result = await apply_agreement_receipt(
        session,
        chain_anchor_id=anchor.id,
        event_name="AgreementActivated",
        receipt=receipt,
        credential_contract_address=CREDENTIAL,
        minimum_confirmations=2,
        activation_audit_command=command,
        applied_at=NOW,
    )
    stored = next(item for item in session.added if isinstance(item, ChainEventReceipt))
    assert result.anchor_status == "active"
    assert revision.status == "active"
    assert stored.status == "applied"

    replay_session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_values=[stored],
        scalar_rows=[parties],
    )
    replay = await apply_agreement_receipt(
        replay_session,
        chain_anchor_id=anchor.id,
        event_name="AgreementActivated",
        receipt=receipt,
        credential_contract_address=CREDENTIAL,
        minimum_confirmations=2,
        activation_audit_command=command,
        applied_at=NOW,
    )
    assert replay.idempotent_replay is True


def test_activation_rejects_incomplete_bitmap_before_writing(monkeypatch) -> None:
    asyncio.run(_activation_rejects_incomplete(monkeypatch))


async def _activation_rejects_incomplete(monkeypatch) -> None:
    revision, contract, parties, _bindings = _graph()
    revision.status = "signed"
    anchor = _anchor(revision, contract, bitmap=7, status="confirming")
    receipt = _receipt("AgreementActivated", anchor)
    session = _FakeSession(
        gets={
            (ContractChainAnchor, anchor.id): anchor,
            (ContractRevision, revision.id): revision,
            (Contract, contract.id): contract,
        },
        scalar_values=[None],
        scalar_rows=[parties],
    )
    with pytest.raises(AgreementOrchestrationError, match="all four"):
        await apply_agreement_receipt(
            session,
            chain_anchor_id=anchor.id,
            event_name="AgreementActivated",
            receipt=receipt,
            credential_contract_address=CREDENTIAL,
            minimum_confirmations=2,
            activation_audit_command=SimpleNamespace(),
            applied_at=NOW,
        )
