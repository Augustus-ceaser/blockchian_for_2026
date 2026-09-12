from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import inspect

import pytest

from app.modules.web3.rpc import (
    ReceiptStatus,
    RpcReceiptError,
    UrllibJsonRpcTransport,
    verify_transaction_receipt,
)


CHAIN_ID = 31337
TX_HASH = "0x" + "a" * 64
BLOCK_HASH = "0x" + "b" * 64
OTHER_BLOCK_HASH = "0x" + "d" * 64
CONTRACT = "0x" + "1" * 40
OTHER_CONTRACT = "0x" + "2" * 40
EVENT_TOPIC = "0x" + "c" * 64
OTHER_TOPIC = "0x" + "e" * 64
LOG_INDEX = 7


class StubTransport:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __call__(self, method: str, params: object) -> object:
        normalized_params = tuple(params)  # type: ignore[arg-type]
        self.calls.append((method, normalized_params))
        return deepcopy(self.responses[method])


def _receipt() -> dict[str, object]:
    return {
        "transactionHash": TX_HASH,
        "status": "0x1",
        "to": CONTRACT,
        "blockNumber": "0x64",
        "blockHash": BLOCK_HASH,
        "logs": [
            {
                "address": CONTRACT,
                "topics": [EVENT_TOPIC, "0x" + "f" * 64],
                "data": "0x" + "deadbeef" * 100,
                "transactionHash": TX_HASH,
                "blockNumber": "0x64",
                "blockHash": BLOCK_HASH,
                "logIndex": hex(LOG_INDEX),
                "removed": False,
            }
        ],
    }


def _responses() -> dict[str, object]:
    return {
        "eth_chainId": hex(CHAIN_ID),
        "eth_getTransactionReceipt": _receipt(),
        "eth_blockNumber": "0x67",
        "eth_getBlockByNumber": {"number": "0x64", "hash": BLOCK_HASH},
    }


def _verify(
    responses: dict[str, object] | None = None,
    **overrides: object,
):
    transport = StubTransport(_responses() if responses is None else responses)
    values: dict[str, object] = {
        "transport": transport,
        "expected_chain_id": CHAIN_ID,
        "tx_hash": TX_HASH,
        "contract_address": CONTRACT,
        "event_topic": EVENT_TOPIC,
        "log_index": LOG_INDEX,
        "minimum_confirmations": 3,
    }
    values.update(overrides)
    result = verify_transaction_receipt(**values)  # type: ignore[arg-type]
    return result, transport


def _assert_rpc_error(code: str, callback: object) -> None:
    with pytest.raises(RpcReceiptError) as captured:
        callback()  # type: ignore[operator]
    assert captured.value.code == code


def test_confirms_exact_event_and_returns_raw_log_for_fixed_abi_decoder() -> None:
    result, transport = _verify()

    assert result.status is ReceiptStatus.CONFIRMED
    assert result.confirmed
    assert result.confirmations == 4
    assert result.block_number == 100
    assert result.block_hash == BLOCK_HASH
    assert result.head_block_number == 103
    assert result.event_topics == (EVENT_TOPIC, "0x" + "f" * 64)
    assert result.event_data == "0x" + "deadbeef" * 100
    assert transport.calls == [
        ("eth_chainId", ()),
        ("eth_getTransactionReceipt", (TX_HASH,)),
        ("eth_blockNumber", ()),
        ("eth_getBlockByNumber", ("0x64", False)),
    ]
    with pytest.raises(FrozenInstanceError):
        result.confirmations = 5  # type: ignore[misc]


