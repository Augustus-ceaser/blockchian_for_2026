"""Fail-closed verification of one Ethereum JSON-RPC event receipt.

The verifier accepts an already configured transport, never a URL from a web
request.  It deliberately checks only receipt inclusion and an exact event
anchor.  Event ``data`` is not decoded because doing so safely requires the
specific, audited contract ABI.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import json
import re
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_QUANTITY_RE = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z")
_DATA_RE = re.compile(r"0x(?:[0-9a-fA-F]{2})*\Z")
_MAX_CHAIN_ID = 2**256 - 1
_MAX_QUANTITY = 2**256 - 1

JsonRpcTransport = Callable[[str, Sequence[object]], object]


class RpcReceiptError(ValueError):
    """A malformed expectation, transport response, or RPC failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ReceiptStatus(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    INSUFFICIENT_CONFIRMATIONS = "insufficient_confirmations"
    REVERTED = "reverted"
    ORPHANED = "orphaned"
    WRONG_CHAIN = "wrong_chain"
    WRONG_CONTRACT = "wrong_contract"
    WRONG_EVENT = "wrong_event"


@dataclass(frozen=True, slots=True)
class ReceiptVerification:
    """Finality result plus the exact raw log for a fixed ABI decoder."""

    status: ReceiptStatus
    chain_id: int
    tx_hash: str
    expected_contract_address: str
    expected_event_topic: str
    expected_log_index: int
    minimum_confirmations: int
    block_number: int | None = None
    block_hash: str | None = None
    head_block_number: int | None = None
    confirmations: int = 0
    event_topics: tuple[str, ...] = ()
    event_data: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.status is ReceiptStatus.CONFIRMED


def _fail(code: str, message: str) -> NoReturn:
    raise RpcReceiptError(code, message)


def _normalize_hash(value: object, *, name: str, code: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail(code, f"{name} must be a 32-byte 0x-prefixed hexadecimal hash")
    return value.lower()


def _normalize_address(value: object, *, name: str, code: str) -> str:
    if not isinstance(value, str) or _ADDRESS_RE.fullmatch(value) is None:
        _fail(code, f"{name} must be a 20-byte 0x-prefixed hexadecimal address")
    return value.lower()


def _quantity(value: object, *, name: str, code: str) -> int:
    if not isinstance(value, str) or _QUANTITY_RE.fullmatch(value) is None:
        _fail(code, f"{name} must be a canonical JSON-RPC hexadecimal quantity")
    parsed = int(value[2:], 16)
    if parsed > _MAX_QUANTITY:
        _fail(code, f"{name} exceeds the uint256 range")
    return parsed


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("invalid_expectation", f"{name} must be a positive integer")
    return value


def _non_negative_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("invalid_expectation", f"{name} must be a non-negative integer")
    return value


def _rpc(transport: JsonRpcTransport, method: str, *params: object) -> object:
    try:
        return transport(method, params)
    except RpcReceiptError:
        raise
    except Exception as exc:
        raise RpcReceiptError("rpc_unavailable", "JSON-RPC request failed") from exc


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail("malformed_response", f"{name} must be a JSON object")
    return value


def _result(
    status: ReceiptStatus,
    *,
    chain_id: int,
    tx_hash: str,
    contract_address: str,
    event_topic: str,
    log_index: int,
    minimum_confirmations: int,
    block_number: int | None = None,
    block_hash: str | None = None,
    head_block_number: int | None = None,
    confirmations: int = 0,
    event_topics: tuple[str, ...] = (),
    event_data: str | None = None,
) -> ReceiptVerification:
    return ReceiptVerification(
        status=status,
        chain_id=chain_id,
        tx_hash=tx_hash,
        expected_contract_address=contract_address,
        expected_event_topic=event_topic,
        expected_log_index=log_index,
        minimum_confirmations=minimum_confirmations,
        block_number=block_number,
        block_hash=block_hash,
        head_block_number=head_block_number,
        confirmations=confirmations,
        event_topics=event_topics,
        event_data=event_data,
    )


def verify_transaction_receipt(
    *,
    transport: JsonRpcTransport,
    expected_chain_id: int,
    tx_hash: str,
    contract_address: str,
    event_topic: str,
    log_index: int,
    minimum_confirmations: int,
) -> ReceiptVerification:
    """Verify one exact event against the canonical chain returned by ``transport``.

    ``transport`` must be created by the server at startup from trusted
    configuration.  This function intentionally has no RPC URL argument, so a
    request body cannot turn it into an SSRF primitive.
    """

    chain_expectation = _positive_int(expected_chain_id, name="expected_chain_id")
    if chain_expectation > _MAX_CHAIN_ID:
        _fail("invalid_expectation", "expected_chain_id exceeds the uint256 range")
    expected_tx_hash = _normalize_hash(
        tx_hash, name="tx_hash", code="invalid_expectation"
    )
    expected_contract = _normalize_address(
        contract_address, name="contract_address", code="invalid_expectation"
    )
    expected_topic = _normalize_hash(
        event_topic, name="event_topic", code="invalid_expectation"
    )
    expected_log_index = _non_negative_int(log_index, name="log_index")
    if expected_log_index > _MAX_QUANTITY:
        _fail("invalid_expectation", "log_index exceeds the uint256 range")
    required_confirmations = _positive_int(
        minimum_confirmations, name="minimum_confirmations"
    )

    chain_id = _quantity(
        _rpc(transport, "eth_chainId"),
        name="eth_chainId result",
        code="malformed_response",
    )
    if chain_id < 1:
        _fail("malformed_response", "eth_chainId must be positive")
    base = {
        "chain_id": chain_id,
        "tx_hash": expected_tx_hash,
        "contract_address": expected_contract,
        "event_topic": expected_topic,
        "log_index": expected_log_index,
        "minimum_confirmations": required_confirmations,
    }
    if chain_id != chain_expectation:
        return _result(ReceiptStatus.WRONG_CHAIN, **base)

    receipt_value = _rpc(
        transport, "eth_getTransactionReceipt", expected_tx_hash
    )
    if receipt_value is None:
        return _result(ReceiptStatus.PENDING, **base)
    receipt = _mapping(receipt_value, name="transaction receipt")

    receipt_tx_hash = _normalize_hash(
        receipt.get("transactionHash"),
        name="receipt transactionHash",
        code="malformed_response",
    )
    if receipt_tx_hash != expected_tx_hash:
        _fail("transaction_mismatch", "receipt belongs to a different transaction")

    execution_status = _quantity(
        receipt.get("status"), name="receipt status", code="malformed_response"
    )
    if execution_status == 0:
        return _result(ReceiptStatus.REVERTED, **base)
    if execution_status != 1:
        _fail("malformed_response", "receipt status must be 0x0 or 0x1")

    receipt_contract = _normalize_address(
        receipt.get("to"), name="receipt to", code="malformed_response"
    )
    if receipt_contract != expected_contract:
        return _result(ReceiptStatus.WRONG_CONTRACT, **base)

    receipt_block_number = _quantity(
        receipt.get("blockNumber"),
        name="receipt blockNumber",
        code="malformed_response",
    )
    receipt_block_hash = _normalize_hash(
        receipt.get("blockHash"),
        name="receipt blockHash",
        code="malformed_response",
    )
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        _fail("malformed_response", "receipt logs must be a JSON array")

    matched_log: Mapping[str, object] | None = None
    for position, candidate in enumerate(logs):
        log = _mapping(candidate, name=f"receipt log {position}")
        candidate_index = _quantity(
            log.get("logIndex"),
            name=f"receipt log {position} logIndex",
            code="malformed_response",
        )
        if candidate_index == expected_log_index:
            if matched_log is not None:
                _fail("malformed_response", "receipt contains duplicate logIndex values")
            matched_log = log
    if matched_log is None:
        return _result(
            ReceiptStatus.WRONG_EVENT,
            block_number=receipt_block_number,
            block_hash=receipt_block_hash,
            **base,
        )

    log_tx_hash = _normalize_hash(
        matched_log.get("transactionHash"),
        name="event transactionHash",
        code="malformed_response",
    )
    if log_tx_hash != expected_tx_hash:
        _fail("transaction_mismatch", "event belongs to a different transaction")
    log_block_hash = _normalize_hash(
        matched_log.get("blockHash"),
        name="event blockHash",
        code="malformed_response",
    )
    log_block_number = _quantity(
        matched_log.get("blockNumber"),
        name="event blockNumber",
        code="malformed_response",
    )
    removed = matched_log.get("removed", False)
    if not isinstance(removed, bool):
        _fail("malformed_response", "event removed flag must be boolean")
    if (
        removed
        or log_block_hash != receipt_block_hash
        or log_block_number != receipt_block_number
    ):
        return _result(
            ReceiptStatus.ORPHANED,
            block_number=receipt_block_number,
            block_hash=receipt_block_hash,
            **base,
        )

    log_contract = _normalize_address(
        matched_log.get("address"),
        name="event address",
        code="malformed_response",
    )
    if log_contract != expected_contract:
        return _result(
            ReceiptStatus.WRONG_CONTRACT,
            block_number=receipt_block_number,
            block_hash=receipt_block_hash,
            **base,
        )

    topics = matched_log.get("topics")
    if not isinstance(topics, list):
        _fail("malformed_response", "event topics must be a JSON array")
    normalized_topics = [
        _normalize_hash(
            topic,
            name=f"event topic {position}",
            code="malformed_response",
        )
        for position, topic in enumerate(topics)
    ]
    if not normalized_topics or normalized_topics[0] != expected_topic:
        return _result(
            ReceiptStatus.WRONG_EVENT,
            block_number=receipt_block_number,
            block_hash=receipt_block_hash,
            **base,
        )

    raw_data = matched_log.get("data")
    if not isinstance(raw_data, str) or _DATA_RE.fullmatch(raw_data) is None:
        _fail("malformed_response", "event data must be byte-aligned hexadecimal")
    if len(raw_data) > 4098:
        _fail("malformed_response", "event data exceeds the MedTrust log limit")
    normalized_data = raw_data.lower()

    head_block_number = _quantity(
        _rpc(transport, "eth_blockNumber"),
        name="eth_blockNumber result",
        code="malformed_response",
    )
    canonical_value = _rpc(
        transport,
        "eth_getBlockByNumber",
        hex(receipt_block_number),
        False,
    )
    if canonical_value is None:
        return _result(
            ReceiptStatus.ORPHANED,
            block_number=receipt_block_number,
            block_hash=receipt_block_hash,
            head_block_number=head_block_number,
            **base,
        )
    canonical_block = _mapping(canonical_value, name="canonical block")
    canonical_number = _quantity(
        canonical_block.get("number"),
        name="canonical block number",
        code="malformed_response",
    )
    canonical_hash = _normalize_hash(
        canonical_block.get("hash"),
        name="canonical block hash",
        code="malformed_response",
    )
    if (
        canonical_number != receipt_block_number
        or canonical_hash != receipt_block_hash
        or head_block_number < receipt_block_number
    ):
        return _result(
            ReceiptStatus.ORPHANED,
            block_number=receipt_block_number,
            block_hash=receipt_block_hash,
            head_block_number=head_block_number,
            **base,
        )

    confirmations = head_block_number - receipt_block_number + 1
    status = (
        ReceiptStatus.CONFIRMED
        if confirmations >= required_confirmations
        else ReceiptStatus.INSUFFICIENT_CONFIRMATIONS
    )
    return _result(
        status,
        block_number=receipt_block_number,
        block_hash=receipt_block_hash,
        head_block_number=head_block_number,
        confirmations=confirmations,
        event_topics=tuple(normalized_topics),
        event_data=normalized_data,
        **base,
    )


@dataclass(frozen=True, slots=True)
class UrllibJsonRpcTransport:
    """Small HTTP transport constructed only from trusted server configuration.

    ``configured_rpc_url`` must come from deployment configuration.  Controllers
    must never populate it from query parameters, headers, or request bodies.
    """

    configured_rpc_url: str
    timeout_seconds: float = 5.0
    max_response_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if not isinstance(self.configured_rpc_url, str):
            _fail("invalid_rpc_configuration", "configured RPC URL must be text")
        try:
            parsed = urlsplit(self.configured_rpc_url)
            hostname = parsed.hostname
        except ValueError:
            _fail("invalid_rpc_configuration", "configured RPC URL is invalid")
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            _fail("invalid_rpc_configuration", "configured RPC URL is invalid")
        try:
            parsed.port
        except ValueError:
            _fail("invalid_rpc_configuration", "configured RPC URL port is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            _fail("invalid_rpc_configuration", "RPC timeout must be positive")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes < 1
        ):
            _fail("invalid_rpc_configuration", "RPC response limit must be positive")

    def __call__(self, method: str, params: Sequence[object]) -> object:
        request_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": list(params),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self.configured_rpc_url,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=float(self.timeout_seconds)) as response:
                raw = response.read(self.max_response_bytes + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise RpcReceiptError(
                "rpc_unavailable", "configured JSON-RPC endpoint is unavailable"
            ) from exc
        if len(raw) > self.max_response_bytes:
            _fail("malformed_response", "JSON-RPC response exceeds the size limit")
        try:
            envelope = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RpcReceiptError(
                "malformed_response", "JSON-RPC response is not valid JSON"
            ) from exc
        if (
            not isinstance(envelope, dict)
            or envelope.get("jsonrpc") != "2.0"
            or envelope.get("id") != 1
        ):
            _fail("malformed_response", "JSON-RPC response envelope is invalid")
        if envelope.get("error") is not None:
            _fail("rpc_error", "configured JSON-RPC endpoint returned an error")
        if "result" not in envelope:
            _fail("malformed_response", "JSON-RPC response has no result")
        return envelope["result"]
