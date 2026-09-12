import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.modules.audit import canonical_json_digest_v1
from app.modules.web3.escrow import (
    Web3EscrowError,
    _chain_payload,
    _record_chain_event,
    build_escrow_plan,
)
from app.modules.web3.evm_codec import DecodedEvent
from app.modules.web3.rpc import ReceiptStatus, ReceiptVerification


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
SPACE_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ADDRESS = "0x" + "55" * 20
ORDER_ID = UUID("11111111-1111-4111-8111-111111111111")
CONTRACT_ID = UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = UUID("33333333-3333-4333-8333-333333333333")
ANCHOR_ID = UUID("44444444-4444-4444-8444-444444444444")
DATA_LINE_ID = UUID("55555555-5555-4555-8555-555555555555")
MODEL_LINE_ID = UUID("66666666-6666-4666-8666-666666666666")


def _plan(**overrides: object):
    values = {
        "order_id": ORDER_ID,
        "contract_id": CONTRACT_ID,
        "contract_revision_id": REVISION_ID,
        "contract_content_digest": "sha256:" + "66" * 32,
        "anchor_id": ANCHOR_ID,
        "chain_id": 31337,
        "agreement_key": "0x" + "11" * 32,
        "quote_digest": "sha256:" + "22" * 32,
        "agreement_digest": "sha256:" + "33" * 32,
        "data_line_id": DATA_LINE_ID,
        "model_line_id": MODEL_LINE_ID,
        "data_fee_minor": 50_000,
        "model_fee_minor": 30_000,
        "platform_fee_minor": 2_000,
        "gross_amount_minor": 82_000,
        "token_decimals": 6,
        "escrow_address": "0x" + "44" * 20,
        "refund_eligible_at": datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return build_escrow_plan(**values)


def test_escrow_plan_balances_cny_minor_units_and_calldata() -> None:
    plan = _plan()
    assert plan.total_token_units == 820_000_000
    assert plan.distribution_snapshot["data_fee_token_units"] == 500_000_000
    assert plan.distribution_snapshot["model_fee_token_units"] == 300_000_000
    assert plan.distribution_snapshot["platform_fee_token_units"] == 20_000_000
    assert plan.distribution_snapshot["contract_revision_id"] == str(REVISION_ID)
    assert plan.distribution_snapshot["contract_content_digest"] == (
        "sha256:" + "66" * 32
    )
    assert plan.distribution_snapshot["chain_id"] == 31337
    assert plan.distribution_snapshot["escrow_contract_address"] == "0x" + "44" * 20
    assert plan.approve_calldata.startswith("0x095ea7b3")
    assert plan.open_calldata.startswith("0x9cd3d8d6")
    assert plan.distribution_digest.startswith("sha256:")
    assert plan.task_digest.startswith("sha256:")


def test_escrow_plan_is_deterministic() -> None:
    assert _plan() == _plan()


def test_task_digest_is_bound_to_chain_and_escrow_contract() -> None:
    baseline = _plan()
    assert _plan(chain_id=31338).task_digest != baseline.task_digest
    assert _plan(escrow_address="0x" + "99" * 20).task_digest != baseline.task_digest


def test_escrow_plan_rejects_unbalanced_or_zero_amounts() -> None:
    with pytest.raises(Web3EscrowError, match="does not balance"):
        _plan(gross_amount_minor=81_999)
    with pytest.raises(Web3EscrowError, match="zero-value"):
        _plan(
            data_fee_minor=0,
            model_fee_minor=0,
            platform_fee_minor=0,
            gross_amount_minor=0,
        )


def test_escrow_plan_rejects_naive_deadline_and_lossy_decimals() -> None:
    with pytest.raises(Web3EscrowError, match="timezone-aware"):
        _plan(refund_eligible_at=NOW.replace(tzinfo=None))
    with pytest.raises(Web3EscrowError, match="between 2 and 18"):
        _plan(token_decimals=1)


def test_chain_event_replay_allows_confirmation_count_to_increase() -> None:
    receipt = ReceiptVerification(
        status=ReceiptStatus.CONFIRMED,
        chain_id=31337,
        tx_hash="0x" + "11" * 32,
        expected_contract_address="0x" + "22" * 20,
        expected_event_topic="0x" + "33" * 32,
        expected_log_index=4,
        minimum_confirmations=1,
        block_number=10,
        block_hash="0x" + "44" * 32,
        head_block_number=10,
        confirmations=1,
        event_topics=("0x" + "33" * 32,),
        event_data="0x",
    )
    decoded = DecodedEvent(name="EscrowFunded", values={"amount": 1})
    payload = _chain_payload(receipt, decoded)
    assert "confirmations" not in payload
    existing = SimpleNamespace(
        space_id=SPACE_ID,
        chain_id=receipt.chain_id,
        contract_address=receipt.expected_contract_address,
        transaction_hash=receipt.tx_hash,
        log_index=receipt.expected_log_index,
        block_number=receipt.block_number,
        block_hash=receipt.block_hash,
        event_name=decoded.name,
        subject_type="web3_escrow",
        subject_key=str(ORDER_ID),
        actor_wallet=ADDRESS,
        payload_digest=canonical_json_digest_v1(payload),
        status="applied",
        confirmations=1,
    )

    class ReplaySession:
        async def scalar(self, _statement):
            return existing

        async def flush(self, rows):
            assert rows == [existing]

    result = asyncio.run(
        _record_chain_event(
            ReplaySession(),
            space_id=SPACE_ID,
            subject_type="web3_escrow",
            subject_key=str(ORDER_ID),
            actor_wallet=ADDRESS,
            receipt=replace(receipt, head_block_number=15, confirmations=6),
            decoded=decoded,
            now=NOW,
        )
    )
    assert result is existing
    assert existing.confirmations == 6
