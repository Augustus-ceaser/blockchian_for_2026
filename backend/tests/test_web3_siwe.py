from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from app.modules.web3.siwe import (
    SiweValidationError,
    build_siwe_message,
    generate_nonce,
    nonce_digest,
    parse_siwe_message,
    recover_eoa_address,
    validate_siwe_message,
    verify_siwe_eoa,
)

PRIVATE_KEY = "0x" + "11" * 32
OTHER_PRIVATE_KEY = "0x" + "22" * 32
ACCOUNT = Account.from_key(PRIVATE_KEY)
OTHER_ACCOUNT = Account.from_key(OTHER_PRIVATE_KEY)
NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
DOMAIN = "127.0.0.1:5173"
URI = "http://127.0.0.1:5173/auth/wallet"
NONCE = "AbCd1234EfGh5678"


def _message(**overrides: object) -> str:
    values: dict[str, object] = {
        "domain": DOMAIN,
        "address": ACCOUNT.address,
        "uri": URI,
        "chain_id": 31337,
        "nonce": NONCE,
        "issued_at": NOW,
        "expiration_time": NOW + timedelta(minutes=5),
        "statement": "Sign in to MedTrust Space.",
    }
    values.update(overrides)
    return build_siwe_message(**values)  # type: ignore[arg-type]


def _validation_inputs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "expected_domain": DOMAIN,
        "expected_uri": URI,
        "expected_address": ACCOUNT.address,
        "expected_nonce": NONCE,
        "allowed_chain_ids": {31337},
        "now": NOW + timedelta(seconds=10),
    }
    values.update(overrides)
    return values


def _signature(message: str, private_key: str = PRIVATE_KEY) -> str:
    raw = Account.sign_message(
        encode_defunct(text=message), private_key=private_key
    ).signature.hex()
    return raw if raw.startswith("0x") else f"0x{raw}"


def _assert_error(code: str, callback: object) -> None:
    with pytest.raises(SiweValidationError) as captured:
        callback()  # type: ignore[operator]
    assert captured.value.code == code


def test_build_and_parse_canonical_siwe_profile() -> None:
    message = _message()
    parsed = parse_siwe_message(message)

    assert parsed.domain == DOMAIN
    assert parsed.address == ACCOUNT.address
    assert parsed.uri == URI
    assert parsed.chain_id == 31337
    assert parsed.nonce == NONCE
    assert parsed.issued_at == NOW
    assert parsed.expiration_time == NOW + timedelta(minutes=5)
    assert parsed.statement == "Sign in to MedTrust Space."
    assert "Role:" not in message


def test_message_without_statement_uses_canonical_blank_lines() -> None:
    message = _message(statement=None)
    assert f"{ACCOUNT.address}\n\n\nURI: {URI}" in message
    assert parse_siwe_message(message).statement is None


@pytest.mark.parametrize(
    "nonce",
    ["short", "invalid-123", "含中文12345678", "A" * 65],
)
def test_rejects_invalid_nonce(nonce: str) -> None:
    _assert_error("invalid_nonce", lambda: _message(nonce=nonce))


@pytest.mark.parametrize(
    "domain",
    ["https://example.com", "user@example.com", "example.com/path", "bad host"],
)
def test_rejects_unsafe_domain(domain: str) -> None:
    _assert_error("invalid_domain", lambda: _message(domain=domain))


def test_rejects_non_loopback_http_uri_and_cross_domain_uri() -> None:
    _assert_error(
        "invalid_uri",
        lambda: build_siwe_message(
            domain="example.com",
            address=ACCOUNT.address,
            uri="http://example.com/login",
            chain_id=1,
            nonce=NONCE,
            issued_at=NOW,
            expiration_time=NOW + timedelta(minutes=5),
        ),
    )
    _assert_error(
        "uri_domain_mismatch",
        lambda: _message(uri="http://localhost:5173/auth/wallet"),
    )


@pytest.mark.parametrize("chain_id", [0, -1, True, "1"])
def test_rejects_invalid_chain_id(chain_id: object) -> None:
    _assert_error("invalid_chain_id", lambda: _message(chain_id=chain_id))


