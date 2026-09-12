from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import canonical_json_digest_v1
from app.modules.commerce.models import (
    CommercialFulfillment,
    CommercialOrder,
    CommercialOrderLine,
    DemoPayment,
)
from app.modules.commerce.services import (
    CommerceError,
    create_commercial_fulfillment_after_verified_payment,
)
from app.modules.compute.models import Artifact, ComputeRun
from app.modules.marketplace.models import ApprovedResultPackage

from .evm_codec import (
    EVENT_TOPICS,
    DecodedEvent,
    decode_medtrust_event,
    digest_to_bytes32,
    encode_contract_call,
    normalize_address,
    stable_bytes32,
)
from .models import (
    ChainEventReceipt,
    ContractChainAnchor,
    SettlementProofRecord,
    WalletIdentityBinding,
    Web3EscrowBinding,
)
from .rpc import ReceiptStatus, ReceiptVerification
from .settlement import build_settlement_proof


_DIGEST_PREFIX = "sha256:"
_UINT128_MAX = (1 << 128) - 1


class Web3EscrowError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EscrowPlan:
    order_key: str
    task_digest: str
    distribution_snapshot: dict[str, Any]
    distribution_digest: str
    approve_calldata: str
    open_calldata: str
    total_token_units: int


@dataclass(frozen=True, slots=True)
class SettlementPlan:
    context_digest: str
    execution_record: SettlementProofRecord
    delivery_record: SettlementProofRecord
    execution_calldata: str
    delivery_calldata: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise Web3EscrowError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(value: object, name: str) -> str:
    if not (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith(_DIGEST_PREFIX)
        and all(character in "0123456789abcdef" for character in value[7:])
    ):
        raise Web3EscrowError(f"{name} is not a canonical SHA-256 digest")
    return value


def _token_units(amount_minor: int, token_decimals: int) -> int:
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor < 0:
        raise Web3EscrowError("minor currency amount must be a non-negative integer")
    if isinstance(token_decimals, bool) or not 2 <= token_decimals <= 18:
        raise Web3EscrowError("settlement token decimals must be between 2 and 18")
    value = amount_minor * 10 ** (token_decimals - 2)
    if value > _UINT128_MAX:
        raise Web3EscrowError("settlement amount exceeds the contract uint128 limit")
    return value


