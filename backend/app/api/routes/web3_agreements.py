from __future__ import annotations

import asyncio
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.web3_common import (
    current_demo_actor,
    ensure_web3_enabled,
    require_siwe_binding,
    transaction_payload,
    web3_http_error,
)
from app.db.session import get_db_session
from app.demo.phase4 import get_phase4_context
from app.modules.audit import AuditCommandContext, digest_idempotency_key
from app.modules.web3.agreements import (
    AgreementOrchestrationError,
    apply_agreement_receipt,
    prepare_agreement_activation,
    prepare_agreement_anchor,
    prepare_agreement_confirmation,
)
from app.modules.web3.evm_codec import EVENT_TOPICS, decode_medtrust_event
from app.modules.web3.models import ContractChainAnchor
from app.modules.web3.rpc import (
    ReceiptVerification,
    RpcReceiptError,
    UrllibJsonRpcTransport,
    verify_transaction_receipt,
)


router = APIRouter(prefix="/web3", tags=["web3-phase54-agreements"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AgreementReceiptRequest(StrictRequest):
    event_name: Literal[
        "AgreementRegistered",
        "AgreementConfirmed",
        "AgreementActivated",
    ]
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    log_index: int = Field(ge=0)


def _anchor_payload(anchor: ContractChainAnchor) -> dict[str, object]:
    completed = int(anchor.confirmation_bitmap).bit_count()
    return {
        "anchored": True,
        "chain_anchor_id": str(anchor.id),
        "contract_id": str(anchor.contract_id),
        "contract_revision_id": str(anchor.contract_revision_id),
        "chain_id": anchor.chain_id,
        "registry_address": anchor.registry_address,
        "agreement_key": anchor.agreement_key,
        "content_digest": anchor.content_digest,
        "status": anchor.status,
        "confirmation_bitmap": anchor.confirmation_bitmap,
        "required_confirmation_bitmap": anchor.required_confirmation_bitmap,
        "confirmation_progress": {"completed": completed, "required": 4},
        "registration_tx_hash": anchor.registration_tx_hash,
        "activation_tx_hash": anchor.activation_tx_hash,
    }


def _call_payload(call, *, fallback_from: str | None = None) -> dict[str, object]:
    return {
        "method": call.method,
        "expected_event": call.expected_event,
        "needs_submission": call.needs_submission,
        "transaction": transaction_payload(
            to=call.to,
            data=call.data,
            label={
                "registerAgreement": "登记当前合约摘要与四方钱包",
                "confirm": "确认当前不可变合约摘要",
                "activate": "触发已满足条件的合约生效",
            }[call.method],
            from_wallet=call.from_wallet or fallback_from,
        ),
    }


@router.get("/contract-revisions/{contract_revision_id}/agreement")
async def agreement_for_revision(
    contract_revision_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    async with session.begin():
        await current_demo_actor(session, request)
        space_id = (await get_phase4_context(session)).space_id
        anchor = await session.scalar(
            select(ContractChainAnchor).where(
                ContractChainAnchor.contract_revision_id == contract_revision_id,
                ContractChainAnchor.space_id == space_id,
            )
        )
    if anchor is None:
        return {
            "anchored": False,
            "contract_revision_id": str(contract_revision_id),
            "status": "not_prepared",
        }
    return _anchor_payload(anchor)


@router.post("/contract-revisions/{contract_revision_id}/agreement/prepare")
async def prepare_revision_agreement(
    contract_revision_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            actor, binding = await require_siwe_binding(session, request)
            if actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="仅空间运营方可登记链上合约")
            preparation = await prepare_agreement_anchor(
                session,
                contract_revision_id=contract_revision_id,
                chain_id=settings.web3_chain_id,
                registry_address=settings.web3_agreement_registry_address,
                credential_contract_address=settings.web3_role_credential_address,
                prepared_by_user_id=actor.user_id,
                expected_space_id=binding.space_id,
            )
            anchor = await session.get(ContractChainAnchor, preparation.anchor_id)
            assert anchor is not None
        return {
            **_anchor_payload(anchor),
            "participant_wallets": preparation.participant_wallets,
            "call": _call_payload(preparation.call, fallback_from=binding.wallet_address),
            "security_boundary": "链上只登记合约摘要、四方地址和有效期，不写入合同正文或医疗数据",
        }
    except HTTPException:
        raise
    except (AgreementOrchestrationError, ValueError) as exc:
        raise web3_http_error(exc) from exc


@router.post("/agreement-anchors/{chain_anchor_id}/confirmation/prepare")
async def prepare_party_confirmation(
    chain_anchor_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            actor, binding = await require_siwe_binding(session, request)
            preparation = await prepare_agreement_confirmation(
                session,
                chain_anchor_id=chain_anchor_id,
                current_user_id=actor.user_id,
                credential_contract_address=settings.web3_role_credential_address,
                expected_space_id=binding.space_id,
            )
            anchor = await session.get(ContractChainAnchor, chain_anchor_id)
            assert anchor is not None
        return {
            **_anchor_payload(anchor),
            "party_role": preparation.party_role,
            "expected_confirmation_bitmap": preparation.expected_confirmation_bitmap,
            "call": _call_payload(preparation.call, fallback_from=binding.wallet_address),
        }
    except HTTPException:
        raise
    except (AgreementOrchestrationError, ValueError) as exc:
        raise web3_http_error(exc) from exc


@router.post("/agreement-anchors/{chain_anchor_id}/activation/prepare")
async def prepare_delayed_activation(
    chain_anchor_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            actor, binding = await require_siwe_binding(session, request)
            if actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="仅空间运营方可同步延期生效")
            call = await prepare_agreement_activation(
                session,
                chain_anchor_id=chain_anchor_id,
                expected_space_id=binding.space_id,
            )
            anchor = await session.get(ContractChainAnchor, chain_anchor_id)
            assert anchor is not None
        return {
            **_anchor_payload(anchor),
            "call": _call_payload(call, fallback_from=binding.wallet_address),
        }
    except HTTPException:
        raise
    except (AgreementOrchestrationError, ValueError) as exc:
        raise web3_http_error(exc) from exc


async def _verify_receipt(
    request: Request,
    *,
    anchor: ContractChainAnchor,
    payload: AgreementReceiptRequest,
) -> ReceiptVerification:
    settings = request.app.state.settings
    transport = UrllibJsonRpcTransport(
        configured_rpc_url=settings.web3_rpc_url,
        timeout_seconds=settings.web3_rpc_timeout_seconds,
    )
    return await asyncio.to_thread(
        verify_transaction_receipt,
        transport=transport,
        expected_chain_id=anchor.chain_id,
        tx_hash=payload.transaction_hash,
        contract_address=anchor.registry_address,
        event_topic=EVENT_TOPICS[payload.event_name],
        log_index=payload.log_index,
        minimum_confirmations=settings.web3_required_confirmations,
    )


def _activation_command(
    *, anchor: ContractChainAnchor, receipt: ReceiptVerification
) -> AuditCommandContext:
    stable = (
        f"{anchor.chain_id}:{receipt.tx_hash}:{receipt.expected_log_index}:"
        f"{anchor.contract_revision_id}"
    )
    return AuditCommandContext(
        command_id=uuid5(NAMESPACE_URL, f"medtrust:web3:agreement-activation:{stable}"),
        idempotency_key=digest_idempotency_key(
            f"web3:agreement-activation:{stable}"
        ),
        correlation_id=uuid5(
            NAMESPACE_URL,
            f"medtrust:web3:agreement:{anchor.contract_revision_id}",
        ),
        actor_type="system",
        actor_service_code="medtrust.web3.agreement-mirror",
    )


@router.post("/agreement-anchors/{chain_anchor_id}/receipts")
async def synchronize_agreement_receipt(
    chain_anchor_id: UUID,
    payload: AgreementReceiptRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            actor, binding = await require_siwe_binding(session, request)
            anchor = await session.get(ContractChainAnchor, chain_anchor_id)
            if anchor is None:
                raise HTTPException(status_code=404, detail="链上合约锚点不存在")
            if anchor.space_id != binding.space_id:
                raise HTTPException(status_code=404, detail="链上合约锚点不存在")
            if payload.event_name != "AgreementConfirmed" and actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="仅空间运营方可同步该合约事件")

        receipt = await _verify_receipt(request, anchor=anchor, payload=payload)
        if receipt.confirmed and payload.event_name == "AgreementConfirmed":
            decoded = decode_medtrust_event(
                payload.event_name, receipt.event_topics, receipt.event_data or ""
            )
            if decoded.values["party_wallet"] != binding.wallet_address:
                raise HTTPException(status_code=403, detail="该确认事件不属于当前钱包")
        if not receipt.confirmed:
            return {
                **_anchor_payload(anchor),
                "receipt_status": receipt.status.value,
                "confirmations": receipt.confirmations,
                "required_confirmations": receipt.minimum_confirmations,
                "applied": False,
            }

        async with session.begin():
            current_actor, current_binding = await require_siwe_binding(
                session, request
            )
            locked_anchor = await session.scalar(
                select(ContractChainAnchor)
                .where(
                    ContractChainAnchor.id == chain_anchor_id,
                    ContractChainAnchor.space_id == current_binding.space_id,
                )
                .with_for_update()
            )
            if locked_anchor is None:
                raise HTTPException(status_code=404, detail="链上合约锚点不存在")
            if payload.event_name != "AgreementConfirmed":
                if current_actor.role != "space_operator":
                    raise HTTPException(
                        status_code=403, detail="仅空间运营方可同步该合约事件"
                    )
            else:
                decoded = decode_medtrust_event(
                    payload.event_name,
                    receipt.event_topics,
                    receipt.event_data or "",
                )
                if decoded.values["party_wallet"] != current_binding.wallet_address:
                    raise HTTPException(status_code=403, detail="该确认事件不属于当前钱包")
            result = await apply_agreement_receipt(
                session,
                chain_anchor_id=chain_anchor_id,
                event_name=payload.event_name,
                receipt=receipt,
                credential_contract_address=settings.web3_role_credential_address,
                minimum_confirmations=settings.web3_required_confirmations,
                expected_space_id=current_binding.space_id,
                activation_audit_command=(
                    _activation_command(anchor=locked_anchor, receipt=receipt)
                    if payload.event_name == "AgreementActivated"
                    else None
                ),
            )
            updated = await session.get(ContractChainAnchor, chain_anchor_id)
            assert updated is not None
        return {
            **_anchor_payload(updated),
            "chain_event_receipt_id": str(result.chain_event_receipt_id),
            "contract_signature_id": (
                str(result.contract_signature_id)
                if result.contract_signature_id is not None
                else None
            ),
            "receipt_status": receipt.status.value,
            "confirmations": receipt.confirmations,
            "applied": True,
            "idempotent_replay": result.idempotent_replay,
        }
    except HTTPException:
        raise
    except (AgreementOrchestrationError, RpcReceiptError, ValueError) as exc:
        raise web3_http_error(exc) from exc