def test_rejects_invalid_or_non_checksummed_message_address() -> None:
    _assert_error("invalid_address", lambda: _message(address="0x1234"))
    message = _message().replace(ACCOUNT.address, ACCOUNT.address.lower(), 1)
    _assert_error("invalid_address", lambda: parse_siwe_message(message))


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"expected_domain": "localhost:5173"}, "domain_mismatch"),
        (
            {"expected_uri": "http://127.0.0.1:5173/auth/other"},
            "uri_mismatch",
        ),
        ({"expected_address": OTHER_ACCOUNT.address}, "address_mismatch"),
        ({"expected_nonce": "Different123456"}, "nonce_mismatch"),
        ({"allowed_chain_ids": {1}}, "chain_mismatch"),
    ],
)
def test_rejects_expected_value_mismatches(
    override: dict[str, object], code: str
) -> None:
    inputs = _validation_inputs(**override)
    _assert_error(code, lambda: validate_siwe_message(_message(), **inputs))


def test_rejects_future_old_expired_and_overlong_messages() -> None:
    future = _message(
        issued_at=NOW + timedelta(minutes=1),
        expiration_time=NOW + timedelta(minutes=6),
    )
    _assert_error(
        "issued_in_future",
        lambda: validate_siwe_message(future, **_validation_inputs()),
    )

    old = _message(
        issued_at=NOW - timedelta(minutes=6),
        expiration_time=NOW + timedelta(minutes=1),
    )
    _assert_error(
        "issued_too_old",
        lambda: validate_siwe_message(old, **_validation_inputs()),
    )

    expired = _message(
        issued_at=NOW - timedelta(minutes=4),
        expiration_time=NOW,
    )
    _assert_error(
        "message_expired",
        lambda: validate_siwe_message(expired, **_validation_inputs()),
    )

    long_lived = _message(expiration_time=NOW + timedelta(minutes=11))
    _assert_error(
        "lifetime_too_long",
        lambda: validate_siwe_message(long_lived, **_validation_inputs()),
    )


def test_build_rejects_expiration_not_after_issued_time() -> None:
    _assert_error(
        "invalid_expiration_time",
        lambda: _message(expiration_time=NOW),
    )


def test_replay_check_uses_only_the_server_nonce_digest() -> None:
    digest = nonce_digest(NONCE)
    assert NONCE not in digest
    _assert_error(
        "nonce_replayed",
        lambda: validate_siwe_message(
            _message(),
            **_validation_inputs(consumed_nonce_digests={digest}),
        ),
    )


def test_parser_rejects_reordered_or_client_claimed_role_fields() -> None:
    message = _message()
    reordered = message.replace(
        "Chain ID: 31337\nNonce: AbCd1234EfGh5678",
        "Nonce: AbCd1234EfGh5678\nChain ID: 31337",
    )
    _assert_error("invalid_message", lambda: parse_siwe_message(reordered))
    _assert_error(
        "invalid_message",
        lambda: parse_siwe_message(f"{message}\nRole: space_operator"),
    )


def test_generates_high_entropy_alphanumeric_nonce() -> None:
    first = generate_nonce()
    second = generate_nonce()
    assert len(first) == 32
    assert first.isalnum()
    assert first != second


def test_recovers_and_verifies_eoa_personal_signature() -> None:
    message = _message()
    signature = _signature(message)

    assert recover_eoa_address(message, signature) == ACCOUNT.address
    assert (
        verify_siwe_eoa(message, signature, **_validation_inputs()).address
        == ACCOUNT.address
    )


def test_rejects_signature_from_another_eoa_and_malformed_signature() -> None:
    message = _message()
    _assert_error(
        "signature_mismatch",
        lambda: verify_siwe_eoa(
            message, _signature(message, OTHER_PRIVATE_KEY), **_validation_inputs()
        ),
    )
    _assert_error("invalid_signature", lambda: recover_eoa_address(message, "0x1234"))
