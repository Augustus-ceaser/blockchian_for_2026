from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import FastAPI, Request
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.identity.models import (
    LocalDemoSession,
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    User,
)
from app.modules.spaces.models import (
    Space,
    SpaceParticipant,
    SpaceParticipantRole,
)
from app.modules.web3.identity_service import (
    VerifiedCredentialReceipt,
    WalletIdentityError,
    WalletIdentityPolicy,
    WalletSessionIssue,
    approve_wallet_binding,
    binding_scope_digest,
    create_bind_challenge,
    create_login_challenge,
    revoke_wallet_binding,
    verify_bind_challenge,
    verify_login_challenge,
)
from app.modules.web3.models import WalletAuthChallenge, WalletIdentityBinding
from app.modules.web3.siwe import parse_siwe_message
from app.api.routes.web3_identity import (
    router as wallet_router,
    wallet_capabilities,
)

NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
PRIVATE_KEY = "0x" + "11" * 32
OTHER_PRIVATE_KEY = "0x" + "22" * 32
ACCOUNT = Account.from_key(PRIVATE_KEY)


@dataclass(frozen=True)
class Graph:
    provider_user_id: UUID
    operator_user_id: UUID
    provider_organization_id: UUID
    space_id: UUID


def _policy() -> WalletIdentityPolicy:
    return WalletIdentityPolicy(
        domain="127.0.0.1:5173",
        login_uri="http://127.0.0.1:5173/auth/wallet/verify",
        bind_uri="http://127.0.0.1:5173/auth/wallet/bind/verify",
        allowed_chain_ids=(31337,),
        minimum_receipt_confirmations=2,
    )


def _signature(message: str, private_key: str = PRIVATE_KEY) -> str:
    value = Account.sign_message(
        encode_defunct(text=message), private_key=private_key
    ).signature.hex()
    return value if value.startswith("0x") else f"0x{value}"


def _request_for(application: FastAPI) -> Request:
    return Request({"type": "http", "app": application})


def _engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"medtrust": None}},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def _create_schema(engine) -> None:
    tables = [
        User.__table__,
        Organization.__table__,
        OrganizationMember.__table__,
        OrganizationMemberRole.__table__,
        Space.__table__,
        SpaceParticipant.__table__,
        SpaceParticipantRole.__table__,
        WalletIdentityBinding.__table__,
        WalletAuthChallenge.__table__,
        LocalDemoSession.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection, tables=tables
            )
        )


async def _seed_graph(session, *, provider_verified: bool = True) -> Graph:
    provider_user = User(
        identity_issuer="test",
        identity_subject="provider",
        display_name="Provider",
        status="active",
    )
    operator_user = User(
        identity_issuer="test",
        identity_subject="operator",
        display_name="Operator",
        status="active",
    )
    session.add_all([provider_user, operator_user])
    await session.flush()

    provider_organization = Organization(
        legal_name="Provider Hospital",
        display_name="Provider Hospital",
        organization_type="hospital",
        verification_status="verified" if provider_verified else "pending",
        status="active",
        created_by=provider_user.id,
    )
    operator_organization = Organization(
        legal_name="Space Operator",
        display_name="Space Operator",
        organization_type="operator",
        verification_status="verified",
        status="active",
        created_by=operator_user.id,
    )
    session.add_all([provider_organization, operator_organization])
    await session.flush()

    space = Space(
        code=f"test-space-{uuid4()}",
        name="Test Space",
        space_type="industry",
        operator_organization_id=operator_organization.id,
        status="active",
        ruleset_version="1",
        classification_scheme_version="1",
        created_by=operator_user.id,
    )
    provider_member = OrganizationMember(
        organization_id=provider_organization.id,
        user_id=provider_user.id,
        status="active",
        valid_from=NOW - timedelta(days=1),
        created_by=operator_user.id,
    )
    operator_member = OrganizationMember(
        organization_id=operator_organization.id,
        user_id=operator_user.id,
        status="active",
        valid_from=NOW - timedelta(days=1),
        created_by=operator_user.id,
    )
    session.add_all([space, provider_member, operator_member])
    await session.flush()
    session.add_all(
        [
            OrganizationMemberRole(
                organization_member_id=provider_member.id,
                role_code="provider_data_admin",
                granted_by=operator_user.id,
            ),
            OrganizationMemberRole(
                organization_member_id=operator_member.id,
                role_code="contract_signer",
                granted_by=operator_user.id,
            ),
        ]
    )

    provider_participant = SpaceParticipant(
        space_id=space.id,
        organization_id=provider_organization.id,
        admission_status="admitted",
        admitted_at=NOW - timedelta(days=1),
        created_by=operator_user.id,
    )
    operator_participant = SpaceParticipant(
        space_id=space.id,
        organization_id=operator_organization.id,
        admission_status="admitted",
        admitted_at=NOW - timedelta(days=1),
        created_by=operator_user.id,
    )
    session.add_all([provider_participant, operator_participant])
    await session.flush()
    session.add_all(
        [
            SpaceParticipantRole(
                space_participant_id=provider_participant.id,
                role_code="data_provider",
                granted_by=operator_user.id,
            ),
            SpaceParticipantRole(
                space_participant_id=operator_participant.id,
                role_code="space_operator",
                granted_by=operator_user.id,
            ),
        ]
    )
    await session.flush()
    return Graph(
        provider_user_id=provider_user.id,
        operator_user_id=operator_user.id,
        provider_organization_id=provider_organization.id,
        space_id=space.id,
    )


