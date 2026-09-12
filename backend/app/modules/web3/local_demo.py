"""Strict helpers for the disposable Hardhat attestor demonstration.

These helpers intentionally rely on unlocked JSON-RPC accounts and therefore
must only be called after the API route has enforced the local chain boundary.
They are not a key-management or production relayer implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
import time
from typing import NoReturn

from .evm_codec import normalize_address
from .rpc import JsonRpcTransport, RpcReceiptError


_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_QUANTITY_RE = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z")


class LocalDemoRelayerError(ValueError):
    """The disposable local relayer cannot safely submit or locate a call."""


def _fail(message: str) -> NoReturn:
    raise LocalDemoRelayerError(message)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(f"{name} must be a JSON object")
    return value


def _rpc(transport: JsonRpcTransport, method: str, params: Sequence[object]) -> object:
    try:
        return transport(method, params)
    except RpcReceiptError:
        raise
    except Exception as exc:
        raise LocalDemoRelayerError("local JSON-RPC request failed") from exc


def submit_unlocked_local_transaction(
    transport: JsonRpcTransport,
    *,
    expected_chain_id: int,
    sender: str,
    contract_address: str,
    calldata: str,
    receipt_attempts: int = 40,
) -> tuple[str, Mapping[str, object]]:
    """Submit one zero-value call and wait for its disposable local receipt."""

    transaction_hash = send_unlocked_local_transaction(
        transport,
        expected_chain_id=expected_chain_id,
        sender=sender,
        contract_address=contract_address,
        calldata=calldata,
    )
    return transaction_hash, load_local_transaction_receipt(
        transport,
        expected_chain_id=expected_chain_id,
        transaction_hash=transaction_hash,
        receipt_attempts=receipt_attempts,
    )


def send_unlocked_local_transaction(
    transport: JsonRpcTransport,
    *,
    expected_chain_id: int,
    sender: str,
    contract_address: str,
    calldata: str,
) -> str:
    """Broadcast one local call without waiting, so its hash can be persisted first."""

    if expected_chain_id != 31337:
        _fail("unlocked-account relaying is restricted to the local Hardhat chain")
    normalized_sender = normalize_address(sender)
    normalized_contract = normalize_address(contract_address)
    if not isinstance(calldata, str) or re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", calldata) is None:
        _fail("calldata must be non-empty byte-aligned hexadecimal data")

    chain_id = _rpc(transport, "eth_chainId", ())
    if chain_id != "0x7a69":
        _fail("configured RPC is not the expected disposable Hardhat chain")
    accounts = _rpc(transport, "eth_accounts", ())
    if not isinstance(accounts, list) or any(not isinstance(item, str) for item in accounts):
        _fail("local RPC returned an invalid unlocked account list")
    normalized_accounts = {normalize_address(item) for item in accounts}
    if normalized_sender not in normalized_accounts:
        _fail("configured attestor is not unlocked by the local Hardhat node")

    transaction_hash = _rpc(
        transport,
        "eth_sendTransaction",
        (
            {
                "from": normalized_sender,
                "to": normalized_contract,
                "data": calldata.lower(),
                "value": "0x0",
            },
        ),
    )
    if not isinstance(transaction_hash, str) or _HASH_RE.fullmatch(transaction_hash) is None:
        _fail("local RPC returned an invalid transaction hash")
    return transaction_hash.lower()


def load_local_transaction_receipt(
    transport: JsonRpcTransport,
    *,
    expected_chain_id: int,
    transaction_hash: str,
    receipt_attempts: int = 40,
) -> Mapping[str, object]:
    """Load one already-submitted local receipt for interrupted-flow recovery."""

    if expected_chain_id != 31337:
        _fail("receipt recovery is restricted to the local Hardhat chain")
    if not isinstance(receipt_attempts, int) or isinstance(receipt_attempts, bool) or not 1 <= receipt_attempts <= 100:
        _fail("receipt_attempts is outside the local demo bound")
    if not isinstance(transaction_hash, str) or _HASH_RE.fullmatch(transaction_hash) is None:
        _fail("transaction hash must be a 32-byte hash")
    if _rpc(transport, "eth_chainId", ()) != "0x7a69":
        _fail("configured RPC is not the expected disposable Hardhat chain")
    normalized_hash = transaction_hash.lower()
    for _ in range(receipt_attempts):
        receipt = _rpc(transport, "eth_getTransactionReceipt", (normalized_hash,))
        if receipt is not None:
            return _mapping(receipt, "transaction receipt")
        time.sleep(0.1)
    _fail("local transaction was not mined within the demo timeout")


def event_log_index(
    receipt: Mapping[str, object],
    *,
    contract_address: str,
    event_topic: str,
) -> int:
    """Locate one exact contract/topic log; ambiguity is rejected."""

    contract = normalize_address(contract_address)
    if not isinstance(event_topic, str) or _HASH_RE.fullmatch(event_topic) is None:
        _fail("event topic must be a 32-byte hash")
    expected_topic = event_topic.lower()
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        _fail("transaction receipt has no valid logs array")
    matches: list[int] = []
    for position, value in enumerate(logs):
        log = _mapping(value, f"transaction log {position}")
        address = log.get("address")
        topics = log.get("topics")
        raw_index = log.get("logIndex")
        if not isinstance(address, str) or not isinstance(topics, list) or not topics:
            continue
        if normalize_address(address) != contract or topics[0] != expected_topic:
            continue
        if not isinstance(raw_index, str) or _QUANTITY_RE.fullmatch(raw_index) is None:
            _fail("matching event has an invalid log index")
        matches.append(int(raw_index[2:], 16))
    if len(matches) != 1:
        _fail("expected exactly one matching event in the local transaction")
    return matches[0]