def frozen_contract_revision(order: CommercialOrder) -> tuple[UUID, str]:
    snapshot = order.agreement_snapshot
    if not isinstance(snapshot, dict):
        raise Web3EscrowError("commercial agreement snapshot is invalid")
    try:
        revision_id = UUID(str(snapshot["active_revision_id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise Web3EscrowError(
            "commercial agreement does not freeze a valid contract revision"
        ) from exc
    content_digest = _digest(
        snapshot.get("contract_content_digest"), "contract_content_digest"
    )
    if snapshot.get("contract_id") != str(order.contract_id):
        raise Web3EscrowError("commercial agreement contract identity is inconsistent")
    return revision_id, content_digest


def build_escrow_plan(
    *,
    order_id: UUID,
    contract_id: UUID,
    contract_revision_id: UUID,
    contract_content_digest: str,
    anchor_id: UUID,
    chain_id: int,
    agreement_key: str,
    quote_digest: str,
    agreement_digest: str,
    data_line_id: UUID,
    model_line_id: UUID,
    data_fee_minor: int,
    model_fee_minor: int,
    platform_fee_minor: int,
    gross_amount_minor: int,
    token_decimals: int,
    escrow_address: str,
    refund_eligible_at: datetime,
) -> EscrowPlan:
    """Build the exact ERC-20 approval and escrow calls from frozen order facts."""

    if chain_id <= 0:
        raise Web3EscrowError("chain_id must be positive")
    normalized_escrow_address = normalize_address(escrow_address)
    agreement_key = agreement_key.lower()
    if len(agreement_key) != 66 or not agreement_key.startswith("0x"):
        raise Web3EscrowError("agreement key must contain exactly 32 bytes")
    quote_digest = _digest(quote_digest, "quote_digest")
    agreement_digest = _digest(agreement_digest, "agreement_digest")
    contract_content_digest = _digest(
        contract_content_digest, "contract_content_digest"
    )
    refund_at = _aware(refund_eligible_at, "refund_eligible_at")
    data_units = _token_units(data_fee_minor, token_decimals)
    model_units = _token_units(model_fee_minor, token_decimals)
    platform_units = _token_units(platform_fee_minor, token_decimals)
    total_units = data_units + model_units + platform_units
    if data_fee_minor + model_fee_minor + platform_fee_minor != gross_amount_minor:
        raise Web3EscrowError("escrow distribution does not balance to the order total")
    if total_units <= 0:
        raise Web3EscrowError("zero-value escrow is not supported")
    order_key = stable_bytes32("commercial-order", order_id)
    task_document = {
        "schema_version": "medtrust.web3.controlled-compute-task/v1",
        "order_id": str(order_id),
        "contract_id": str(contract_id),
        "contract_revision_id": str(contract_revision_id),
        "contract_content_digest": contract_content_digest,
        "contract_chain_anchor_id": str(anchor_id),
        "chain_id": chain_id,
        "escrow_contract_address": normalized_escrow_address,
        "agreement_key": agreement_key,
        "quote_digest": quote_digest,
        "agreement_digest": agreement_digest,
    }
    task_digest = canonical_json_digest_v1(task_document)
    distribution = {
        "schema_version": "medtrust.web3.escrow-distribution/v1",
        "order_id": str(order_id),
        "contract_id": str(contract_id),
        "contract_revision_id": str(contract_revision_id),
        "contract_content_digest": contract_content_digest,
        "chain_id": chain_id,
        "escrow_contract_address": normalized_escrow_address,
        "agreement_key": agreement_key,
        "task_digest": task_digest,
        "currency": "CNY",
        "token_decimals": token_decimals,
        "data_line_id": str(data_line_id),
        "model_line_id": str(model_line_id),
        "data_fee_minor": data_fee_minor,
        "model_fee_minor": model_fee_minor,
        "platform_fee_minor": platform_fee_minor,
        "gross_amount_minor": gross_amount_minor,
        "data_fee_token_units": data_units,
        "model_fee_token_units": model_units,
        "platform_fee_token_units": platform_units,
        "total_token_units": total_units,
        "refund_eligible_at": refund_at.isoformat(),
    }
    return EscrowPlan(
        order_key=order_key,
        task_digest=task_digest,
        distribution_snapshot=distribution,
        distribution_digest=canonical_json_digest_v1(distribution),
        approve_calldata=encode_contract_call(
            "approve", [normalized_escrow_address, total_units]
        ),
        open_calldata=encode_contract_call(
            "openEscrow",
            [
                order_key,
                agreement_key,
                digest_to_bytes32(task_digest),
                data_units,
                model_units,
                platform_units,
                int(refund_at.timestamp()),
            ],
        ),
        total_token_units=total_units,
    )


async def prepare_escrow_binding(
    session: AsyncSession,
    *,
    order_id: UUID,
    wallet_binding: WalletIdentityBinding,
    chain_id: int,
    escrow_address: str,
    settlement_token_address: str,
    token_decimals: int = 6,
    refund_after: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> tuple[Web3EscrowBinding, EscrowPlan]:
    current = _aware(now or _now(), "now")
    order = await session.scalar(
        select(CommercialOrder).where(CommercialOrder.id == order_id).with_for_update()
    )
    if order is None:
        raise Web3EscrowError("commercial order was not found")
    if order.source_type != "contract" or order.contract_id is None:
        raise Web3EscrowError("Web3 escrow is limited to controlled-compute orders")
    if order.space_id != wallet_binding.space_id:
        raise Web3EscrowError("commercial order belongs to another space")
    if order.status != "awaiting_payment":
        raise Web3EscrowError("commercial agreement must be accepted before escrow funding")
    if (
        wallet_binding.status != "active"
        or wallet_binding.role_code != "data_requester"
        or wallet_binding.organization_id != order.requester_organization_id
        or wallet_binding.user_id != order.requester_user_id
        or wallet_binding.chain_id != chain_id
        or wallet_binding.credential_expires_at is None
        or _aware(wallet_binding.credential_expires_at, "credential_expires_at") <= current
    ):
        raise Web3EscrowError("active requester wallet credential is required")
    frozen_revision_id, frozen_content_digest = frozen_contract_revision(order)
    anchor = await session.scalar(
        select(ContractChainAnchor).where(
            ContractChainAnchor.space_id == order.space_id,
            ContractChainAnchor.contract_id == order.contract_id,
            ContractChainAnchor.contract_revision_id == frozen_revision_id,
            ContractChainAnchor.chain_id == chain_id,
        )
    )
    if (
        anchor is None
        or anchor.status != "active"
        or anchor.content_digest != frozen_content_digest
    ):
        raise Web3EscrowError("the digital contract has no active finalized chain anchor")
    lines = list(
        (
            await session.scalars(
                select(CommercialOrderLine)
                .where(CommercialOrderLine.order_id == order.id)
                .order_by(CommercialOrderLine.line_no)
            )
        ).all()
    )
    by_kind = {line.product_kind: line for line in lines}
    if len(lines) != 2 or set(by_kind) != {"data", "model"}:
        raise Web3EscrowError("escrow requires exactly one data and one model line")
    refund_at = current + refund_after
    if refund_after <= timedelta(minutes=5):
        raise Web3EscrowError("escrow refund window must exceed five minutes")
    plan = build_escrow_plan(
        order_id=order.id,
        contract_id=order.contract_id,
        contract_revision_id=frozen_revision_id,
        contract_content_digest=frozen_content_digest,
        anchor_id=anchor.id,
        chain_id=chain_id,
        agreement_key=anchor.agreement_key,
        quote_digest=order.quote_digest,
        agreement_digest=order.agreement_digest,
        data_line_id=by_kind["data"].id,
        model_line_id=by_kind["model"].id,
        data_fee_minor=by_kind["data"].provider_net_minor,
        model_fee_minor=by_kind["model"].provider_net_minor,
        platform_fee_minor=order.platform_fee_minor,
        gross_amount_minor=order.gross_amount_minor,
        token_decimals=token_decimals,
        escrow_address=escrow_address,
        refund_eligible_at=refund_at,
    )
    binding_id = uuid5(NAMESPACE_URL, f"medtrust:web3-escrow:{order.id}:{chain_id}")
    existing = await session.get(Web3EscrowBinding, binding_id)
    if existing is not None:
        if (
            existing.distribution_digest != plan.distribution_digest
            or existing.contract_chain_anchor_id != anchor.id
            or existing.payer_wallet != wallet_binding.wallet_address
            or existing.escrow_contract_address != normalize_address(escrow_address)
            or existing.settlement_token_address
            != normalize_address(settlement_token_address)
        ):
            raise Web3EscrowError("prepared escrow conflicts with frozen order facts")
        return existing, plan
    binding = Web3EscrowBinding(
        id=binding_id,
        space_id=order.space_id,
        commercial_order_id=order.id,
        contract_chain_anchor_id=anchor.id,
        chain_id=chain_id,
        escrow_contract_address=normalize_address(escrow_address),
        settlement_token_address=normalize_address(settlement_token_address),
        escrow_order_key=plan.order_key,
        payer_wallet=wallet_binding.wallet_address,
        amount_token_units=Decimal(plan.total_token_units),
        token_decimals=token_decimals,
        distribution_snapshot=plan.distribution_snapshot,
        distribution_digest=plan.distribution_digest,
        status="prepared",
        refund_eligible_at=refund_at,
        created_by=wallet_binding.user_id,
        created_at=current,
        updated_at=current,
    )
    session.add(binding)
    await session.flush([binding])
    return binding, plan


def _require_confirmed(receipt: ReceiptVerification) -> None:
    if receipt.status is not ReceiptStatus.CONFIRMED or not receipt.confirmed:
        raise Web3EscrowError(f"chain receipt is not final: {receipt.status.value}")
    if (
        receipt.block_number is None
        or receipt.block_hash is None
        or receipt.event_data is None
        or not receipt.event_topics
    ):
        raise Web3EscrowError("confirmed chain receipt is incomplete")


def _chain_payload(
    receipt: ReceiptVerification, decoded: DecodedEvent
) -> dict[str, Any]:
    return {
        "schema_version": "medtrust.web3.chain-event/v1",
        "event_name": decoded.name,
        "chain_id": receipt.chain_id,
        "transaction_hash": receipt.tx_hash,
        "log_index": receipt.expected_log_index,
        "block_number": receipt.block_number,
        "block_hash": receipt.block_hash,
        "values": decoded.values,
    }


async def _record_chain_event(
    session: AsyncSession,
    *,
    space_id: UUID,
    subject_type: str,
    subject_key: str,
    actor_wallet: str | None,
    receipt: ReceiptVerification,
    decoded: DecodedEvent,
    now: datetime,
) -> ChainEventReceipt:
    existing = await session.scalar(
        select(ChainEventReceipt).where(
            ChainEventReceipt.chain_id == receipt.chain_id,
            ChainEventReceipt.transaction_hash == receipt.tx_hash,
            ChainEventReceipt.log_index == receipt.expected_log_index,
        )
    )
    payload = _chain_payload(receipt, decoded)
    payload_digest = canonical_json_digest_v1(payload)
    normalized_actor = normalize_address(actor_wallet) if actor_wallet else None
    if existing is not None:
        if (
            existing.space_id != space_id
            or existing.chain_id != receipt.chain_id
            or existing.contract_address != receipt.expected_contract_address
            or existing.transaction_hash != receipt.tx_hash
            or existing.log_index != receipt.expected_log_index
            or existing.block_number != receipt.block_number
            or existing.block_hash != receipt.block_hash
            or existing.event_name != decoded.name
            or existing.subject_type != subject_type
            or existing.subject_key != subject_key
            or existing.actor_wallet != normalized_actor
            or existing.payload_digest != payload_digest
            or existing.status not in {"finalized", "applied"}
        ):
            raise Web3EscrowError("chain event position conflicts with existing evidence")
        if existing.confirmations < receipt.confirmations:
            existing.confirmations = receipt.confirmations
            await session.flush([existing])
        return existing
    row = ChainEventReceipt(
        space_id=space_id,
        chain_id=receipt.chain_id,
        contract_address=receipt.expected_contract_address,
        transaction_hash=receipt.tx_hash,
        log_index=receipt.expected_log_index,
        block_number=receipt.block_number,
        block_hash=receipt.block_hash,
        event_name=decoded.name,
        subject_type=subject_type,
        subject_key=subject_key,
        actor_wallet=normalized_actor,
        payload_snapshot=payload,
        payload_digest=payload_digest,
        confirmations=receipt.confirmations,
        status="applied",
        first_seen_at=now,
        finalized_at=now,
        applied_at=now,
    )
    session.add(row)
    await session.flush([row])
    return row


async def apply_funded_receipt(
    session: AsyncSession,
    *,
    escrow_binding_id: UUID,
    receipt: ReceiptVerification,
    now: datetime | None = None,
) -> tuple[Web3EscrowBinding, CommercialFulfillment, ChainEventReceipt]:
    current = _aware(now or _now(), "now")
    _require_confirmed(receipt)
    binding = await session.scalar(
        select(Web3EscrowBinding)
        .where(Web3EscrowBinding.id == escrow_binding_id)
        .with_for_update()
    )
    if binding is None:
        raise Web3EscrowError("escrow binding was not found")
    if (
        receipt.chain_id != binding.chain_id
        or receipt.expected_contract_address != binding.escrow_contract_address
        or receipt.expected_event_topic != EVENT_TOPICS["EscrowFunded"]
    ):
        raise Web3EscrowError("funding receipt does not match the prepared escrow")
    decoded = decode_medtrust_event(
        "EscrowFunded", receipt.event_topics, receipt.event_data or ""
    )
    values = decoded.values
    snapshot = binding.distribution_snapshot
    expected = {
        "escrow_order_key": binding.escrow_order_key,
        "agreement_key": snapshot["agreement_key"],
        "task_digest": digest_to_bytes32(snapshot["task_digest"]),
        "payer": binding.payer_wallet,
        "total_amount": int(binding.amount_token_units),
        "refund_after": int(binding.refund_eligible_at.timestamp()),
    }
    if values != expected:
        raise Web3EscrowError("EscrowFunded event conflicts with frozen escrow facts")
    chain_event = await _record_chain_event(
        session,
        space_id=binding.space_id,
        subject_type="web3_escrow",
        subject_key=str(binding.id),
        actor_wallet=binding.payer_wallet,
        receipt=receipt,
        decoded=decoded,
        now=current,
    )
    order = await session.scalar(
        select(CommercialOrder)
        .where(CommercialOrder.id == binding.commercial_order_id)
        .with_for_update()
    )
    if order is None:
        raise Web3EscrowError("escrow commercial order was not found")
    demo_payment = await session.scalar(
        select(DemoPayment).where(DemoPayment.order_id == order.id)
    )
    if demo_payment is not None:
        raise Web3EscrowError("order already has incompatible local-demo payment evidence")
    if order.status == "awaiting_payment":
        order.status = "paid"
        order.updated_at = current
        order.row_version += 1
        order._transition_validated = True
    elif order.status != "paid":
        raise Web3EscrowError("order cannot accept a finalized escrow payment")
    lines = list(
        (
            await session.scalars(
                select(CommercialOrderLine)
                .where(CommercialOrderLine.order_id == order.id)
                .order_by(CommercialOrderLine.line_no)
            )
        ).all()
    )
    fulfillment = await create_commercial_fulfillment_after_verified_payment(
        session,
        order=order,
        lines=lines,
        payment_receipt_digest=chain_event.payload_digest,
        now=current,
    )
    if binding.status in {"prepared", "funding"}:
        binding.status = "funded"
        binding.funding_tx_hash = receipt.tx_hash
        binding.funded_at = current
        binding.updated_at = current
        binding.row_version += 1
    elif binding.status not in {"funded", "proving", "claimable"}:
        raise Web3EscrowError("escrow binding is not in a fundable state")
    await session.flush([binding, order, fulfillment])
    return binding, fulfillment, chain_event


async def prepare_settlement_plan(
    session: AsyncSession,
    *,
    escrow_binding_id: UUID,
    compute_run_id: UUID,
    result_package_id: UUID,
    submitted_by: UUID,
    expected_space_id: UUID | None = None,
    now: datetime | None = None,
) -> SettlementPlan:
    current = _aware(now or _now(), "now")
    binding = await session.scalar(
        select(Web3EscrowBinding)
        .where(Web3EscrowBinding.id == escrow_binding_id)
        .with_for_update()
    )
    if binding is None or binding.status not in {"funded", "proving"}:
        raise Web3EscrowError("a funded escrow is required")
    if expected_space_id is not None and binding.space_id != expected_space_id:
        raise Web3EscrowError("escrow belongs to another space")
    if binding.refund_eligible_at <= current + timedelta(minutes=2):
        raise Web3EscrowError("escrow is too close to its refund deadline")
    order = await session.get(CommercialOrder, binding.commercial_order_id)
    if order is None or order.status != "paid" or order.contract_id is None:
        raise Web3EscrowError("paid controlled-compute order is unavailable")
    if order.space_id != binding.space_id:
        raise Web3EscrowError("escrow commercial order belongs to another space")
    frozen_revision_id, frozen_content_digest = frozen_contract_revision(order)
    anchor = await session.get(ContractChainAnchor, binding.contract_chain_anchor_id)
    if (
        anchor is None
        or anchor.space_id != binding.space_id
        or anchor.contract_id != order.contract_id
        or anchor.contract_revision_id != frozen_revision_id
        or anchor.content_digest != frozen_content_digest
        or anchor.chain_id != binding.chain_id
        or anchor.agreement_key != binding.distribution_snapshot.get("agreement_key")
        or anchor.status != "active"
    ):
        raise Web3EscrowError("escrow is not bound to the frozen active contract revision")
    fulfillment = await session.scalar(
        select(CommercialFulfillment).where(
            CommercialFulfillment.order_id == order.id,
            CommercialFulfillment.kind == "execution_entitlement",
            CommercialFulfillment.status == "ready",
        )
    )
    if fulfillment is None:
        raise Web3EscrowError("ready execution entitlement is unavailable")
    run = await session.get(ComputeRun, compute_run_id)
    if (
        run is None
        or run.status != "succeeded"
        or run.space_id != binding.space_id
        or run.contract_id != order.contract_id
        or run.contract_revision_id != frozen_revision_id
        or run.completion_receipt_digest is None
    ):
        raise Web3EscrowError("successful compute execution evidence is unavailable")
    completion_digest = _digest(
        run.completion_receipt_digest, "completion_receipt_digest"
    )
    package = await session.get(ApprovedResultPackage, result_package_id)
    if (
        package is None
        or package.status != "available"
        or package.space_id != binding.space_id
        or package.requester_organization_id != order.requester_organization_id
    ):
        raise Web3EscrowError("approved result package is unavailable to the requester")
    artifact = await session.get(Artifact, package.artifact_id)
    if (
        artifact is None
        or artifact.space_id != binding.space_id
        or artifact.compute_run_id != run.id
        or artifact.release_status != "quarantined"
    ):
        raise Web3EscrowError("result package does not belong to the selected compute run")

    context = build_settlement_proof(
        order_id=order.id,
        contract_id=order.contract_id,
        contract_revision_id=frozen_revision_id,
        run_id=run.id,
        artifact_id=artifact.id,
        package_id=package.id,
        quote_digest=order.quote_digest,
        contract_content_digest=frozen_content_digest,
        entitlement_digest=fulfillment.entitlement_digest,
        package_digest=package.package_digest,
        review_evidence_digest=package.review_evidence_digest,
        deadline=binding.refund_eligible_at,
        # Stable per escrow and independent of mutable database row versions,
        # so retrying preparation cannot silently create a different proof.
        nonce=int(binding.escrow_order_key[2:], 16),
        now=current,
    )
    execution_snapshot = {
        "schema_version": "medtrust.web3.execution-attestation/v1",
        "escrow_binding_id": str(binding.id),
        "escrow_order_key": binding.escrow_order_key,
        "chain_id": binding.chain_id,
        "escrow_contract_address": binding.escrow_contract_address,
        "settlement_context_digest": context.digest,
        "compute_run_id": str(run.id),
        "contract_revision_id": str(run.contract_revision_id),
        "completion_receipt_digest": completion_digest,
    }
    delivery_snapshot = {
        "schema_version": "medtrust.web3.delivery-attestation/v1",
        "escrow_binding_id": str(binding.id),
        "escrow_order_key": binding.escrow_order_key,
        "chain_id": binding.chain_id,
        "escrow_contract_address": binding.escrow_contract_address,
        "settlement_context_digest": context.digest,
        "artifact_id": str(artifact.id),
        "artifact_content_digest": _digest(artifact.content_digest, "artifact digest"),
        "result_package_id": str(package.id),
        "package_digest": _digest(package.package_digest, "package digest"),
        "review_evidence_digest": _digest(
            package.review_evidence_digest, "review evidence digest"
        ),
        "authority_evaluation_digest": _digest(
            package.authority_evaluation_digest, "authority evaluation digest"
        ),
    }
    execution_digest = canonical_json_digest_v1(execution_snapshot)
    delivery_digest = canonical_json_digest_v1(delivery_snapshot)
    records: dict[str, SettlementProofRecord] = {}
    for proof_type, proof_digest, source, kwargs in (
        (
            "execution",
            execution_digest,
            execution_snapshot,
            {"compute_run_id": run.id},
        ),
        (
            "delivery",
            delivery_digest,
            delivery_snapshot,
            {"artifact_id": artifact.id, "result_package_id": package.id},
        ),
    ):
        record_id = uuid5(
            NAMESPACE_URL, f"medtrust:web3-proof:{binding.id}:{proof_type}"
        )
        existing = await session.get(SettlementProofRecord, record_id)
        if existing is not None:
            if existing.proof_digest != proof_digest or existing.source_snapshot != source:
                raise Web3EscrowError("settlement proof conflicts with prior frozen evidence")
            records[proof_type] = existing
            continue
        record = SettlementProofRecord(
            id=record_id,
            space_id=binding.space_id,
            escrow_binding_id=binding.id,
            proof_type=proof_type,
            proof_digest=proof_digest,
            source_snapshot=source,
            status="prepared",
            submitted_by=submitted_by,
            created_at=current,
            **kwargs,
        )
        session.add(record)
        records[proof_type] = record
    if binding.status == "funded":
        binding.status = "proving"
        binding.updated_at = current
        binding.row_version += 1
    await session.flush([binding, *records.values()])
    return SettlementPlan(
        context_digest=context.digest,
        execution_record=records["execution"],
        delivery_record=records["delivery"],
        execution_calldata=encode_contract_call(
            "attestExecution",
            [binding.escrow_order_key, digest_to_bytes32(execution_digest)],
        ),
        delivery_calldata=encode_contract_call(
            "attestDelivery",
            [binding.escrow_order_key, digest_to_bytes32(delivery_digest)],
        ),
    )


async def apply_attestation_receipt(
    session: AsyncSession,
    *,
    escrow_binding_id: UUID,
    proof_type: str,
    receipt: ReceiptVerification,
    expected_attestor_address: str,
    now: datetime | None = None,
) -> tuple[SettlementProofRecord, ChainEventReceipt]:
    if proof_type not in {"execution", "delivery"}:
        raise Web3EscrowError("proof_type must be execution or delivery")
    current = _aware(now or _now(), "now")
    _require_confirmed(receipt)
    binding = await session.get(Web3EscrowBinding, escrow_binding_id)
    if binding is None or binding.status not in {"proving", "claimable"}:
        raise Web3EscrowError("escrow is not accepting settlement proofs")
    event_name = "ExecutionAttested" if proof_type == "execution" else "DeliveryAttested"
    if (
        receipt.chain_id != binding.chain_id
        or receipt.expected_contract_address != binding.escrow_contract_address
        or receipt.expected_event_topic != EVENT_TOPICS[event_name]
    ):
        raise Web3EscrowError("attestation receipt does not match the escrow")
    record = await session.scalar(
        select(SettlementProofRecord)
        .where(
            SettlementProofRecord.escrow_binding_id == binding.id,
            SettlementProofRecord.proof_type == proof_type,
        )
        .with_for_update()
    )
    if record is None:
        raise Web3EscrowError("prepared settlement proof was not found")
    decoded = decode_medtrust_event(
        event_name, receipt.event_topics, receipt.event_data or ""
    )
    expected_attestor = normalize_address(expected_attestor_address)
    if decoded.values != {
        "escrow_order_key": binding.escrow_order_key,
        "proof_digest": digest_to_bytes32(record.proof_digest),
        "attestor": expected_attestor,
    }:
        raise Web3EscrowError("attestation event conflicts with prepared evidence")
    chain_event = await _record_chain_event(
        session,
        space_id=binding.space_id,
        subject_type="settlement_proof",
        subject_key=str(record.id),
        actor_wallet=expected_attestor,
        receipt=receipt,
        decoded=decoded,
        now=current,
    )
    if record.status in {"prepared", "submitted"}:
        if (
            record.status == "submitted"
            and record.transaction_hash != receipt.tx_hash
        ):
            raise Web3EscrowError(
                "attestation receipt conflicts with the persisted transaction"
            )
        record.status = "finalized"
        record.transaction_hash = receipt.tx_hash
        record.submitted_at = record.submitted_at or current
        record.finalized_at = current
    elif record.status == "finalized":
        if record.transaction_hash != receipt.tx_hash:
            raise Web3EscrowError(
                "finalized attestation conflicts with the persisted transaction"
            )
    else:
        raise Web3EscrowError("settlement proof is not in an applicable state")
    await session.flush([record])
    return record, chain_event


async def apply_settled_receipt(
    session: AsyncSession,
    *,
    escrow_binding_id: UUID,
    receipt: ReceiptVerification,
    now: datetime | None = None,
) -> tuple[Web3EscrowBinding, ChainEventReceipt]:
    current = _aware(now or _now(), "now")
    _require_confirmed(receipt)
    binding = await session.scalar(
        select(Web3EscrowBinding)
        .where(Web3EscrowBinding.id == escrow_binding_id)
        .with_for_update()
    )
    if binding is None or binding.status not in {"proving", "claimable"}:
        raise Web3EscrowError("escrow is not ready for settlement")
    if (
        receipt.chain_id != binding.chain_id
        or receipt.expected_contract_address != binding.escrow_contract_address
        or receipt.expected_event_topic != EVENT_TOPICS["EscrowSettled"]
    ):
        raise Web3EscrowError("settlement receipt does not match the escrow")
    proofs = list(
        (
            await session.scalars(
                select(SettlementProofRecord).where(
                    SettlementProofRecord.escrow_binding_id == binding.id
                )
            )
        ).all()
    )
    by_type = {proof.proof_type: proof for proof in proofs}
    if set(by_type) != {"execution", "delivery"} or any(
        proof.status != "finalized" for proof in proofs
    ):
        raise Web3EscrowError("both independent settlement proofs must be finalized")
    decoded = decode_medtrust_event(
        "EscrowSettled", receipt.event_topics, receipt.event_data or ""
    )
    snapshot = binding.distribution_snapshot
    expected = {
        "escrow_order_key": binding.escrow_order_key,
        "execution_digest": digest_to_bytes32(by_type["execution"].proof_digest),
        "delivery_digest": digest_to_bytes32(by_type["delivery"].proof_digest),
        "data_fee": snapshot["data_fee_token_units"],
        "model_fee": snapshot["model_fee_token_units"],
        "platform_fee": snapshot["platform_fee_token_units"],
    }
    if decoded.values != expected:
        raise Web3EscrowError("EscrowSettled event conflicts with frozen distribution")
    chain_event = await _record_chain_event(
        session,
        space_id=binding.space_id,
        subject_type="web3_escrow",
        subject_key=str(binding.id),
        actor_wallet=None,
        receipt=receipt,
        decoded=decoded,
        now=current,
    )
    if binding.status == "proving":
        binding.status = "claimable"
        binding.settlement_tx_hash = receipt.tx_hash
        binding.settled_at = current
        binding.updated_at = current
        binding.row_version += 1
    await session.flush([binding])
    return binding, chain_event


def receipt_expectation(
    *, binding: Web3EscrowBinding, event_name: str, tx_hash: str, log_index: int
) -> dict[str, object]:
    if event_name not in {
        "EscrowFunded",
        "ExecutionAttested",
        "DeliveryAttested",
        "EscrowSettled",
        "EscrowRefunded",
    }:
        raise Web3EscrowError("unsupported escrow event")
    return {
        "expected_chain_id": binding.chain_id,
        "tx_hash": tx_hash,
        "contract_address": binding.escrow_contract_address,
        "event_topic": EVENT_TOPICS[event_name],
        "log_index": log_index,
    }
