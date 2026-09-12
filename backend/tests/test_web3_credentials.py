from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.identity.models import User
from app.modules.web3 import credentials
from app.modules.web3.credentials import (
    MAX_CREDENTIAL_LIFETIME,
    MIN_CREDENTIAL_LIFETIME,
    ROLE_HASHES,
    CredentialIssuanceError,
    apply_credential_issued_receipt,
    prepare_credential_issue,
    prepare_credential_revocation,
)
from app.modules.web3.evm_codec import EVENT_TOPICS
from app.modules.web3.identity_service import load_verified_credential_receipt
from app.modules.web3.models import ChainEventReceipt, WalletIdentityBinding
from app.modules.web3.rpc import ReceiptStatus, ReceiptVerification


NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
CONTRACT = "0x" + "33" * 20
ISSUER = "0x" + "66" * 20
TX_HASH = "0x" + "44" * 32
BLOCK_HASH = "0x" + "55" * 32


def _binding(*, role_code: str = "data_provider") -> WalletIdentityBinding:
    return WalletIdentityBinding(
        id=UUID("00000000-0000-0000-0000-000000000101"),
        space_id=UUID("00000000-0000-0000-0000-000000000102"),
        user_id=UUID("00000000-0000-0000-0000-000000000103"),
        organization_id=UUID("00000000-0000-0000-0000-000000000104"),
        role_code=role_code,
        chain_id=31337,
        wallet_address="0x" + "12" * 20,
        did_uri="did:pkh:eip155:31337:0x" + "12" * 20,
        status="pending",
        identity_evidence_digest="sha256:" + "ab" * 32,
        row_version=1,
    )


def _word(value: int) -> str:
    return f"{value:064x}"


def _address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:]


def _receipt(
    prepared_call,
    *,
    token_id: int = 7,
    holder: str | None = None,
    role_hash: str | None = None,
    evidence_digest: str | None = None,
    expires_at: int | None = None,
    issuer: str = ISSUER,
) -> ReceiptVerification:
    arguments = prepared_call.arguments
    actual_holder = holder or str(arguments[0])
    actual_role = role_hash or str(arguments[3])
    actual_evidence = evidence_digest or str(arguments[4])
    actual_expiry = expires_at if expires_at is not None else int(arguments[5])
    event_data = (
        "0x"
        + str(arguments[3])[2:]
        + str(arguments[1])[2:]
        + str(arguments[2])[2:]
        + actual_evidence[2:]
        + _word(actual_expiry)
    )
    return ReceiptVerification(
        status=ReceiptStatus.CONFIRMED,
        chain_id=prepared_call.chain_id,
        tx_hash=TX_HASH,
        expected_contract_address=prepared_call.to,
        expected_event_topic=EVENT_TOPICS["CredentialIssued"],
        expected_log_index=2,
        minimum_confirmations=2,
        block_number=100,
        block_hash=BLOCK_HASH,
        head_block_number=101,
        confirmations=2,
        event_topics=(
            EVENT_TOPICS["CredentialIssued"],
            "0x" + _word(token_id),
            _address_topic(actual_holder),
            _address_topic(issuer),
        ),
        event_data=event_data,
    )


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"medtrust": None}},
    )


async def _new_database():
    engine = _engine()
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    WalletIdentityBinding.__table__,
                    ChainEventReceipt.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add(_binding())
    return engine, factory


def test_role_hashes_are_fixed_for_the_four_server_roles() -> None:
    assert dict(ROLE_HASHES) == {
        "data_requester": "0x4ed76b89a0904b3be2092ca0165f73e258b8d32a08f83e56de2e977def71c9ab",
        "data_provider": "0xb356ad87e296e97224845d86b97fce26123c5ea3eef41d97b092edf5153b40d9",
        "model_provider": "0x0d514e5ddafce1ed34408d04a640c3471a33d3979a3d335490ffc64772059cac",
        "space_operator": "0x2e7ad114562f4d7249fe36ff3c80e664a6c66c10da9a5a0f1703a8f2ba51b512",
    }
    with pytest.raises(TypeError):
        ROLE_HASHES["attacker"] = "0x" + "00" * 32


def test_pending_binding_produces_deterministic_minimal_issue_calldata() -> None:
    binding = _binding()
    expiry = NOW + timedelta(days=30, microseconds=777)

    first = prepare_credential_issue(
        binding,
        credential_contract_address=CONTRACT.upper().replace("0X", "0x"),
        expires_at=expiry,
        now=NOW,
    )
    second = prepare_credential_issue(
        binding,
        credential_contract_address=CONTRACT,
        expires_at=expiry,
        now=NOW,
    )

    assert first == second
    assert first.method == "issue"
    assert first.expected_event == "CredentialIssued"
    assert first.data.startswith("0xce9c9c46")
    assert first.arguments[0] == binding.wallet_address
    assert first.arguments[3] == ROLE_HASHES["data_provider"]
    assert first.arguments[4] == "0x" + first.credential_scope_digest[7:]
    assert first.expires_at.microsecond == 0
    assert binding.did_uri not in first.data
    assert str(binding.organization_id) not in first.data
    assert binding.identity_evidence_digest not in first.data