@pytest.mark.parametrize("bad_data", ["deadbeef", "0x0", "0xgg", "0x" + "00" * 2049])
def test_rejects_malformed_or_oversized_event_data(bad_data: str) -> None:
    responses = _responses()
    receipt = responses["eth_getTransactionReceipt"]
    receipt["logs"][0]["data"] = bad_data  # type: ignore[index]
    _assert_rpc_error("malformed_response", lambda: _verify(responses))


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda responses: responses.__setitem__("eth_getTransactionReceipt", None), ReceiptStatus.PENDING),
        (lambda responses: responses.__setitem__("eth_blockNumber", "0x64"), ReceiptStatus.INSUFFICIENT_CONFIRMATIONS),
        (lambda responses: responses["eth_getTransactionReceipt"].__setitem__("status", "0x0"), ReceiptStatus.REVERTED),  # type: ignore[union-attr]
        (lambda responses: responses["eth_getBlockByNumber"].__setitem__("hash", OTHER_BLOCK_HASH), ReceiptStatus.ORPHANED),  # type: ignore[union-attr]
        (lambda responses: responses.__setitem__("eth_chainId", "0x1"), ReceiptStatus.WRONG_CHAIN),
        (lambda responses: responses["eth_getTransactionReceipt"].__setitem__("to", OTHER_CONTRACT), ReceiptStatus.WRONG_CONTRACT),  # type: ignore[union-attr]
        (lambda responses: responses["eth_getTransactionReceipt"]["logs"][0]["topics"].__setitem__(0, OTHER_TOPIC), ReceiptStatus.WRONG_EVENT),  # type: ignore[index,union-attr]
    ],
)
def test_distinguishes_non_confirmed_outcomes(mutate: object, expected: ReceiptStatus) -> None:
    responses = _responses()
    mutate(responses)  # type: ignore[operator]
    result, _ = _verify(responses)
    assert result.status is expected
    assert not result.confirmed


def test_requires_exact_log_index_and_expected_log_contract() -> None:
    responses = _responses()
    receipt = responses["eth_getTransactionReceipt"]
    receipt["logs"][0]["logIndex"] = "0x8"  # type: ignore[index]
    result, _ = _verify(responses)
    assert result.status is ReceiptStatus.WRONG_EVENT

    responses = _responses()
    receipt = responses["eth_getTransactionReceipt"]
    receipt["logs"][0]["address"] = OTHER_CONTRACT  # type: ignore[index]
    result, _ = _verify(responses)
    assert result.status is ReceiptStatus.WRONG_CONTRACT


def test_removed_or_noncanonical_log_is_orphaned() -> None:
    responses = _responses()
    receipt = responses["eth_getTransactionReceipt"]
    receipt["logs"][0]["removed"] = True  # type: ignore[index]
    result, _ = _verify(responses)
    assert result.status is ReceiptStatus.ORPHANED

    responses = _responses()
    responses["eth_getBlockByNumber"] = None
    result, _ = _verify(responses)
    assert result.status is ReceiptStatus.ORPHANED


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt.__setitem__("transactionHash", "0x1234"),
        lambda receipt: receipt.__setitem__("blockHash", "0x1234"),
        lambda receipt: receipt["logs"][0].__setitem__("transactionHash", "0x1234"),  # type: ignore[index]
        lambda receipt: receipt["logs"][0].__setitem__("blockHash", "0x1234"),  # type: ignore[index]
        lambda receipt: receipt["logs"][0]["topics"].__setitem__(0, "0x1234"),  # type: ignore[index]
    ],
)
def test_rejects_malformed_transaction_log_and_topic_hashes(mutation: object) -> None:
    responses = _responses()
    mutation(responses["eth_getTransactionReceipt"])  # type: ignore[operator]
    _assert_rpc_error("malformed_response", lambda: _verify(responses))


def test_rejects_receipt_for_another_transaction() -> None:
    responses = _responses()
    receipt = responses["eth_getTransactionReceipt"]
    receipt["transactionHash"] = "0x" + "9" * 64  # type: ignore[index]
    _assert_rpc_error("transaction_mismatch", lambda: _verify(responses))


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"expected_chain_id": 0}, "invalid_expectation"),
        ({"tx_hash": "0x1234"}, "invalid_expectation"),
        ({"contract_address": "0x1234"}, "invalid_expectation"),
        ({"event_topic": "0x1234"}, "invalid_expectation"),
        ({"log_index": -1}, "invalid_expectation"),
        ({"log_index": 2**256}, "invalid_expectation"),
        ({"minimum_confirmations": 0}, "invalid_expectation"),
    ],
)
def test_rejects_invalid_server_expectations(
    overrides: dict[str, object], code: str
) -> None:
    _assert_rpc_error(code, lambda: _verify(**overrides))


def test_rpc_url_is_not_part_of_receipt_verifier_api() -> None:
    parameters = inspect.signature(verify_transaction_receipt).parameters
    assert "rpc_url" not in parameters
    assert "configured_rpc_url" not in parameters

    _assert_rpc_error(
        "invalid_rpc_configuration",
        lambda: UrllibJsonRpcTransport("file:///etc/passwd"),
    )
    _assert_rpc_error(
        "invalid_rpc_configuration",
        lambda: UrllibJsonRpcTransport("http://[::1"),
    )
