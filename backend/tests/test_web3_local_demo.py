from __future__ import annotations

import pytest

from app.modules.web3.local_demo import (
    LocalDemoRelayerError,
    event_log_index,
    send_unlocked_local_transaction,
    submit_unlocked_local_transaction,
)


SENDER = "0x" + "11" * 20
CONTRACT = "0x" + "22" * 20
TOPIC = "0x" + "33" * 32
TX_HASH = "0x" + "44" * 32


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __call__(self, method: str, params: tuple[object, ...]) -> object:
        self.calls.append((method, params))
        if method == "eth_chainId":
            return "0x7a69"
        if method == "eth_accounts":
            return [SENDER]
        if method == "eth_sendTransaction":
            return TX_HASH
        if method == "eth_getTransactionReceipt":
            return {
                "logs": [
                    {"address": CONTRACT, "topics": [TOPIC], "logIndex": "0x2"}
                ]
            }
        raise AssertionError(method)


def test_local_relayer_submits_only_from_the_configured_unlocked_account() -> None:
    transport = FakeTransport()
    tx_hash, receipt = submit_unlocked_local_transaction(
        transport,
        expected_chain_id=31337,
        sender=SENDER.upper().replace("0X", "0x"),
        contract_address=CONTRACT,
        calldata="0x1234",
    )
    assert tx_hash == TX_HASH
    assert event_log_index(receipt, contract_address=CONTRACT, event_topic=TOPIC) == 2
    submitted = next(params for method, params in transport.calls if method == "eth_sendTransaction")
    assert submitted[0] == {
        "from": SENDER,
        "to": CONTRACT,
        "data": "0x1234",
        "value": "0x0",
    }


def test_local_relayer_can_return_hash_before_waiting_for_receipt() -> None:
    transport = FakeTransport()
    tx_hash = send_unlocked_local_transaction(
        transport,
        expected_chain_id=31337,
        sender=SENDER,
        contract_address=CONTRACT,
        calldata="0x1234",
    )
    assert tx_hash == TX_HASH
    assert all(method != "eth_getTransactionReceipt" for method, _ in transport.calls)


def test_local_relayer_refuses_any_non_demo_chain() -> None:
    with pytest.raises(LocalDemoRelayerError, match="restricted"):
        submit_unlocked_local_transaction(
            FakeTransport(),
            expected_chain_id=1,
            sender=SENDER,
            contract_address=CONTRACT,
            calldata="0x1234",
        )


def test_event_locator_rejects_ambiguous_logs() -> None:
    log = {"address": CONTRACT, "topics": [TOPIC], "logIndex": "0x1"}
    with pytest.raises(LocalDemoRelayerError, match="exactly one"):
        event_log_index(
            {"logs": [log, {**log, "logIndex": "0x2"}]},
            contract_address=CONTRACT,
            event_topic=TOPIC,
        )
