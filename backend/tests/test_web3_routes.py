from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


ADDRESSES = {
    "web3_space_scope_digest": "0x" + "aa" * 32,
    "web3_role_credential_address": "0x" + "11" * 20,
    "web3_agreement_registry_address": "0x" + "22" * 20,
    "web3_escrow_address": "0x" + "33" * 20,
    "web3_settlement_token_address": "0x" + "44" * 20,
    "web3_credential_issuer_address": "0x" + "77" * 20,
    "web3_execution_attestor_address": "0x" + "55" * 20,
    "web3_delivery_attestor_address": "0x" + "66" * 20,
}


def test_web3_openapi_exposes_phase54_and_phase56_operations() -> None:
    application = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            deployment_mode="local",
            web3_enabled=True,
            **ADDRESSES,
        )
    )
    paths = application.openapi()["paths"]
    assert "/api/v1/auth/wallet/capabilities" in paths
    assert "/api/v1/web3/contract-revisions/{contract_revision_id}/agreement/prepare" in paths
    assert "/api/v1/web3/agreement-anchors/{chain_anchor_id}/receipts" in paths
    assert "/api/v1/web3/commercial-orders/{order_id}/escrow/prepare" in paths
    assert "/api/v1/web3/commercial-orders/{order_id}/escrow" in paths
    assert "/api/v1/web3/escrows/{escrow_binding_id}/settlement/prepare" in paths
    assert "/api/v1/web3/escrows/{escrow_binding_id}/local-demo-auto-settle" in paths


def test_wallet_capability_probe_is_fail_closed_when_disabled() -> None:
    application = create_app(
        Settings(_env_file=None, app_env="test", web3_enabled=False)
    )
    with TestClient(application) as client:
        response = client.get("/api/v1/auth/wallet/capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "chain_id": 31337,
        "chain_name": "Local Hardhat",
        "local_demo": False,
        "notice": "钱包签名登录未启用",
    }
