from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.routes import web3_identity
from app.core.config import Settings
from app.main import create_app
from app.modules.audit import canonical_json_digest_v1
from app.modules.identity.models import User
from app.modules.web3.credentials import prepare_credential_issue
from app.modules.web3.models import ChainEventReceipt, WalletIdentityBinding
from app.modules.web3.rpc import ReceiptStatus, ReceiptVerification


NOW = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
SPACE_ID = UUID("00000000-0000-0000-0000-000000000101")
OPERATOR_ID = UUID("00000000-0000-0000-0000-000000000102")
TARGET_ID = UUID("00000000-0000-0000-0000-000000000103")
OPERATOR_BINDING_ID = UUID("00000000-0000-0000-0000-000000000104")
CONTRACT = "0x" + "11" * 20
TX_HASH = "0x" + "22" * 32
BLOCK_HASH = "0x" + "33" * 32
SPACE_SCOPE_DIGEST = "0x" + "aa" * 32


def _settings(*, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        web3_enabled=enabled,
        web3_chain_id=31337,
        web3_space_scope_digest=SPACE_SCOPE_DIGEST,
        web3_role_credential_address=CONTRACT,
        web3_credential_issuer_address="0x" + "66" * 20,
        web3_rpc_url="http://127.0.0.1:8545",
        web3_rpc_timeout_seconds=1.0,
        web3_required_confirmations=2,
    )


def _request(settings: SimpleNamespace | None = None) -> Request:
    application = FastAPI()
    application.state.settings = settings or _settings()
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "app": application,
        }
    )


def _target_binding() -> WalletIdentityBinding:
    return WalletIdentityBinding(
        id=TARGET_ID,
        space_id=SPACE_ID,
        user_id=UUID("00000000-0000-0000-0000-000000000105"),
        organization_id=UUID("00000000-0000-0000-0000-000000000106"),
        role_code="data_provider",
        chain_id=31337,
        wallet_address="0x" + "44" * 20,
        did_uri="did:pkh:eip155:31337:0x" + "44" * 20,
        status="pending",
        identity_evidence_digest="sha256:" + "55" * 32,
        row_version=1,
    )


def _operator_binding() -> WalletIdentityBinding:
    binding = _target_binding()
    binding.id = OPERATOR_BINDING_ID
    binding.user_id = OPERATOR_ID
    binding.organization_id = UUID("00000000-0000-0000-0000-000000000107")
    binding.role_code = "space_operator"
    binding.wallet_address = "0x" + "66" * 20
    binding.did_uri = "did:pkh:eip155:31337:0x" + "66" * 20
    binding.status = "active"
    binding.credential_contract_address = CONTRACT
    binding.credential_token_id = "1"
    binding.credential_scope_digest = "sha256:" + "77" * 32
    binding.credential_expires_at = NOW + timedelta(days=100)
    binding.verified_at = NOW
    binding.verified_by = OPERATOR_ID
    return binding


class _Transaction(AbstractAsyncContextManager):
    def __init__(self, session: "_Session") -> None:
        self.session = session

    async def __aenter__(self):
        assert not self.session.in_transaction
        self.session.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.session.in_transaction = False
        return False


class _Session:
    def __init__(
        self,
        *,
        target: WalletIdentityBinding,
        receipt: ChainEventReceipt | None = None,
    ) -> None:
        self.target = target
        self.receipt = receipt
        self.in_transaction = False

    def begin(self) -> _Transaction:
        return _Transaction(self)

    async def get(self, model, identifier):
        if model is WalletIdentityBinding and identifier == self.target.id:
            return self.target
        if model is User and identifier == OPERATOR_ID:
            return User(id=OPERATOR_ID)
        return None

    async def scalar(self, statement):
        if self.target.status == "pending":
            return self.target
        return self.receipt


async def _operator_context(session, request):
    actor = SimpleNamespace(role="space_operator", user_id=OPERATOR_ID)
    return actor, _operator_binding(), User(id=OPERATOR_ID)


async def _expiry(session, binding):
    return NOW + timedelta(days=90)


async def _qualified(*args, **kwargs):
    return User(id=TARGET_ID)


