from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import get_phase4_context
from app.modules.commerce.models import CommercialOrder, CommercialOrderLine
from app.modules.commerce.services import CommerceError
from app.modules.compute.models import Artifact, ComputeRun
from app.modules.marketplace.models import ApprovedResultPackage
from app.api.routes.web3_common import (
    current_demo_actor,
    ensure_web3_enabled,
    require_siwe_binding,
    transaction_payload,
    web3_http_error,
)
from app.modules.web3.escrow import (
    Web3EscrowError,
    apply_attestation_receipt,
    apply_funded_receipt,
    apply_settled_receipt,
    frozen_contract_revision,
    prepare_escrow_binding,
    prepare_settlement_plan,
    receipt_expectation,
)
from app.modules.web3.models import (
    ChainEventReceipt,
    ContractChainAnchor,
    SettlementProofRecord,
    Web3EscrowBinding,
)
from app.modules.web3.evm_codec import EVENT_TOPICS
from app.modules.web3.local_demo import (
    LocalDemoRelayerError,
    event_log_index,
    load_local_transaction_receipt,
    send_unlocked_local_transaction,
)
from app.modules.web3.rpc import (
    ReceiptVerification,
    RpcReceiptError,
    UrllibJsonRpcTransport,
    verify_transaction_receipt,
)


