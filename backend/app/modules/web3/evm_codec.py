from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Sequence
from uuid import UUID


HEX_32_PATTERN = re.compile(r"^0x[0-9a-f]{64}$")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")

FUNCTION_SELECTORS = {
    "approve": "095ea7b3",
    "issue": "ce9c9c46",
    "revokeCredential": "3d3162b0",
    "registerAgreement": "fb9fc43b",
    "confirm": "797af627",
    "activate": "59db6e85",
    "openEscrow": "9cd3d8d6",
    "attestExecution": "5e25e828",
    "attestDelivery": "be4ca0c9",
    "refund": "7249fbb6",
}

EVENT_TOPICS = {
    "CredentialIssued": "0xfd56af81dba93c0c62083c71a0bb0cdbfe9362d7cbd54c5627ed481390b7aa30",
    "CredentialRevoked": "0xcc681b79338b510ca365402bbe484c0263c702930a4b0ca0ed6be80e8f484c6e",
    "AgreementRegistered": "0x0b8e183ea38db41c28fee2ccae32aaa078ed7f8eb9b65c678caf95cb6daad60e",
    "AgreementConfirmed": "0x73982bfca06dc575f3b259853877283e78acc5548f65c54a51a1dad20f4d15fa",
    "AgreementFullyConfirmed": "0xfba0234770e8fc472ae7390357a4dcc6e4fb73024f523dc38d272e6dab552431",
    "AgreementActivated": "0xde0c7ec1bbadfb28cfb64d45b81845de4a8b3dbd7346e07a9fe9166df5b9f6b4",
    "EscrowFunded": "0xbf96dd82842c221dbd4985fc04f3da09da30badfc10151b6996644169f05bd4c",
    "ExecutionAttested": "0x9fe62276c6c8f9fbf50db560f678f87dc5a6d1197e727bae3e8125eea46b5ae8",
    "DeliveryAttested": "0xfad4eaff843f3c7debc54b2d856a28a4b35ccff3598fdc8efd0de5419a77e903",
    "EscrowSettled": "0xcbd5b919c5b27c031a9a5a969839f37e2a5009b723faa4ae57164fc5f17187af",
    "EscrowRefunded": "0xfc31a7ddbe933aa6e67f3c98c183fbc87addd2b602fcfb10238d2f85cf026617",
}

CALL_ARGUMENT_TYPES = {
    "approve": ("address", "uint256"),
    "issue": (
        "address",
        "bytes32",
        "bytes32",
        "bytes32",
        "bytes32",
        "uint64",
    ),
    "revokeCredential": ("uint256", "bytes32"),
    "registerAgreement": (
        "bytes32",
        "bytes32",
        "address",
        "address",
        "address",
        "address",
        "uint64",
        "uint64",
    ),
    "confirm": ("bytes32",),
    "activate": ("bytes32",),
    "openEscrow": (
        "bytes32",
        "bytes32",
        "bytes32",
        "uint128",
        "uint128",
        "uint128",
        "uint64",
    ),
    "attestExecution": ("bytes32", "bytes32"),
    "attestDelivery": ("bytes32", "bytes32"),
    "refund": ("bytes32",),
}


class EvmCodecError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedEvent:
    name: str
    values: dict[str, str | int]