async def _wallet_proof(*args, **kwargs):
    return None


def test_openapi_exposes_phase55_binding_and_credential_operations() -> None:
    application = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            deployment_mode="local",
            web3_enabled=True,
            web3_space_scope_digest=SPACE_SCOPE_DIGEST,
            web3_role_credential_address=CONTRACT,
            web3_agreement_registry_address="0x" + "12" * 20,
            web3_escrow_address="0x" + "13" * 20,
            web3_settlement_token_address="0x" + "14" * 20,
            web3_credential_issuer_address="0x" + "17" * 20,
            web3_execution_attestor_address="0x" + "15" * 20,
            web3_delivery_attestor_address="0x" + "16" * 20,
        )
    )
    paths = application.openapi()["paths"]
    assert "/api/v1/auth/wallet/bindings" in paths
    assert "/api/v1/auth/wallet/bindings/review-queue" in paths
    assert (
        "/api/v1/auth/wallet/bindings/{binding_id}/credential/prepare" in paths
    )
    assert (
        "/api/v1/auth/wallet/bindings/{binding_id}/credential/receipts" in paths
    )
    assert (
        "/api/v1/auth/wallet/bindings/{binding_id}/credential/local-demo-bootstrap"
        in paths
    )
    assert (
        "/api/v1/auth/wallet/bindings/{binding_id}/credential/revoke/prepare"
        in paths
    )
    assert (
        "/api/v1/auth/wallet/bindings/{binding_id}/credential/revoke/receipts"
        in paths
    )


def test_binding_routes_fail_closed_before_database_access_when_disabled() -> None:
    application = create_app(
        Settings(_env_file=None, app_env="test", web3_enabled=False)
    )
    with TestClient(application) as client:
        listed = client.get("/api/v1/auth/wallet/bindings")
        prepared = client.post(
            f"/api/v1/auth/wallet/bindings/{TARGET_ID}/credential/prepare"
        )
    assert listed.status_code == 503
    assert prepared.status_code == 503
    assert listed.json()["detail"] == "Web3 演示功能尚未启用"


def test_prepare_credential_uses_server_scope_expiry_and_operator_wallet(
    monkeypatch,
) -> None:
    target = _target_binding()
    session = _Session(target=target)
    monkeypatch.setattr(web3_identity, "_operator_context", _operator_context)
    monkeypatch.setattr(web3_identity, "_credential_expiry", _expiry)
    monkeypatch.setattr(
        web3_identity, "validate_credential_issuance_eligibility", _qualified
    )

    response = asyncio.run(
        web3_identity.prepare_binding_credential(
            TARGET_ID, _request(), session
        )
    )

    expected = prepare_credential_issue(
        target,
        credential_contract_address=CONTRACT,
        expires_at=NOW + timedelta(days=90),
        now=NOW,
    )
    assert response["transaction"]["from"] == _operator_binding().wallet_address
    assert response["transaction"]["data"] == expected.data
    assert response["holder_wallet"] == target.wallet_address
    assert response["platform_role"] == "data_provider"
    assert "不替代法定 KYC" in response["security_boundary"]