router = APIRouter(prefix="/web3", tags=["web3-phase54-56"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReceiptRequest(StrictRequest):
    event_name: Literal[
        "EscrowFunded",
        "ExecutionAttested",
        "DeliveryAttested",
        "EscrowSettled",
    ]
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    log_index: int = Field(ge=0)


class SettlementRequest(StrictRequest):
    compute_run_id: UUID
    result_package_id: UUID


async def _automatic_settlement_target(
    session: AsyncSession,
    *,
    binding: Web3EscrowBinding,
) -> tuple[UUID, UUID]:
    """Resolve the only approved run/package pair; never accept it from the browser."""

    order = await session.get(CommercialOrder, binding.commercial_order_id)
    if (
        order is None
        or order.contract_id is None
        or order.space_id != binding.space_id
    ):
        raise Web3EscrowError("controlled-compute order has no bound contract")
    frozen_revision_id, frozen_content_digest = frozen_contract_revision(order)
    anchor = await session.get(ContractChainAnchor, binding.contract_chain_anchor_id)
    if (
        anchor is None
        or anchor.space_id != binding.space_id
        or anchor.contract_id != order.contract_id
        or anchor.contract_revision_id != frozen_revision_id
        or anchor.content_digest != frozen_content_digest
        or anchor.status != "active"
    ):
        raise Web3EscrowError("escrow is not bound to the frozen active contract revision")
    rows = (
        await session.execute(
            select(ComputeRun.id, ApprovedResultPackage.id)
            .join(Artifact, Artifact.compute_run_id == ComputeRun.id)
            .join(
                ApprovedResultPackage,
                ApprovedResultPackage.artifact_id == Artifact.id,
            )
            .where(
                ComputeRun.space_id == binding.space_id,
                ComputeRun.contract_id == order.contract_id,
                ComputeRun.contract_revision_id == frozen_revision_id,
                ComputeRun.status == "succeeded",
                ComputeRun.completion_receipt_digest.is_not(None),
                # The approved package is the releasable derivative.  The source
                # artifact must stay quarantined so settlement can never become
                # an implicit raw-output release path.
                Artifact.release_status == "quarantined",
                Artifact.space_id == binding.space_id,
                ApprovedResultPackage.status == "available",
                ApprovedResultPackage.space_id == binding.space_id,
                ApprovedResultPackage.requester_organization_id
                == order.requester_organization_id,
            )
        )
    ).all()
    unique = list(dict.fromkeys((run_id, package_id) for run_id, package_id in rows))
    if not unique:
        raise Web3EscrowError(
            "automatic settlement is waiting for a successful run and approved result package"
        )
    if len(unique) != 1:
        raise Web3EscrowError(
            "automatic settlement found multiple eligible result packages; operator review is required"
        )
    return unique[0]


def _escrow_payload(binding: Web3EscrowBinding) -> dict[str, object]:
    return {
        "escrow_binding_id": str(binding.id),
        "order_id": str(binding.commercial_order_id),
        "chain_id": binding.chain_id,
        "escrow_contract_address": binding.escrow_contract_address,
        "settlement_token_address": binding.settlement_token_address,
        "escrow_order_key": binding.escrow_order_key,
        "payer_wallet": binding.payer_wallet,
        "amount_token_units": str(int(binding.amount_token_units)),
        "token_decimals": binding.token_decimals,
        "status": binding.status,
        "refund_eligible_at": binding.refund_eligible_at.isoformat(),
        "funding_tx_hash": binding.funding_tx_hash,
        "settlement_tx_hash": binding.settlement_tx_hash,
        "distribution": binding.distribution_snapshot,
    }


async def _order_is_visible(
    session: AsyncSession,
    *,
    order: CommercialOrder,
    actor: object,
    space_id: UUID,
) -> bool:
    if order.space_id != space_id:
        return False
    role = getattr(actor, "role", None)
    organization_id = getattr(actor, "organization_id", None)
    if role == "space_operator":
        return True
    if role == "data_requester" and organization_id == order.requester_organization_id:
        return True
    if role not in {"data_provider", "model_provider"}:
        return False
    return (
        await session.scalar(
            select(CommercialOrderLine.id).where(
                CommercialOrderLine.order_id == order.id,
                CommercialOrderLine.provider_organization_id == organization_id,
            )
        )
    ) is not None


@router.post("/commercial-orders/{order_id}/escrow/prepare")
async def prepare_order_escrow(
    order_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            actor, wallet_binding = await require_siwe_binding(session, request)
            if actor.role != "data_requester":
                raise HTTPException(status_code=403, detail="仅需求方可建立订单托管")
            binding, plan = await prepare_escrow_binding(
                session,
                order_id=order_id,
                wallet_binding=wallet_binding,
                chain_id=settings.web3_chain_id,
                escrow_address=settings.web3_escrow_address,
                settlement_token_address=settings.web3_settlement_token_address,
                token_decimals=settings.web3_settlement_token_decimals,
                refund_after=timedelta(seconds=settings.web3_escrow_refund_seconds),
            )
        return {
            **_escrow_payload(binding),
            "transactions": [
                transaction_payload(
                    to=binding.settlement_token_address,
                    data=plan.approve_calldata,
                    label="授权本订单精确金额",
                ),
                transaction_payload(
                    to=binding.escrow_contract_address,
                    data=plan.open_calldata,
                    label="建立链上资金托管",
                ),
            ],
            "security_boundary": "本地模拟结算币；不代表法币支付或生产托管",
        }
    except HTTPException:
        raise
    except (Web3EscrowError, CommerceError, ValueError) as exc:
        raise web3_http_error(exc) from exc


@router.get("/escrows/{escrow_binding_id}")
async def get_escrow_status(
    escrow_binding_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            actor, _ = await current_demo_actor(session, request)
            space_id = (await get_phase4_context(session)).space_id
            binding = await session.get(Web3EscrowBinding, escrow_binding_id)
            if binding is None:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            order = await session.get(CommercialOrder, binding.commercial_order_id)
            if order is None:
                raise HTTPException(status_code=404, detail="商业订单不存在")
            if not await _order_is_visible(
                session, order=order, actor=actor, space_id=space_id
            ):
                raise HTTPException(status_code=403, detail="无权查看该托管记录")
            proofs = list(
                (
                    await session.scalars(
                        select(SettlementProofRecord).where(
                            SettlementProofRecord.escrow_binding_id == binding.id
                        )
                    )
                ).all()
            )
            events = list(
                (
                    await session.scalars(
                        select(ChainEventReceipt)
                        .where(
                            ChainEventReceipt.space_id == binding.space_id,
                            ChainEventReceipt.subject_key.in_(
                                [str(binding.id), *[str(item.id) for item in proofs]]
                            ),
                        )
                        .order_by(ChainEventReceipt.block_number, ChainEventReceipt.log_index)
                    )
                ).all()
            )
        return {
            **_escrow_payload(binding),
            "proofs": [
                {
                    "proof_id": str(item.id),
                    "proof_type": item.proof_type,
                    "status": item.status,
                    "proof_digest": item.proof_digest,
                    "transaction_hash": item.transaction_hash,
                }
                for item in proofs
            ],
            "events": [
                {
                    "event_name": item.event_name,
                    "transaction_hash": item.transaction_hash,
                    "block_number": item.block_number,
                    "confirmations": item.confirmations,
                    "status": item.status,
                }
                for item in events
            ],
        }
    except HTTPException:
        raise
    except (Web3EscrowError, ValueError) as exc:
        raise web3_http_error(exc) from exc


@router.get("/commercial-orders/{order_id}/escrow")
async def find_order_escrow(
    order_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Discover a chain escrow without relying on one browser's local storage."""

    ensure_web3_enabled(request)
    async with session.begin():
        actor, _ = await current_demo_actor(session, request)
        space_id = (await get_phase4_context(session)).space_id
        order = await session.get(CommercialOrder, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="商业订单不存在")
        if not await _order_is_visible(
            session, order=order, actor=actor, space_id=space_id
        ):
            raise HTTPException(status_code=403, detail="无权查看该订单托管记录")
        binding = await session.scalar(
            select(Web3EscrowBinding).where(
                Web3EscrowBinding.commercial_order_id == order.id
            )
        )
    if binding is None:
        return {"available": False, "order_id": str(order.id)}
    return {"available": True, **_escrow_payload(binding)}


@router.post("/escrows/{escrow_binding_id}/settlement/prepare")
async def prepare_escrow_settlement(
    escrow_binding_id: UUID,
    payload: SettlementRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            actor, wallet_binding = await require_siwe_binding(session, request)
            if actor.role != "space_operator":
                raise HTTPException(
                    status_code=403, detail="仅平台运营方可触发双证明结算编排"
                )
            plan = await prepare_settlement_plan(
                session,
                escrow_binding_id=escrow_binding_id,
                compute_run_id=payload.compute_run_id,
                result_package_id=payload.result_package_id,
                submitted_by=actor.user_id,
                expected_space_id=wallet_binding.space_id,
            )
            binding = await session.get(Web3EscrowBinding, escrow_binding_id)
        assert binding is not None
        return {
            **_escrow_payload(binding),
            "settlement_context_digest": plan.context_digest,
            "proofs": [
                {
                    "proof_id": str(plan.execution_record.id),
                    "proof_type": "execution",
                    "proof_digest": plan.execution_record.proof_digest,
                    "submission_mode": "independent_system_attestor",
                    "transaction": transaction_payload(
                        to=binding.escrow_contract_address,
                        data=plan.execution_calldata,
                        label="提交执行成功证明",
                    ),
                },
                {
                    "proof_id": str(plan.delivery_record.id),
                    "proof_type": "delivery",
                    "proof_digest": plan.delivery_record.proof_digest,
                    "submission_mode": "independent_system_attestor",
                    "transaction": transaction_payload(
                        to=binding.escrow_contract_address,
                        data=plan.delivery_calldata,
                        label="提交已审核交付证明",
                    ),
                },
            ],
            "automatic_effect": "第二项独立证明确认后，合约自动进入可提现结算状态",
        }
    except HTTPException:
        raise
    except (Web3EscrowError, ValueError) as exc:
        raise web3_http_error(exc) from exc


async def _verify_receipt(
    request: Request,
    *,
    binding: Web3EscrowBinding,
    payload: ReceiptRequest,
) -> ReceiptVerification:
    settings = request.app.state.settings
    expectation = receipt_expectation(
        binding=binding,
        event_name=payload.event_name,
        tx_hash=payload.transaction_hash,
        log_index=payload.log_index,
    )
    transport = UrllibJsonRpcTransport(
        configured_rpc_url=settings.web3_rpc_url,
        timeout_seconds=settings.web3_rpc_timeout_seconds,
    )
    return await asyncio.to_thread(
        verify_transaction_receipt,
        transport=transport,
        minimum_confirmations=settings.web3_required_confirmations,
        **expectation,
    )


async def _verified_local_event(
    request: Request,
    *,
    binding: Web3EscrowBinding,
    event_name: Literal[
        "ExecutionAttested", "DeliveryAttested", "EscrowSettled"
    ],
    transaction_hash: str,
    raw_receipt: object,
) -> ReceiptVerification:
    if not isinstance(raw_receipt, dict):
        raise LocalDemoRelayerError("local receipt is not a JSON object")
    log_index = event_log_index(
        raw_receipt,
        contract_address=binding.escrow_contract_address,
        event_topic=EVENT_TOPICS[event_name],
    )
    receipt = await _verify_receipt(
        request,
        binding=binding,
        payload=ReceiptRequest(
            event_name=event_name,
            transaction_hash=transaction_hash,
            log_index=log_index,
        ),
    )
    if not receipt.confirmed:
        raise Web3EscrowError(
            f"local {event_name} receipt is not final: {receipt.status.value}"
        )
    return receipt


async def _ensure_local_attestation_submitted(
    session: AsyncSession,
    request: Request,
    *,
    escrow_binding_id: UUID,
    proof_record_id: UUID,
    proof_type: Literal["execution", "delivery"],
    sender: str,
    calldata: str,
    transport: UrllibJsonRpcTransport,
) -> tuple[str, Web3EscrowBinding]:
    """Persist a local attestation hash before waiting for finality.

    The short RPC broadcast happens while the proof row is locked. This is a
    local-roadshow recovery bridge, not a production relayer/outbox design.
    """

    async with session.begin():
        actor, wallet_binding = await require_siwe_binding(session, request)
        if actor.role != "space_operator":
            raise HTTPException(
                status_code=403, detail="仅运营方可启动本地自动结算演示"
            )
        binding = await session.scalar(
            select(Web3EscrowBinding)
            .where(
                Web3EscrowBinding.id == escrow_binding_id,
                Web3EscrowBinding.space_id == wallet_binding.space_id,
            )
            .with_for_update()
        )
        if binding is None:
            raise HTTPException(status_code=404, detail="链上托管记录不存在")
        record = await session.scalar(
            select(SettlementProofRecord)
            .where(
                SettlementProofRecord.id == proof_record_id,
                SettlementProofRecord.escrow_binding_id == binding.id,
                SettlementProofRecord.space_id == wallet_binding.space_id,
                SettlementProofRecord.proof_type == proof_type,
            )
            .with_for_update()
        )
        if record is None:
            raise Web3EscrowError("prepared settlement proof was not found")
        if record.status in {"submitted", "finalized"}:
            if not record.transaction_hash:
                raise Web3EscrowError(
                    "submitted settlement proof has no recoverable transaction hash"
                )
            return record.transaction_hash, binding
        if record.status != "prepared":
            raise Web3EscrowError("settlement proof is not available for submission")

        transaction_hash = await asyncio.to_thread(
            send_unlocked_local_transaction,
            transport,
            expected_chain_id=binding.chain_id,
            sender=sender,
            contract_address=binding.escrow_contract_address,
            calldata=calldata,
        )
        record.status = "submitted"
        record.transaction_hash = transaction_hash
        record.submitted_at = datetime.now(timezone.utc)
        await session.flush([record])
        return transaction_hash, binding


@router.post("/escrows/{escrow_binding_id}/local-demo-auto-settle")
async def auto_settle_local_demo_escrow(
    escrow_binding_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Run the two independent Hardhat attestors for the local roadshow only."""

    ensure_web3_enabled(request)
    settings = request.app.state.settings
    if settings.deployment_mode != "local" or settings.web3_chain_id != 31337:
        raise HTTPException(
            status_code=403,
            detail="自动代签仅限一次性本地 Hardhat 演示链",
        )
    try:
        async with session.begin():
            actor, wallet_binding = await require_siwe_binding(session, request)
            if actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="仅运营方可启动本地自动结算演示")
            binding = await session.scalar(
                select(Web3EscrowBinding)
                .where(
                    Web3EscrowBinding.id == escrow_binding_id,
                    Web3EscrowBinding.space_id == wallet_binding.space_id,
                )
                .with_for_update()
            )
            if binding is None:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            if binding.status == "claimable":
                return {
                    **_escrow_payload(binding),
                    "automatic_settlement": "already_completed",
                    "idempotent_replay": True,
                }
            run_id, package_id = await _automatic_settlement_target(
                session, binding=binding
            )
            plan = await prepare_settlement_plan(
                session,
                escrow_binding_id=binding.id,
                compute_run_id=run_id,
                result_package_id=package_id,
                submitted_by=actor.user_id,
                expected_space_id=wallet_binding.space_id,
            )
            execution_record_id = plan.execution_record.id
            delivery_record_id = plan.delivery_record.id
            chain_id = binding.chain_id

        transport = UrllibJsonRpcTransport(
            configured_rpc_url=settings.web3_rpc_url,
            timeout_seconds=settings.web3_rpc_timeout_seconds,
        )
        execution_transaction_hash, binding = (
            await _ensure_local_attestation_submitted(
                session,
                request,
                escrow_binding_id=escrow_binding_id,
                proof_record_id=execution_record_id,
                proof_type="execution",
                sender=settings.web3_execution_attestor_address,
                calldata=plan.execution_calldata,
                transport=transport,
            )
        )
        raw_execution = await asyncio.to_thread(
            load_local_transaction_receipt,
            transport,
            expected_chain_id=chain_id,
            transaction_hash=execution_transaction_hash,
        )
        execution_receipt = await _verified_local_event(
            request,
            binding=binding,
            event_name="ExecutionAttested",
            transaction_hash=execution_transaction_hash,
            raw_receipt=raw_execution,
        )
        async with session.begin():
            current_actor, current_wallet_binding = await require_siwe_binding(
                session, request
            )
            if current_actor.role != "space_operator":
                raise HTTPException(
                    status_code=403, detail="仅运营方可启动本地自动结算演示"
                )
            locked_binding = await session.scalar(
                select(Web3EscrowBinding)
                .where(
                    Web3EscrowBinding.id == escrow_binding_id,
                    Web3EscrowBinding.space_id == current_wallet_binding.space_id,
                )
                .with_for_update()
            )
            if locked_binding is None:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            await apply_attestation_receipt(
                session,
                escrow_binding_id=escrow_binding_id,
                proof_type="execution",
                receipt=execution_receipt,
                expected_attestor_address=settings.web3_execution_attestor_address,
            )

        delivery_transaction_hash, binding = (
            await _ensure_local_attestation_submitted(
                session,
                request,
                escrow_binding_id=escrow_binding_id,
                proof_record_id=delivery_record_id,
                proof_type="delivery",
                sender=settings.web3_delivery_attestor_address,
                calldata=plan.delivery_calldata,
                transport=transport,
            )
        )
        raw_delivery = await asyncio.to_thread(
            load_local_transaction_receipt,
            transport,
            expected_chain_id=chain_id,
            transaction_hash=delivery_transaction_hash,
        )
        delivery_receipt = await _verified_local_event(
            request,
            binding=binding,
            event_name="DeliveryAttested",
            transaction_hash=delivery_transaction_hash,
            raw_receipt=raw_delivery,
        )
        async with session.begin():
            current_actor, current_wallet_binding = await require_siwe_binding(
                session, request
            )
            if current_actor.role != "space_operator":
                raise HTTPException(
                    status_code=403, detail="仅运营方可启动本地自动结算演示"
                )
            locked_binding = await session.scalar(
                select(Web3EscrowBinding)
                .where(
                    Web3EscrowBinding.id == escrow_binding_id,
                    Web3EscrowBinding.space_id == current_wallet_binding.space_id,
                )
                .with_for_update()
            )
            if locked_binding is None:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            await apply_attestation_receipt(
                session,
                escrow_binding_id=escrow_binding_id,
                proof_type="delivery",
                receipt=delivery_receipt,
                expected_attestor_address=settings.web3_delivery_attestor_address,
            )

        settled_receipt = await _verified_local_event(
            request,
            binding=binding,
            event_name="EscrowSettled",
            transaction_hash=delivery_transaction_hash,
            raw_receipt=raw_delivery,
        )
        async with session.begin():
            current_actor, current_wallet_binding = await require_siwe_binding(
                session, request
            )
            if current_actor.role != "space_operator":
                raise HTTPException(
                    status_code=403, detail="仅运营方可启动本地自动结算演示"
                )
            locked_binding = await session.scalar(
                select(Web3EscrowBinding)
                .where(
                    Web3EscrowBinding.id == escrow_binding_id,
                    Web3EscrowBinding.space_id == current_wallet_binding.space_id,
                )
                .with_for_update()
            )
            if locked_binding is None:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            updated, settled_event = await apply_settled_receipt(
                session,
                escrow_binding_id=escrow_binding_id,
                receipt=settled_receipt,
            )
        return {
            **_escrow_payload(updated),
            "automatic_settlement": "completed",
            "idempotent_replay": False,
            "compute_run_id": str(run_id),
            "result_package_id": str(package_id),
            "execution_transaction_hash": execution_transaction_hash,
            "delivery_transaction_hash": delivery_transaction_hash,
            "settlement_chain_event_receipt_id": str(settled_event.id),
            "security_boundary": (
                "仅本地 Hardhat 使用独立解锁测试账户；生产环境必须由独立证明服务签名"
            ),
        }
    except HTTPException:
        raise
    except (
        Web3EscrowError,
        LocalDemoRelayerError,
        RpcReceiptError,
        ValueError,
    ) as exc:
        raise web3_http_error(exc) from exc


@router.post("/escrows/{escrow_binding_id}/receipts")
async def synchronize_escrow_receipt(
    escrow_binding_id: UUID,
    payload: ReceiptRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            actor, wallet_binding = await require_siwe_binding(session, request)
            binding = await session.get(Web3EscrowBinding, escrow_binding_id)
            if binding is None:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            if binding.space_id != wallet_binding.space_id:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            if payload.event_name == "EscrowFunded":
                if (
                    actor.role != "data_requester"
                    or wallet_binding.wallet_address != binding.payer_wallet
                ):
                    raise HTTPException(status_code=403, detail="仅付款钱包可确认托管付款")
            elif actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="仅平台运营方可同步结算证明")
        receipt = await _verify_receipt(request, binding=binding, payload=payload)
        if not receipt.confirmed:
            return {
                **_escrow_payload(binding),
                "receipt_status": receipt.status.value,
                "confirmations": receipt.confirmations,
                "required_confirmations": receipt.minimum_confirmations,
                "applied": False,
            }
        async with session.begin():
            current_actor, current_wallet_binding = await require_siwe_binding(
                session, request
            )
            locked_binding = await session.scalar(
                select(Web3EscrowBinding)
                .where(
                    Web3EscrowBinding.id == escrow_binding_id,
                    Web3EscrowBinding.space_id == current_wallet_binding.space_id,
                )
                .with_for_update()
            )
            if locked_binding is None:
                raise HTTPException(status_code=404, detail="链上托管记录不存在")
            if payload.event_name == "EscrowFunded":
                if (
                    current_actor.role != "data_requester"
                    or current_wallet_binding.wallet_address
                    != locked_binding.payer_wallet
                ):
                    raise HTTPException(status_code=403, detail="仅付款钱包可确认托管付款")
            elif current_actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="仅平台运营方可同步结算证明")
            if payload.event_name == "EscrowFunded":
                updated, fulfillment, event = await apply_funded_receipt(
                    session, escrow_binding_id=escrow_binding_id, receipt=receipt
                )
                result = {
                    "fulfillment_id": str(fulfillment.id),
                    "chain_event_receipt_id": str(event.id),
                }
            elif payload.event_name in {"ExecutionAttested", "DeliveryAttested"}:
                proof_type = (
                    "execution"
                    if payload.event_name == "ExecutionAttested"
                    else "delivery"
                )
                proof, event = await apply_attestation_receipt(
                    session,
                    escrow_binding_id=escrow_binding_id,
                    proof_type=proof_type,
                    receipt=receipt,
                    expected_attestor_address=(
                        request.app.state.settings.web3_execution_attestor_address
                        if proof_type == "execution"
                        else request.app.state.settings.web3_delivery_attestor_address
                    ),
                )
                updated = await session.get(Web3EscrowBinding, escrow_binding_id)
                assert updated is not None
                result = {
                    "proof_id": str(proof.id),
                    "chain_event_receipt_id": str(event.id),
                }
            else:
                updated, event = await apply_settled_receipt(
                    session, escrow_binding_id=escrow_binding_id, receipt=receipt
                )
                result = {"chain_event_receipt_id": str(event.id)}
        return {
            **_escrow_payload(updated),
            **result,
            "receipt_status": receipt.status.value,
            "confirmations": receipt.confirmations,
            "applied": True,
        }
    except HTTPException:
        raise
    except (Web3EscrowError, RpcReceiptError, ValueError) as exc:
        raise web3_http_error(exc) from exc