def digest_to_bytes32(value: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise EvmCodecError("digest must be sha256:<64 lowercase hex>")
    return "0x" + value[7:]


def stable_bytes32(namespace: str, value: UUID | str) -> str:
    if not namespace or not re.fullmatch(r"[a-z0-9._-]{1,64}", namespace):
        raise EvmCodecError("namespace is invalid")
    raw = str(value)
    if not raw:
        raise EvmCodecError("stable key value is empty")
    return "0x" + hashlib.sha256(f"{namespace}:{raw}".encode("utf-8")).hexdigest()


def normalize_address(value: str) -> str:
    if ADDRESS_PATTERN.fullmatch(value) is None:
        raise EvmCodecError("address must contain exactly 20 hex bytes")
    return value.lower()


def encode_contract_call(method: str, arguments: Sequence[object]) -> str:
    types = CALL_ARGUMENT_TYPES.get(method)
    selector = FUNCTION_SELECTORS.get(method)
    if types is None or selector is None:
        raise EvmCodecError("unsupported MedTrust contract method")
    if len(arguments) != len(types):
        raise EvmCodecError("contract argument count mismatch")
    words = [_encode_word(kind, value) for kind, value in zip(types, arguments, strict=True)]
    return "0x" + selector + "".join(words)


def decode_medtrust_event(
    expected_name: str,
    topics: Sequence[str],
    data: str,
) -> DecodedEvent:
    expected_topic = EVENT_TOPICS.get(expected_name)
    normalized_topics = [_hex32(item, "event topic") for item in topics]
    if expected_topic is None or not normalized_topics or normalized_topics[0] != expected_topic:
        raise EvmCodecError("event signature does not match expected MedTrust event")
    words = _data_words(data)

    if expected_name == "CredentialIssued":
        _shape(normalized_topics, words, topic_count=4, word_count=5)
        values = {
            "token_id": _uint(normalized_topics[1][2:], 256),
            "holder": _topic_address(normalized_topics[2]),
            "issuer": _topic_address(normalized_topics[3]),
            "role": "0x" + words[0],
            "did_hash": "0x" + words[1],
            "organization_digest": "0x" + words[2],
            "evidence_digest": "0x" + words[3],
            "expires_at": _uint(words[4], 64),
        }
    elif expected_name == "CredentialRevoked":
        _shape(normalized_topics, words, topic_count=4, word_count=2)
        values = {
            "token_id": _uint(normalized_topics[1][2:], 256),
            "holder": _topic_address(normalized_topics[2]),
            "issuer": _topic_address(normalized_topics[3]),
            "role": "0x" + words[0],
            "reason_digest": "0x" + words[1],
        }
    elif expected_name == "AgreementConfirmed":
        _shape(normalized_topics, words, topic_count=4, word_count=1)
        values = {
            "agreement_key": normalized_topics[1],
            "terms_digest": normalized_topics[2],
            "party_wallet": _topic_address(normalized_topics[3]),
            "confirmation_bitmap": _uint(words[0], 8),
        }
    elif expected_name in {"AgreementFullyConfirmed", "AgreementActivated"}:
        _shape(normalized_topics, words, topic_count=3, word_count=0)
        values = {
            "agreement_key": normalized_topics[1],
            "terms_digest": normalized_topics[2],
        }
    elif expected_name == "AgreementRegistered":
        _shape(normalized_topics, words, topic_count=3, word_count=6)
        values = {
            "agreement_key": normalized_topics[1],
            "terms_digest": normalized_topics[2],
            "requester": _word_address(words[0]),
            "data_provider": _word_address(words[1]),
            "model_provider": _word_address(words[2]),
            "operator": _word_address(words[3]),
            "valid_from": _uint(words[4], 64),
            "valid_until": _uint(words[5], 64),
        }
    elif expected_name == "EscrowFunded":
        _shape(normalized_topics, words, topic_count=4, word_count=3)
        values = {
            "escrow_order_key": normalized_topics[1],
            "agreement_key": normalized_topics[2],
            "task_digest": normalized_topics[3],
            "payer": _word_address(words[0]),
            "total_amount": _uint(words[1], 256),
            "refund_after": _uint(words[2], 64),
        }
    elif expected_name in {"ExecutionAttested", "DeliveryAttested"}:
        _shape(normalized_topics, words, topic_count=4, word_count=0)
        values = {
            "escrow_order_key": normalized_topics[1],
            "proof_digest": normalized_topics[2],
            "attestor": _word_address(normalized_topics[3][2:]),
        }
    elif expected_name == "EscrowSettled":
        _shape(normalized_topics, words, topic_count=4, word_count=3)
        values = {
            "escrow_order_key": normalized_topics[1],
            "execution_digest": normalized_topics[2],
            "delivery_digest": normalized_topics[3],
            "data_fee": _uint(words[0], 256),
            "model_fee": _uint(words[1], 256),
            "platform_fee": _uint(words[2], 256),
        }
    elif expected_name == "EscrowRefunded":
        _shape(normalized_topics, words, topic_count=3, word_count=1)
        values = {
            "escrow_order_key": normalized_topics[1],
            "payer": _topic_address(normalized_topics[2]),
            "amount": _uint(words[0], 256),
        }
    else:
        raise EvmCodecError("event decoder is not implemented")
    return DecodedEvent(name=expected_name, values=values)


def _encode_word(kind: str, value: object) -> str:
    if kind == "bytes32":
        return _hex32(str(value), "bytes32")[2:]
    if kind == "address":
        return "0" * 24 + normalize_address(str(value))[2:]
    if kind.startswith("uint"):
        bits = int(kind[4:])
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < (1 << bits):
            raise EvmCodecError(f"{kind} value is outside its range")
        return f"{value:064x}"
    raise EvmCodecError("unsupported static ABI type")


def _hex32(value: str, label: str) -> str:
    normalized = value.lower()
    if HEX_32_PATTERN.fullmatch(normalized) is None:
        raise EvmCodecError(f"{label} must contain exactly 32 hex bytes")
    return normalized


def _data_words(data: str) -> list[str]:
    if not isinstance(data, str) or not data.startswith("0x"):
        raise EvmCodecError("event data must be hex")
    payload = data[2:].lower()
    if re.fullmatch(r"[0-9a-f]*", payload) is None or len(payload) % 64:
        raise EvmCodecError("event data is not 32-byte aligned")
    return [payload[index : index + 64] for index in range(0, len(payload), 64)]


def _shape(
    topics: Sequence[str], words: Sequence[str], *, topic_count: int, word_count: int
) -> None:
    if len(topics) != topic_count or len(words) != word_count:
        raise EvmCodecError("event log shape does not match its ABI")


def _topic_address(topic: str) -> str:
    if topic[2:26] != "0" * 24:
        raise EvmCodecError("indexed address has non-zero padding")
    return "0x" + topic[-40:]


def _word_address(word: str) -> str:
    if word[:24] != "0" * 24:
        raise EvmCodecError("address word has non-zero padding")
    return "0x" + word[-40:]


def _uint(word: str, bits: int) -> int:
    value = int(word, 16)
    if value >= (1 << bits):
        raise EvmCodecError(f"uint{bits} word has non-zero high bits")
    return value