def test_receipt_rpc_happens_outside_transaction_then_applies_once(
    monkeypatch,
) -> None:
    target = _target_binding()
    session = _Session(target=target)
    monkeypatch.setattr(web3_identity, "_operator_context", _operator_context)
    monkeypatch.setattr(web3_identity, "_credential_expiry", _expiry)
    monkeypatch.setattr(
        web3_identity, "validate_credential_issuance_eligibility", _qualified
    )
    calls: list[str] = []

    async def fake_verify(request, *, prepared_call, payload):
        assert session.in_transaction is False
        calls.append("rpc")
        return ReceiptVerification(
            status=ReceiptStatus.CONFIRMED,
            chain_id=31337,
            tx_hash=TX_HASH,
            expected_contract_address=CONTRACT,
            expected_event_topic=web3_identity.EVENT_TOPICS["CredentialIssued"],
            expected_log_index=payload.log_index,
            minimum_confirmations=2,
            block_number=10,
            block_hash=BLOCK_HASH,
            head_block_number=11,
            confirmations=2,
            event_topics=(web3_identity.EVENT_TOPICS["CredentialIssued"],),
            event_data="0x",
        )

    async def fake_apply(
        session_arg,
        *,
        binding_id,
        prepared_call,
        operator_user,
        issuer_address,
        receipt,
        minimum_confirmations,
    ):
        assert session_arg.in_transaction is True
        assert binding_id == TARGET_ID
        assert operator_user.id == OPERATOR_ID
        assert issuer_address == _settings().web3_credential_issuer_address
        assert minimum_confirmations == 2
        calls.append("apply")
        target.status = "active"
        target.credential_contract_address = CONTRACT
        target.credential_token_id = "7"
        target.credential_scope_digest = prepared_call.credential_scope_digest
        target.credential_expires_at = prepared_call.expires_at
        return SimpleNamespace(
            chain_event_receipt_id=UUID(
                "00000000-0000-0000-0000-000000000108"
            ),
            idempotent_replay=False,
        )

    monkeypatch.setattr(web3_identity, "_verify_credential_receipt", fake_verify)
    monkeypatch.setattr(web3_identity, "apply_credential_issued_receipt", fake_apply)

    response = asyncio.run(
        web3_identity.synchronize_binding_credential(
            TARGET_ID,
            web3_identity.CredentialReceiptRequest(
                transaction_hash=TX_HASH, log_index=2
            ),
            _request(),
            session,
        )
    )

    assert calls == ["rpc", "apply"]
    assert response["applied"] is True
    assert response["idempotent_replay"] is False
    assert response["status"] == "active"


def test_applied_receipt_replay_is_idempotent_without_second_rpc(
    monkeypatch,
) -> None:
    target = _target_binding()
    prepared = prepare_credential_issue(
        target,
        credential_contract_address=CONTRACT,
        expires_at=NOW + timedelta(days=90),
        now=NOW,
    )
    target.status = "active"
    target.credential_contract_address = CONTRACT
    target.credential_token_id = "7"
    target.credential_scope_digest = prepared.credential_scope_digest
    target.credential_expires_at = prepared.expires_at
    target.verified_at = NOW
    target.verified_by = OPERATOR_ID
    event_id = UUID("00000000-0000-0000-0000-000000000109")
    payload_snapshot = {
        "schema_version": "medtrust.role-credential-issued/v1",
        "binding_id": str(TARGET_ID),
        "token_id": "7",
        "holder_address": target.wallet_address,
        "issuer_address": _operator_binding().wallet_address,
        "role_code": target.role_code,
        "credential_scope_digest": prepared.credential_scope_digest,
        "credential_expires_at": prepared.expires_at.isoformat().replace(
            "+00:00", "Z"
        ),
    }
    event = ChainEventReceipt(
        id=event_id,
        space_id=SPACE_ID,
        chain_id=31337,
        contract_address=CONTRACT,
        transaction_hash=TX_HASH,
        log_index=2,
        block_number=10,
        block_hash=BLOCK_HASH,
        event_name="CredentialIssued",
        subject_type="wallet_identity_binding",
        subject_key=str(TARGET_ID),
        actor_wallet=_operator_binding().wallet_address,
        payload_snapshot=payload_snapshot,
        payload_digest=canonical_json_digest_v1(payload_snapshot),
        confirmations=2,
        status="applied",
        finalized_at=NOW,
        applied_at=NOW,
    )
    session = _Session(target=target, receipt=event)
    monkeypatch.setattr(web3_identity, "_operator_context", _operator_context)

    async def should_not_verify(*args, **kwargs):
        raise AssertionError("idempotent replay must not re-enter RPC")

    monkeypatch.setattr(
        web3_identity, "_verify_credential_receipt", should_not_verify
    )
    response = asyncio.run(
        web3_identity.synchronize_binding_credential(
            TARGET_ID,
            web3_identity.CredentialReceiptRequest(
                transaction_hash=TX_HASH, log_index=2
            ),
            _request(),
            session,
        )
    )
    assert response["chain_event_receipt_id"] == str(event_id)
    assert response["idempotent_replay"] is True
    assert response["applied"] is True
