from uuid import UUID

import pytest

from app.modules.web3.evm_codec import (
    EVENT_TOPICS,
    EvmCodecError,
    decode_medtrust_event,
    digest_to_bytes32,
    encode_contract_call,
    stable_bytes32,
)


DIGEST = "sha256:" + "ab" * 32
BYTES32 = "0x" + "ab" * 32
ADDRESS = "0x" + "12" * 20
ISSUER = "0x" + "34" * 20


def _word(value: int) -> str:
    return f"{value:064x}"


def _address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:]


def test_digest_and_stable_key_are_canonical() -> None:
    assert digest_to_bytes32(DIGEST) == BYTES32
    assert stable_bytes32("contract-revision", UUID(int=1)) == stable_bytes32(
        "contract-revision", str(UUID(int=1))
    )
    with pytest.raises(EvmCodecError):
        digest_to_bytes32("sha256:" + "AB" * 32)


def test_encode_confirm_static_call() -> None:
    encoded = encode_contract_call("confirm", [BYTES32])
    assert encoded == "0x797af627" + "ab" * 32
    with pytest.raises(EvmCodecError):
        encode_contract_call("confirm", [])


def test_encode_erc20_approve_call() -> None:
    encoded = encode_contract_call("approve", [ADDRESS, 820_000_000])
    assert encoded == (
        "0x095ea7b3"
        + "0" * 24
        + ADDRESS[2:].lower()
        + f"{820_000_000:064x}"
    )


def test_encode_role_credential_issue_call() -> None:
    did_hash = "0x" + "01" * 32
    organization_digest = "0x" + "02" * 32
    role = "0x" + "03" * 32
    evidence_digest = "0x" + "04" * 32
    expires_at = 1_800_000_000

    encoded = encode_contract_call(
        "issue",
        [
            ADDRESS,
            did_hash,
            organization_digest,
            role,
            evidence_digest,
            expires_at,
        ],
    )
    assert encoded == (
        "0xce9c9c46"
        + "0" * 24
        + ADDRESS[2:].lower()
        + did_hash[2:]
        + organization_digest[2:]
        + role[2:]
        + evidence_digest[2:]
        + _word(expires_at)
    )


def test_encode_and_decode_role_credential_revocation() -> None:
    reason_digest = "0x" + "07" * 32
    encoded = encode_contract_call("revokeCredential", [17, reason_digest])
    assert encoded == "0x3d3162b0" + _word(17) + reason_digest[2:]

    result = decode_medtrust_event(
        "CredentialRevoked",
        [
            EVENT_TOPICS["CredentialRevoked"],
            "0x" + _word(17),
            _address_topic(ADDRESS),
            _address_topic(ISSUER),
        ],
        "0x" + "03" * 32 + reason_digest[2:],
    )
    assert result.values == {
        "token_id": 17,
        "holder": ADDRESS,
        "issuer": ISSUER,
        "role": "0x" + "03" * 32,
        "reason_digest": reason_digest,
    }

def test_decode_agreement_confirmation_exact_shape() -> None:
    result = decode_medtrust_event(
        "AgreementConfirmed",
        [
            EVENT_TOPICS["AgreementConfirmed"],
            "0x" + "01" * 32,
            BYTES32,
            _address_topic(ADDRESS),
        ],
        "0x" + _word(7),
    )
    assert result.values == {
        "agreement_key": "0x" + "01" * 32,
        "terms_digest": BYTES32,
        "party_wallet": ADDRESS,
        "confirmation_bitmap": 7,
    }


def test_decode_credential_issued_exact_shape() -> None:
    role = "0x" + "03" * 32
    did_hash = "0x" + "04" * 32
    organization_digest = "0x" + "05" * 32
    evidence_digest = "0x" + "06" * 32
    result = decode_medtrust_event(
        "CredentialIssued",
        [
            EVENT_TOPICS["CredentialIssued"],
            "0x" + _word(17),
            _address_topic(ADDRESS),
            _address_topic(ISSUER),
        ],
        (
            "0x"
            + role[2:]
            + did_hash[2:]
            + organization_digest[2:]
            + evidence_digest[2:]
            + _word(1_800_000_000)
        ),
    )

    assert result.values == {
        "token_id": 17,
        "holder": ADDRESS,
        "issuer": ISSUER,
        "role": role,
        "did_hash": did_hash,
        "organization_digest": organization_digest,
        "evidence_digest": evidence_digest,
        "expires_at": 1_800_000_000,
    }


def test_decode_credential_rejects_wrong_shape_and_uint64_padding() -> None:
    topics = [
        EVENT_TOPICS["CredentialIssued"],
        "0x" + _word(1),
        _address_topic(ADDRESS),
        _address_topic(ISSUER),
    ]
    data_words = ["03" * 32, "04" * 32, "05" * 32, "06" * 32]
    with pytest.raises(EvmCodecError):
        decode_medtrust_event(
            "CredentialIssued",
            topics[:-1],
            "0x" + "".join(data_words) + _word(1_800_000_000),
        )
    with pytest.raises(EvmCodecError):
        decode_medtrust_event(
            "CredentialIssued",
            topics,
            "0x" + "".join(data_words) + _word(1 << 64),
        )


def test_decode_rejects_wrong_topic_and_nonzero_address_padding() -> None:
    with pytest.raises(EvmCodecError):
        decode_medtrust_event(
            "AgreementConfirmed",
            ["0x" + "00" * 32, BYTES32, BYTES32, _address_topic(ADDRESS)],
            "0x" + _word(1),
        )
    with pytest.raises(EvmCodecError):
        decode_medtrust_event(
            "AgreementConfirmed",
            [
                EVENT_TOPICS["AgreementConfirmed"],
                BYTES32,
                BYTES32,
                "0x" + "ff" * 32,
            ],
            "0x" + _word(1),
        )


def test_decode_escrow_settlement_preserves_exact_fees() -> None:
    result = decode_medtrust_event(
        "EscrowSettled",
        [
            EVENT_TOPICS["EscrowSettled"],
            "0x" + "01" * 32,
            "0x" + "02" * 32,
            "0x" + "03" * 32,
        ],
        "0x" + _word(500) + _word(300) + _word(20),
    )
    assert result.values["data_fee"] == 500
    assert result.values["model_fee"] == 300
    assert result.values["platform_fee"] == 20


@pytest.mark.parametrize("event_name", ["ExecutionAttested", "DeliveryAttested"])
def test_decode_attestation_binds_the_actual_attestor(event_name: str) -> None:
    result = decode_medtrust_event(
        event_name,
        [
            EVENT_TOPICS[event_name],
            "0x" + "01" * 32,
            "0x" + "02" * 32,
            _address_topic(ADDRESS),
        ],
        "0x",
    )
    assert result.values == {
        "escrow_order_key": "0x" + "01" * 32,
        "proof_digest": "0x" + "02" * 32,
        "attestor": ADDRESS,
    }