async def _new_database(*, provider_verified: bool = True):
    engine = _engine()
    await _create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        graph = await _seed_graph(session, provider_verified=provider_verified)
    return engine, factory, graph


async def _bind_and_prove(factory, graph: Graph):
    async with factory() as session, session.begin():
        user = await session.get(User, graph.provider_user_id)
        issued = await create_bind_challenge(
            session,
            policy=_policy(),
            user=user,
            space_id=graph.space_id,
            chain_id=31337,
            wallet_address=ACCOUNT.address,
            now=NOW,
        )
    async with factory() as session, session.begin():
        user = await session.get(User, graph.provider_user_id)
        binding = await verify_bind_challenge(
            session,
            policy=_policy(),
            user=user,
            challenge_id=issued.challenge_id,
            message=issued.message,
            signature=_signature(issued.message),
            now=NOW + timedelta(seconds=5),
        )
        binding_id = binding.id
    return issued, binding_id


def _receipt(binding: WalletIdentityBinding, **overrides: object):
    values = {
        "binding_id": binding.id,
        "chain_id": binding.chain_id,
        "contract_address": "0x" + "33" * 20,
        "token_id": "1",
        "holder_address": binding.wallet_address,
        "issuer_address": "0x" + "66" * 20,
        "role_code": binding.role_code,
        "credential_scope_digest": binding_scope_digest(binding),
        "credential_expires_at": NOW + timedelta(days=30),
        "transaction_hash": "0x" + "44" * 32,
        "block_number": 100,
        "block_hash": "0x" + "55" * 32,
        "log_index": 0,
        "confirmations": 12,
    }
    values.update(overrides)
    return VerifiedCredentialReceipt(**values)


async def _activate_binding(factory, graph: Graph) -> UUID:
    _, binding_id = await _bind_and_prove(factory, graph)
    async with factory() as session, session.begin():
        binding = await session.get(WalletIdentityBinding, binding_id)
        operator = await session.get(User, graph.operator_user_id)
        await approve_wallet_binding(
            session,
            binding_id=binding_id,
            operator_user=operator,
            receipt=_receipt(binding),
            minimum_confirmations=2,
            now=NOW + timedelta(seconds=10),
        )
    return binding_id


def test_bind_challenge_derives_role_and_persists_only_digests() -> None:
    asyncio.run(_test_bind_challenge_derives_role_and_persists_only_digests())