def test_issue_preparation_rejects_role_status_address_and_bad_lifetimes() -> None:
    binding = _binding(role_code="frontend_claimed_admin")
    with pytest.raises(CredentialIssuanceError):
        prepare_credential_issue(
            binding,
            credential_contract_address=CONTRACT,
            expires_at=NOW + timedelta(days=1),
            now=NOW,
        )

    binding = _binding()
    binding.status = "active"
    with pytest.raises(CredentialIssuanceError):
        prepare_credential_issue(
            binding,
            credential_contract_address=CONTRACT,
            expires_at=NOW + timedelta(days=1),
            now=NOW,
        )

    binding = _binding()
    for expiry in (
        NOW + MIN_CREDENTIAL_LIFETIME - timedelta(seconds=1),
        NOW + MAX_CREDENTIAL_LIFETIME + timedelta(seconds=1),
    ):
        with pytest.raises(CredentialIssuanceError):
            prepare_credential_issue(
                binding,
                credential_contract_address=CONTRACT,
                expires_at=expiry,
                now=NOW,
            )
    with pytest.raises(CredentialIssuanceError):
        prepare_credential_issue(
            binding,
            credential_contract_address="0x" + "0" * 40,
            expires_at=NOW + timedelta(days=1),
            now=NOW,
        )


def test_active_binding_produces_deterministic_revocation_calldata() -> None:
    binding = _binding()
    binding.status = "active"
    binding.credential_contract_address = CONTRACT
    binding.credential_token_id = "17"
    binding.credential_scope_digest = "sha256:" + "cd" * 32
    binding.credential_expires_at = NOW + timedelta(days=30)
    binding.verified_at = NOW
    binding.verified_by = UUID("00000000-0000-0000-0000-000000000105")

    first = prepare_credential_revocation(
        binding,
        credential_contract_address=CONTRACT,
        reason="  机构角色已撤销  ",
    )
    second = prepare_credential_revocation(
        binding,
        credential_contract_address=CONTRACT,
        reason="机构角色已撤销",
    )

    assert first == second
    assert first.method == "revokeCredential"
    assert first.expected_event == "CredentialRevoked"
    assert first.arguments == (17, "0x" + first.reason_digest[7:])
    assert first.data == "0x3d3162b0" + _word(17) + first.reason_digest[7:]
    assert "机构角色已撤销" not in first.data


def test_revocation_preparation_rejects_pending_or_mismatched_binding() -> None:
    pending = _binding()
    with pytest.raises(CredentialIssuanceError):
        prepare_credential_revocation(
            pending,
            credential_contract_address=CONTRACT,
            reason="role revoked",
        )

    active = _binding()
    active.status = "active"
    active.credential_contract_address = "0x" + "99" * 20
    active.credential_token_id = "17"
    with pytest.raises(CredentialIssuanceError):
        prepare_credential_revocation(
            active,
            credential_contract_address=CONTRACT,
            reason="role revoked",
        )

def test_receipt_is_persisted_before_approval_and_replay_is_idempotent(
    monkeypatch,
) -> None:
    asyncio.run(_test_receipt_application(monkeypatch))


async def _test_receipt_application(monkeypatch) -> None:
    engine, factory = await _new_database()
    calls: list[str] = []
    operator = User(id=UUID("00000000-0000-0000-0000-000000000105"))
    try:
        async with factory() as session:
            binding = await session.get(
                WalletIdentityBinding,
                UUID("00000000-0000-0000-0000-000000000101"),
            )
            prepared = prepare_credential_issue(
                binding,
                credential_contract_address=CONTRACT,
                expires_at=NOW + timedelta(days=30),
                now=NOW,
            )
        receipt = _receipt(prepared)

        async def fake_approve(
            session,
            *,
            binding_id,
            operator_user,
            receipt,
            minimum_confirmations,
            now,
        ):
            stored = await session.scalar(
                select(ChainEventReceipt).where(
                    ChainEventReceipt.transaction_hash == TX_HASH
                )
            )
            assert stored is not None
            assert stored.status == "finalized"
            assert stored.applied_at is None
            calls.append(receipt.token_id)
            binding = await session.get(WalletIdentityBinding, binding_id)
            binding.status = "active"
            binding.credential_contract_address = receipt.contract_address
            binding.credential_token_id = receipt.token_id
            binding.credential_scope_digest = receipt.credential_scope_digest
            binding.credential_expires_at = receipt.credential_expires_at
            binding.verified_at = now
            binding.verified_by = operator_user.id
            binding.row_version += 1
            await session.flush()
            return binding

        monkeypatch.setattr(credentials, "approve_wallet_binding", fake_approve)
        async with factory() as session, session.begin():
            first = await apply_credential_issued_receipt(
                session,
                binding_id=prepared.binding_id,
                prepared_call=prepared,
                operator_user=operator,
                issuer_address=ISSUER,
                receipt=receipt,
                minimum_confirmations=2,
                applied_at=NOW + timedelta(minutes=1),
            )
        assert first.idempotent_replay is False
        assert first.binding_status == "active"
        assert calls == ["7"]

        async with factory() as session, session.begin():
            replay = await apply_credential_issued_receipt(
                session,
                binding_id=prepared.binding_id,
                prepared_call=prepared,
                operator_user=operator,
                issuer_address=ISSUER,
                receipt=receipt,
                minimum_confirmations=2,
                applied_at=NOW + timedelta(minutes=2),
            )
        assert replay.idempotent_replay is True
        assert replay.chain_event_receipt_id == first.chain_event_receipt_id
        assert calls == ["7"]

        async with factory() as session, session.begin():
            mirrored = await load_verified_credential_receipt(
                session, first.chain_event_receipt_id
            )
            stored = await session.get(
                ChainEventReceipt, first.chain_event_receipt_id
            )
            assert mirrored.binding_id == prepared.binding_id
            assert mirrored.token_id == "7"
            assert stored.status == "applied"
            assert stored.applied_at is not None
    finally:
        await engine.dispose()


