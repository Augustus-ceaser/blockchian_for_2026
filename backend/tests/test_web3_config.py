import pytest
from pydantic import ValidationError

from app.core.config import Settings


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


def test_web3_is_fail_closed_by_default() -> None:
    settings = Settings(_env_file=None, app_env="test")
    assert settings.web3_enabled is False
    assert settings.web3_chain_id == 31337
    assert settings.web3_settlement_token_decimals == 6


def test_local_web3_configuration_accepts_explicit_deployments() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        deployment_mode="local",
        web3_enabled=True,
        **ADDRESSES,
    )
    assert settings.web3_rpc_url == "http://127.0.0.1:8545"
    assert settings.web3_escrow_refund_seconds == 86_400
    assert settings.web3_space_scope_digest == "0x" + "aa" * 32


@pytest.mark.parametrize(
    "digest",
    ["", "0x" + "0" * 64, "0x1234", "sha256:" + "aa" * 32],
)
def test_enabled_web3_requires_nonzero_space_scope_digest(digest: str) -> None:
    with pytest.raises(ValidationError, match="space_scope_digest"):
        Settings(
            _env_file=None,
            app_env="test",
            deployment_mode="local",
            web3_enabled=True,
            **{**ADDRESSES, "web3_space_scope_digest": digest},
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("web3_settlement_token_decimals", 1, "decimals"),
        ("web3_escrow_refund_seconds", 599, "refund"),
        ("web3_rpc_timeout_seconds", 0.1, "timeout"),
    ],
)
def test_web3_numeric_security_bounds_fail_closed(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, app_env="test", **{field: value})


def test_remote_web3_rejects_local_chain_even_with_review_digest() -> None:
    with pytest.raises(ValidationError, match="local Hardhat chain"):
        Settings(
            _env_file=None,
            app_env="test",
            deployment_mode="remote-preview",
            web3_enabled=True,
            web3_rpc_url="https://rpc.example.com",
            web3_siwe_domain="demo.example.com",
            web3_siwe_uri="https://demo.example.com",
            web3_security_review_digest="sha256:" + "aa" * 32,
            **ADDRESSES,
        )