async def _test_bind_challenge_derives_role_and_persists_only_digests() -> None:
    engine, factory, graph = await _new_database()
    try:
        async with factory() as session, session.begin():
            user = await session.get(User, graph.provider_user_id)
            issued = await create_bind_challenge(
                session,
                policy=_policy(),
                user=user,
                space_id=graph.space_id,
                chain_id=31337,
                wallet_address=ACCOUNT.address,
                now=NOW,
            )
        parsed = parse_siwe_message(issued.message)
        async with factory() as session:
            challenge = await session.get(WalletAuthChallenge, issued.challenge_id)
            binding = await session.get(WalletIdentityBinding, issued.binding_id)
            assert challenge.nonce_digest != parsed.nonce
            assert challenge.message_digest != issued.message
            assert "nonce" not in WalletAuthChallenge.__table__.columns
            assert "message" not in WalletAuthChallenge.__table__.columns
            assert "signature" not in WalletAuthChallenge.__table__.columns
            assert binding.role_code == "data_provider"
            assert binding.status == "pending"
            assert binding.wallet_address == ACCOUNT.address.lower()
        assert "role" not in inspect.signature(create_bind_challenge).parameters
        assert "Role:" not in issued.message
    finally:
        await engine.dispose()


def test_wallet_capabilities_and_openapi_are_non_sensitive() -> None:
    application = FastAPI()
    application.state.settings = SimpleNamespace(
        web3_enabled=False,
        web3_chain_id=31337,
        deployment_mode="local",
        web3_rpc_url="http://secret.invalid",
    )
    disabled = asyncio.run(wallet_capabilities(_request_for(application)))
    assert disabled.enabled is False
    assert disabled.chain_name == "Local Hardhat"
    assert disabled.local_demo is False

    application.state.settings.web3_enabled = True
    application.state.web3_identity_policy = _policy()
    enabled = asyncio.run(wallet_capabilities(_request_for(application)))
    assert enabled.enabled is True
    assert enabled.chain_id == 31337
    assert enabled.local_demo is True
    assert "不替代" in enabled.notice

    application.include_router(wallet_router, prefix="/api/v1")
    schema = application.openapi()
    paths = schema["paths"]
    assert "/api/v1/auth/wallet/capabilities" in paths
    assert "/api/v1/auth/wallet/challenge" in paths
    assert "/api/v1/auth/wallet/verify" in paths
    serialized = json.dumps(schema, ensure_ascii=False)
    assert "web3_rpc_url" not in serialized
    assert "wallet_binding_id" not in serialized
    assert "role_code" not in serialized


def test_bind_verify_consumes_once_and_rejects_signature_tampering() -> None:
    asyncio.run(_test_bind_verify_consumes_once_and_rejects_signature_tampering())


async def _test_bind_verify_consumes_once_and_rejects_signature_tampering() -> None:
    engine, factory, graph = await _new_database()
    try:
        async with factory() as session, session.begin():
            user = await session.get(User, graph.provider_user_id)
            issued = await create_bind_challenge(
                session,
                policy=_policy(),
                user=user,
                space_id=graph.space_id,
                chain_id=31337,
                wallet_address=ACCOUNT.address,
                now=NOW,
            )
        async with factory() as session:
            with pytest.raises(WalletIdentityError) as invalid:
                async with session.begin():
                    user = await session.get(User, graph.provider_user_id)
                    await verify_bind_challenge(
                        session,
                        policy=_policy(),
                        user=user,
                        challenge_id=issued.challenge_id,
                        message=issued.message,
                        signature=_signature(issued.message, OTHER_PRIVATE_KEY),
                        now=NOW + timedelta(seconds=5),
                    )
            assert invalid.value.code == "siwe_invalid"
        async with factory() as session:
            challenge = await session.get(WalletAuthChallenge, issued.challenge_id)
            assert challenge.consumed_at is None

        async with factory() as session, session.begin():
            user = await session.get(User, graph.provider_user_id)
            await verify_bind_challenge(
                session,
                policy=_policy(),
                user=user,
                challenge_id=issued.challenge_id,
                message=issued.message,
                signature=_signature(issued.message),
                now=NOW + timedelta(seconds=5),
            )
        async with factory() as session:
            with pytest.raises(WalletIdentityError) as replay:
                async with session.begin():
                    user = await session.get(User, graph.provider_user_id)
                    await verify_bind_challenge(
                        session,
                        policy=_policy(),
                        user=user,
                        challenge_id=issued.challenge_id,
                        message=issued.message,
                        signature=_signature(issued.message),
                        now=NOW + timedelta(seconds=6),
                    )
            assert replay.value.code == "challenge_replayed"
    finally:
        await engine.dispose()