def test_receipt_rejects_lookalikes_event_tampering_and_stored_tampering(
    monkeypatch,
) -> None:
    asyncio.run(_test_receipt_tampering(monkeypatch))


async def _test_receipt_tampering(monkeypatch) -> None:
    engine, factory = await _new_database()
    operator = User(id=UUID("00000000-0000-0000-0000-000000000105"))
    try:
        async with factory() as session:
            binding = await session.get(
                WalletIdentityBinding,
                UUID("00000000-0000-0000-0000-000000000101"),
            )
            prepared = prepare_credential_issue(
                binding,
                credential_contract_address=CONTRACT,
                expires_at=NOW + timedelta(days=30),
                now=NOW,
            )

        async with factory() as session:
            with pytest.raises(CredentialIssuanceError):
                async with session.begin():
                    await apply_credential_issued_receipt(
                        session,
                        binding_id=prepared.binding_id,
                        prepared_call=prepared,
                        operator_user=operator,
                        issuer_address=ISSUER,
                        receipt=object(),
                        minimum_confirmations=2,
                        applied_at=NOW + timedelta(minutes=1),
                    )

        wrong_holder = "0x" + "99" * 20
        async with factory() as session:
            with pytest.raises(CredentialIssuanceError):
                async with session.begin():
                    await apply_credential_issued_receipt(
                        session,
                        binding_id=prepared.binding_id,
                        prepared_call=prepared,
                        operator_user=operator,
                        issuer_address=ISSUER,
                        receipt=_receipt(prepared, holder=wrong_holder),
                        minimum_confirmations=2,
                        applied_at=NOW + timedelta(minutes=1),
                    )
        async with factory() as session:
            assert await session.scalar(select(ChainEventReceipt.id)) is None

        async def fake_approve(
            session,
            *,
            binding_id,
            operator_user,
            receipt,
            minimum_confirmations,
            now,
        ):
            binding = await session.get(WalletIdentityBinding, binding_id)
            binding.status = "active"
            binding.credential_contract_address = receipt.contract_address
            binding.credential_token_id = receipt.token_id
            binding.credential_scope_digest = receipt.credential_scope_digest
            binding.credential_expires_at = receipt.credential_expires_at
            binding.verified_at = now
            binding.verified_by = operator_user.id
            binding.row_version += 1
            await session.flush()
            return binding

        monkeypatch.setattr(credentials, "approve_wallet_binding", fake_approve)
        valid_receipt = _receipt(prepared)
        async with factory() as session, session.begin():
            applied = await apply_credential_issued_receipt(
                session,
                binding_id=prepared.binding_id,
                prepared_call=prepared,
                operator_user=operator,
                issuer_address=ISSUER,
                receipt=valid_receipt,
                minimum_confirmations=2,
                applied_at=NOW + timedelta(minutes=1),
            )
        async with factory() as session, session.begin():
            stored = await session.get(
                ChainEventReceipt, applied.chain_event_receipt_id
            )
            stored.payload_snapshot = {
                **stored.payload_snapshot,
                "role_code": "space_operator",
            }
        async with factory() as session:
            with pytest.raises(CredentialIssuanceError):
                async with session.begin():
                    await apply_credential_issued_receipt(
                        session,
                        binding_id=prepared.binding_id,
                        prepared_call=prepared,
                        operator_user=operator,
                        issuer_address=ISSUER,
                        receipt=valid_receipt,
                        minimum_confirmations=2,
                        applied_at=NOW + timedelta(minutes=2),
                    )
    finally:
        await engine.dispose()