def test_approval_requires_wallet_proof_and_verified_organization() -> None:
    asyncio.run(_test_approval_requires_wallet_proof_and_verified_organization())


async def _test_approval_requires_wallet_proof_and_verified_organization() -> None:
    engine, factory, graph = await _new_database(provider_verified=False)
    try:
        _, binding_id = await _bind_and_prove(factory, graph)
        async with factory() as session:
            with pytest.raises(WalletIdentityError) as rejected:
                async with session.begin():
                    binding = await session.get(WalletIdentityBinding, binding_id)
                    operator = await session.get(User, graph.operator_user_id)
                    await approve_wallet_binding(
                        session,
                        binding_id=binding_id,
                        operator_user=operator,
                        receipt=_receipt(binding),
                        now=NOW + timedelta(seconds=10),
                    )
            assert rejected.value.code == "wallet_not_eligible"

        async with factory() as session, session.begin():
            organization = await session.get(
                Organization, graph.provider_organization_id
            )
            organization.verification_status = "verified"
        async with factory() as session, session.begin():
            binding = await session.get(WalletIdentityBinding, binding_id)
            operator = await session.get(User, graph.operator_user_id)
            approved = await approve_wallet_binding(
                session,
                binding_id=binding_id,
                operator_user=operator,
                receipt=_receipt(binding),
                minimum_confirmations=2,
                now=NOW + timedelta(seconds=10),
            )
            assert approved.status == "active"
            assert approved.verified_by == operator.id
    finally:
        await engine.dispose()


def test_login_uses_active_binding_and_injected_session_factory() -> None:
    asyncio.run(_test_login_uses_active_binding_and_injected_session_factory())


async def _test_login_uses_active_binding_and_injected_session_factory() -> None:
    engine, factory, graph = await _new_database()
    try:
        binding_id = await _activate_binding(factory, graph)
        async with factory() as session, session.begin():
            issued = await create_login_challenge(
                session,
                policy=_policy(),
                chain_id=31337,
                wallet_address=ACCOUNT.address,
                now=NOW + timedelta(minutes=1),
            )

        calls: list[tuple[UUID, UUID]] = []

        async def fake_session_factory(
            session,
            user,
            binding,
            now,
            requested_lifetime,
        ) -> WalletSessionIssue:
            calls.append((user.id, binding.id))
            return WalletSessionIssue(
                session_id=uuid4(),
                secret="s" * 40,
                expires_at=now + timedelta(hours=1),
            )

        async with factory() as session, session.begin():
            login = await verify_login_challenge(
                session,
                policy=_policy(),
                challenge_id=issued.challenge_id,
                message=issued.message,
                signature=_signature(issued.message),
                session_factory=fake_session_factory,
                now=NOW + timedelta(minutes=1, seconds=5),
            )
            assert login.binding_id == binding_id
            assert login.user_id == graph.provider_user_id
        assert calls == [(graph.provider_user_id, binding_id)]
        async with factory() as session:
            challenge = await session.get(WalletAuthChallenge, issued.challenge_id)
            assert challenge.consumed_at is not None
    finally:
        await engine.dispose()


def test_failed_session_factory_rolls_back_nonce_consumption() -> None:
    asyncio.run(_test_failed_session_factory_rolls_back_nonce_consumption())


async def _test_failed_session_factory_rolls_back_nonce_consumption() -> None:
    engine, factory, graph = await _new_database()
    try:
        await _activate_binding(factory, graph)
        async with factory() as session, session.begin():
            issued = await create_login_challenge(
                session,
                policy=_policy(),
                chain_id=31337,
                wallet_address=ACCOUNT.address,
                now=NOW + timedelta(minutes=1),
            )

        async def fail_session_factory(*args, **kwargs):
            raise RuntimeError("session persistence failed")

        with pytest.raises(RuntimeError, match="session persistence failed"):
            async with factory() as session, session.begin():
                await verify_login_challenge(
                    session,
                    policy=_policy(),
                    challenge_id=issued.challenge_id,
                    message=issued.message,
                    signature=_signature(issued.message),
                    session_factory=fail_session_factory,
                    now=NOW + timedelta(minutes=1, seconds=5),
                )
        async with factory() as session:
            challenge = await session.get(WalletAuthChallenge, issued.challenge_id)
            assert challenge.consumed_at is None
    finally:
        await engine.dispose()


def test_receipt_scope_and_confirmation_count_fail_closed() -> None:
    asyncio.run(_test_receipt_scope_and_confirmation_count_fail_closed())


async def _test_receipt_scope_and_confirmation_count_fail_closed() -> None:
    engine, factory, graph = await _new_database()
    try:
        _, binding_id = await _bind_and_prove(factory, graph)
        async with factory() as session:
            with pytest.raises(WalletIdentityError) as invalid:
                async with session.begin():
                    binding = await session.get(WalletIdentityBinding, binding_id)
                    operator = await session.get(User, graph.operator_user_id)
                    await approve_wallet_binding(
                        session,
                        binding_id=binding_id,
                        operator_user=operator,
                        receipt=_receipt(binding, confirmations=1),
                        minimum_confirmations=2,
                        now=NOW + timedelta(seconds=10),
                    )
            assert invalid.value.code == "invalid_receipt"
        async with factory() as session:
            with pytest.raises(WalletIdentityError) as mismatch:
                async with session.begin():
                    binding = await session.get(WalletIdentityBinding, binding_id)
                    operator = await session.get(User, graph.operator_user_id)
                    await approve_wallet_binding(
                        session,
                        binding_id=binding_id,
                        operator_user=operator,
                        receipt=_receipt(
                            binding,
                            credential_scope_digest="sha256:" + "9" * 64,
                        ),
                        minimum_confirmations=2,
                        now=NOW + timedelta(seconds=10),
                    )
            assert mismatch.value.code == "receipt_mismatch"
    finally:
        await engine.dispose()


def test_revoke_requires_operator_and_revokes_wallet_sessions() -> None:
    asyncio.run(_test_revoke_requires_operator_and_revokes_wallet_sessions())


async def _test_revoke_requires_operator_and_revokes_wallet_sessions() -> None:
    engine, factory, graph = await _new_database()
    try:
        binding_id = await _activate_binding(factory, graph)
        async with factory() as session, session.begin():
            issued = await create_login_challenge(
                session,
                policy=_policy(),
                chain_id=31337,
                wallet_address=ACCOUNT.address,
                now=NOW + timedelta(minutes=1),
            )
        async with factory() as session, session.begin():
            login = await verify_login_challenge(
                session,
                policy=_policy(),
                challenge_id=issued.challenge_id,
                message=issued.message,
                signature=_signature(issued.message),
                now=NOW + timedelta(minutes=1, seconds=5),
            )
            session_id = login.session.session_id

        async with factory() as session:
            with pytest.raises(WalletIdentityError) as unauthorized:
                async with session.begin():
                    provider = await session.get(User, graph.provider_user_id)
                    await revoke_wallet_binding(
                        session,
                        binding_id=binding_id,
                        operator_user=provider,
                        reason="Compromised wallet",
                        now=NOW + timedelta(minutes=2),
                    )
            assert unauthorized.value.code == "operator_required"
        async with factory() as session, session.begin():
            operator = await session.get(User, graph.operator_user_id)
            revoked = await revoke_wallet_binding(
                session,
                binding_id=binding_id,
                operator_user=operator,
                reason="Compromised wallet",
                now=NOW + timedelta(minutes=2),
            )
            assert revoked.status == "revoked"
        async with factory() as session:
            login_session = await session.get(LocalDemoSession, session_id)
            assert login_session.revoked_at is not None
    finally:
        await engine.dispose()
