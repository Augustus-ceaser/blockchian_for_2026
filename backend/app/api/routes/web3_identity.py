from __future__ import annotations

import asyncio
import hmac
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.web3_common import (
    current_demo_actor,
    ensure_web3_enabled,
    ensure_web3_space_scope,
    require_siwe_binding,
    transaction_payload,
    web3_http_error,
)
from app.db.session import get_db_session
from app.modules.audit import canonical_json_digest_v1
from app.modules.identity.local_auth import resolve_session_user, set_session_cookie
from app.modules.identity.models import User
from app.modules.web3.credentials import (
    ROLE_HASHES,
    CredentialIssuanceError,
    PreparedCredentialIssueCall,
    PreparedCredentialRevokeCall,
    apply_credential_issued_receipt,
    prepare_credential_issue,
    prepare_credential_revocation,
    reconstruct_credential_revocation,
)
from app.modules.web3.evm_codec import (
    EVENT_TOPICS,
    decode_medtrust_event,
    normalize_address,
)
from app.modules.web3.identity_service import (
    WalletIdentityError,
    WalletIdentityPolicy,
    WalletSessionFactory,
    approve_wallet_binding,
    create_bind_challenge,
    create_login_challenge,
    issue_local_siwe_session,
    load_verified_credential_receipt,
    revoke_wallet_binding,
    validate_credential_issuance_eligibility,
    verify_bind_challenge,
    verify_login_challenge,
)
from app.modules.web3.local_demo import (
    LocalDemoRelayerError,
    event_log_index,
    load_local_transaction_receipt,
    send_unlocked_local_transaction,
)
from app.modules.web3.models import (
    ChainEventReceipt,
    WalletAuthChallenge,
    WalletIdentityBinding,
)
from app.modules.web3.rpc import (
    ReceiptVerification,
    RpcReceiptError,
    UrllibJsonRpcTransport,
    verify_transaction_receipt,
)

router = APIRouter(prefix="/auth/wallet", tags=["web3-identity"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WalletChallengeRequest(StrictRequest):
    chain_id: int = Field(gt=0)
    wallet_address: str = Field(min_length=42, max_length=42)


class BindChallengeRequest(WalletChallengeRequest):
    space_id: UUID


class WalletVerifyRequest(StrictRequest):
    challenge_id: UUID
    message: str = Field(min_length=1, max_length=4096)
    signature: str = Field(min_length=132, max_length=132)


class ApproveBindingRequest(StrictRequest):
    chain_event_receipt_id: UUID


class CredentialRevocationRequest(StrictRequest):
    reason: str = Field(min_length=3, max_length=500)


class CredentialReceiptRequest(StrictRequest):
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    log_index: int = Field(ge=0)


class CredentialRevocationReceiptRequest(CredentialRevocationRequest):
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    log_index: int = Field(ge=0)


class ChallengeResponse(BaseModel):
    challenge_id: UUID
    purpose: str
    message: str
    expires_at: datetime


class LoginResponse(BaseModel):
    authenticated: bool = True
    auth_method: str = "siwe"


class WalletCapabilitiesResponse(BaseModel):
    enabled: bool
    chain_id: int
    chain_name: str
    local_demo: bool
    notice: str


class BindingResponse(BaseModel):
    binding_id: UUID
    status: str
    credential_expires_at: datetime | None = None


class BindingStatusResponse(BindingResponse):
    chain_id: int
    wallet_address: str
    platform_role: str
    credential_token_id: str | None = None


_CREDENTIAL_LIFETIME = timedelta(days=90)


def _policy(request: Request) -> WalletIdentityPolicy:
    value = getattr(request.app.state, "web3_identity_policy", None)
    if not isinstance(value, WalletIdentityPolicy):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="钱包身份策略尚未配置",
        )
    return value


def _session_factory(request: Request) -> WalletSessionFactory:
    value = getattr(
        request.app.state,
        "web3_wallet_session_factory",
        issue_local_siwe_session,
    )
    if not callable(value):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="钱包会话服务不可用",
        )
    return value


def _raise_service_error(exc: WalletIdentityError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def _chain_name(chain_id: int) -> str:
    return {
        1: "Ethereum Mainnet",
        11155111: "Sepolia",
        31337: "Local Hardhat",
    }.get(chain_id, f"EVM Chain {chain_id}")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _binding_payload(binding: WalletIdentityBinding) -> BindingStatusResponse:
    return BindingStatusResponse(
        binding_id=binding.id,
        status=binding.status,
        chain_id=binding.chain_id,
        wallet_address=binding.wallet_address,
        platform_role=binding.role_code,
        credential_token_id=binding.credential_token_id,
        credential_expires_at=binding.credential_expires_at,
    )


async def _credential_expiry(
    session: AsyncSession, binding: WalletIdentityBinding
) -> datetime:
    """Derive one stable expiry from the consumed ownership proof.

    The browser cannot choose the credential lifetime.  Recomputing from the
    immutable proof timestamp also makes receipt synchronization stateless and
    deterministic across retries.
    """

    consumed_at = await session.scalar(
        select(WalletAuthChallenge.consumed_at)
        .where(
            WalletAuthChallenge.wallet_binding_id == binding.id,
            WalletAuthChallenge.purpose == "bind",
            WalletAuthChallenge.consumed_at.is_not(None),
        )
        .order_by(WalletAuthChallenge.consumed_at.desc())
        .limit(1)
    )
    if consumed_at is None:
        raise CredentialIssuanceError("wallet ownership proof is missing")
    return (_as_utc(consumed_at) + _CREDENTIAL_LIFETIME).replace(microsecond=0)


async def _pending_issue_call(
    session: AsyncSession,
    *,
    binding: WalletIdentityBinding,
    credential_contract_address: str,
) -> PreparedCredentialIssueCall:
    now = datetime.now(timezone.utc)
    # A pending row alone is not enough: issuing an irreversible on-chain
    # credential before checking the current user, organization, membership,
    # role and completed wallet proof would leave an invalid SBT on chain even
    # if the later database activation correctly failed.
    await validate_credential_issuance_eligibility(session, binding, now=now)
    return prepare_credential_issue(
        binding,
        credential_contract_address=credential_contract_address,
        expires_at=await _credential_expiry(session, binding),
        now=now,
    )


async def _operator_context(
    session: AsyncSession, request: Request
) -> tuple[object, WalletIdentityBinding, User]:
    actor, operator_binding = await require_siwe_binding(session, request)
    if actor.role != "space_operator":
        raise HTTPException(status_code=403, detail="仅空间运营方可签发平台资格凭证")
    operator_user = await session.get(User, actor.user_id)
    if operator_user is None:
        raise HTTPException(status_code=403, detail="空间运营账号不存在")
    return actor, operator_binding, operator_user


def _assert_same_space(
    operator_binding: WalletIdentityBinding, target: WalletIdentityBinding
) -> None:
    if target.space_id != operator_binding.space_id:
        raise HTTPException(status_code=403, detail="不得签发其他协作空间的资格凭证")


def _assert_issuer_wallet(request: Request, operator_binding: WalletIdentityBinding) -> None:
    expected = request.app.state.settings.web3_credential_issuer_address
    if operator_binding.wallet_address != expected:
        raise HTTPException(status_code=403, detail="当前运营钱包不是配置的资格凭证签发方")


async def _find_applied_credential_replay(
    session: AsyncSession,
    *,
    binding: WalletIdentityBinding,
    transaction_hash: str,
    log_index: int,
    credential_contract_address: str,
    issuer_address: str,
) -> ChainEventReceipt | None:
    """Return only an already-applied, internally consistent receipt replay."""

    receipt = await session.scalar(
        select(ChainEventReceipt).where(
            ChainEventReceipt.chain_id == binding.chain_id,
            ChainEventReceipt.transaction_hash == transaction_hash.lower(),
            ChainEventReceipt.log_index == log_index,
        )
    )
    if receipt is None:
        return None
    contract_address = normalize_address(credential_contract_address)
    snapshot = receipt.payload_snapshot
    expected_expiry = (
        _as_utc(binding.credential_expires_at).isoformat().replace("+00:00", "Z")
        if binding.credential_expires_at is not None
        else None
    )
    if (
        receipt.status != "applied"
        or receipt.applied_at is None
        or receipt.space_id != binding.space_id
        or receipt.contract_address != contract_address
        or receipt.event_name != "CredentialIssued"
        or receipt.subject_type != "wallet_identity_binding"
        or receipt.subject_key != str(binding.id)
        or receipt.actor_wallet != issuer_address
        or binding.status not in {"active", "revoked"}
        or binding.credential_contract_address != contract_address
        or not isinstance(snapshot, dict)
        or not hmac.compare_digest(
            receipt.payload_digest, canonical_json_digest_v1(snapshot)
        )
        or snapshot.get("binding_id") != str(binding.id)
        or snapshot.get("token_id") != binding.credential_token_id
        or snapshot.get("holder_address") != binding.wallet_address
        or snapshot.get("issuer_address") != issuer_address
        or snapshot.get("role_code") != binding.role_code
        or snapshot.get("credential_scope_digest")
        != binding.credential_scope_digest
        or snapshot.get("credential_expires_at") != expected_expiry
    ):
        raise CredentialIssuanceError(
            "stored credential receipt conflicts with the wallet binding"
        )
    return receipt


async def _verify_credential_receipt(
    request: Request,
    *,
    prepared_call: PreparedCredentialIssueCall,
    payload: CredentialReceiptRequest,
) -> ReceiptVerification:
    settings = request.app.state.settings
    transport = UrllibJsonRpcTransport(
        configured_rpc_url=settings.web3_rpc_url,
        timeout_seconds=settings.web3_rpc_timeout_seconds,
    )
    return await asyncio.to_thread(
        verify_transaction_receipt,
        transport=transport,
        expected_chain_id=prepared_call.chain_id,
        tx_hash=payload.transaction_hash,
        contract_address=prepared_call.to,
        event_topic=EVENT_TOPICS["CredentialIssued"],
        log_index=payload.log_index,
        minimum_confirmations=settings.web3_required_confirmations,
    )


async def _verify_credential_revocation_receipt(
    request: Request,
    *,
    prepared_call: PreparedCredentialRevokeCall,
    payload: CredentialRevocationReceiptRequest,
) -> ReceiptVerification:
    settings = request.app.state.settings
    transport = UrllibJsonRpcTransport(
        configured_rpc_url=settings.web3_rpc_url,
        timeout_seconds=settings.web3_rpc_timeout_seconds,
    )
    return await asyncio.to_thread(
        verify_transaction_receipt,
        transport=transport,
        expected_chain_id=prepared_call.chain_id,
        tx_hash=payload.transaction_hash,
        contract_address=prepared_call.to,
        event_topic=EVENT_TOPICS["CredentialRevoked"],
        log_index=payload.log_index,
        minimum_confirmations=settings.web3_required_confirmations,
    )


def _revocation_snapshot(
    binding: WalletIdentityBinding,
    prepared_call: PreparedCredentialRevokeCall,
    *,
    issuer_address: str,
) -> dict[str, str]:
    return {
        "schema_version": "medtrust.role-credential-revoked/v1",
        "binding_id": str(binding.id),
        "token_id": prepared_call.token_id,
        "holder_address": binding.wallet_address,
        "issuer_address": issuer_address,
        "role_code": binding.role_code,
        "reason_digest": prepared_call.reason_digest,
    }


def _validate_revocation_event(
    receipt: ReceiptVerification,
    *,
    binding: WalletIdentityBinding,
    prepared_call: PreparedCredentialRevokeCall,
    issuer_address: str,
) -> None:
    decoded = decode_medtrust_event(
        "CredentialRevoked",
        receipt.event_topics,
        receipt.event_data or "",
    )
    if (
        decoded.values["token_id"] != int(prepared_call.token_id)
        or decoded.values["holder"] != binding.wallet_address
        or decoded.values["issuer"] != issuer_address
        or decoded.values["role"] != ROLE_HASHES[binding.role_code]
        or decoded.values["reason_digest"] != prepared_call.arguments[1]
    ):
        raise CredentialIssuanceError(
            "CredentialRevoked event does not match the reviewed wallet binding"
        )


def _assert_revocation_replay(
    receipt: ChainEventReceipt | None,
    *,
    binding: WalletIdentityBinding,
    prepared_call: PreparedCredentialRevokeCall,
    issuer_address: str,
) -> ChainEventReceipt:
    snapshot = _revocation_snapshot(
        binding,
        prepared_call,
        issuer_address=issuer_address,
    )
    if (
        receipt is None
        or receipt.status != "applied"
        or receipt.applied_at is None
        or receipt.space_id != binding.space_id
        or receipt.chain_id != binding.chain_id
        or receipt.contract_address != prepared_call.to
        or receipt.event_name != "CredentialRevoked"
        or receipt.subject_type != "wallet_identity_binding"
        or receipt.subject_key != str(binding.id)
        or receipt.actor_wallet != issuer_address
        or receipt.payload_snapshot != snapshot
        or not hmac.compare_digest(
            receipt.payload_digest, canonical_json_digest_v1(snapshot)
        )
        or binding.status != "revoked"
        or binding.revocation_reason != prepared_call.reason
    ):
        raise CredentialIssuanceError(
            "stored credential revocation conflicts with the wallet binding"
        )
    return receipt


@router.get("/capabilities", response_model=WalletCapabilitiesResponse)
async def wallet_capabilities(request: Request) -> WalletCapabilitiesResponse:
    settings = request.app.state.settings
    configured = bool(settings.web3_enabled)
    policy = getattr(request.app.state, "web3_identity_policy", None)
    enabled = configured and isinstance(policy, WalletIdentityPolicy)
    chain_id = int(settings.web3_chain_id)
    local_demo = bool(
        enabled
        and chain_id == 31337
        and settings.deployment_mode == "local"
    )
    if not enabled:
        notice = "钱包签名登录未启用"
    elif local_demo:
        notice = "本地演示链；钱包签名不替代法定身份或机构资格核验"
    else:
        notice = "钱包签名仅证明地址控制权；平台资格仍以机构与角色核验为准"
    return WalletCapabilitiesResponse(
        enabled=enabled,
        chain_id=chain_id,
        chain_name=_chain_name(chain_id),
        local_demo=local_demo,
        notice=notice,
    )


@router.get("/bindings", response_model=list[BindingStatusResponse])
async def list_current_wallet_bindings(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> list[BindingStatusResponse]:
    """List only bindings owned by the authenticated platform account."""

    ensure_web3_enabled(request)
    async with session.begin():
        await ensure_web3_space_scope(session, request)
        user, _, _ = await resolve_session_user(session, request)
        bindings = list(
            (
                await session.scalars(
                    select(WalletIdentityBinding)
                    .where(WalletIdentityBinding.user_id == user.id)
                    .order_by(WalletIdentityBinding.created_at.desc())
                )
            ).all()
        )
    return [_binding_payload(binding) for binding in bindings]


@router.get("/bindings/review-queue", response_model=list[BindingStatusResponse])
async def list_pending_wallet_bindings(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> list[BindingStatusResponse]:
    """List pending bindings in the current operator's space and chain."""

    ensure_web3_enabled(request)
    async with session.begin():
        _, operator_binding, _ = await _operator_context(session, request)
        bindings = list(
            (
                await session.scalars(
                    select(WalletIdentityBinding)
                    .where(
                        WalletIdentityBinding.space_id
                        == operator_binding.space_id,
                        WalletIdentityBinding.chain_id
                        == operator_binding.chain_id,
                        WalletIdentityBinding.status == "pending",
                    )
                    .order_by(WalletIdentityBinding.created_at)
                )
            ).all()
        )
    return [_binding_payload(binding) for binding in bindings]


@router.post(
    "/challenge",
    response_model=ChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def login_challenge(
    payload: WalletChallengeRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ChallengeResponse:
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            await ensure_web3_space_scope(session, request)
            issued = await create_login_challenge(
                session,
                policy=_policy(request),
                chain_id=payload.chain_id,
                wallet_address=payload.wallet_address,
            )
    except WalletIdentityError as exc:
        _raise_service_error(exc)
    return ChallengeResponse(
        challenge_id=issued.challenge_id,
        purpose=issued.purpose,
        message=issued.message,
        expires_at=issued.expires_at,
    )


@router.post("/verify", response_model=LoginResponse)
async def login_verify(
    payload: WalletVerifyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    ensure_web3_enabled(request)
    policy = _policy(request)
    try:
        async with session.begin():
            await ensure_web3_space_scope(session, request)
            issued = await verify_login_challenge(
                session,
                policy=policy,
                challenge_id=payload.challenge_id,
                message=payload.message,
                signature=payload.signature,
                session_factory=_session_factory(request),
            )
    except WalletIdentityError as exc:
        _raise_service_error(exc)
    remaining_seconds = max(
        1,
        int(
            (
                issued.session.expires_at - datetime.now(timezone.utc)
            ).total_seconds()
        ),
    )
    set_session_cookie(
        response,
        issued.session.secret,
        secure=request.app.state.settings.cookie_secure,
        max_age=remaining_seconds,
    )
    return LoginResponse()


@router.post(
    "/bind/challenge",
    response_model=ChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bind_challenge(
    payload: BindChallengeRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ChallengeResponse:
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            context = await ensure_web3_space_scope(session, request)
            if payload.space_id != context.space_id:
                raise HTTPException(
                    status_code=403,
                    detail="不得绑定其他协作空间的钱包资格",
                )
            # Binding never accepts a client role. The cookie session resolves
            # the user, and the service derives the only eligible role from DB.
            user, _, _ = await resolve_session_user(session, request)
            issued = await create_bind_challenge(
                session,
                policy=_policy(request),
                user=user,
                space_id=payload.space_id,
                chain_id=payload.chain_id,
                wallet_address=payload.wallet_address,
            )
    except WalletIdentityError as exc:
        _raise_service_error(exc)
    return ChallengeResponse(
        challenge_id=issued.challenge_id,
        purpose=issued.purpose,
        message=issued.message,
        expires_at=issued.expires_at,
    )


@router.post("/bind/verify", response_model=BindingResponse)
async def bind_verify(
    payload: WalletVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> BindingResponse:
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            await ensure_web3_space_scope(session, request)
            user, _, _ = await resolve_session_user(session, request)
            binding = await verify_bind_challenge(
                session,
                policy=_policy(request),
                user=user,
                challenge_id=payload.challenge_id,
                message=payload.message,
                signature=payload.signature,
            )
    except WalletIdentityError as exc:
        _raise_service_error(exc)
    return BindingResponse(binding_id=binding.id, status=binding.status)


@router.post("/bindings/{binding_id}/credential/local-demo-bootstrap")
async def bootstrap_local_operator_credential(
    binding_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Break the first-operator credential cycle on disposable Hardhat only."""

    ensure_web3_enabled(request)
    settings = request.app.state.settings
    if settings.deployment_mode != "local" or settings.web3_chain_id != 31337:
        raise HTTPException(
            status_code=403,
            detail="运营资格自举仅限一次性本地 Hardhat 演示链",
        )
    try:
        async with session.begin():
            actor, _ = await current_demo_actor(session, request)
            context = await ensure_web3_space_scope(session, request)
            if actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="仅运营方可初始化自身演示资格")
            actor_user_id = actor.user_id
            target = await session.scalar(
                select(WalletIdentityBinding)
                .where(
                    WalletIdentityBinding.id == binding_id,
                    WalletIdentityBinding.space_id == context.space_id,
                )
                .with_for_update()
            )
            if target is None:
                raise HTTPException(status_code=404, detail="钱包绑定不存在")
            if target.user_id != actor.user_id or target.role_code != "space_operator":
                raise HTTPException(status_code=403, detail="只能初始化当前运营账号的钱包资格")
            if target.status == "active":
                return {
                    **_binding_payload(target).model_dump(mode="json"),
                    "automatic_bootstrap": "already_completed",
                    "idempotent_replay": True,
                }
            if target.wallet_address != settings.web3_credential_issuer_address:
                raise HTTPException(
                    status_code=409,
                    detail="请使用本次部署清单中的 operator 演示钱包完成绑定",
                )
            target_space_id = target.space_id
            prepared = await _pending_issue_call(
                session,
                binding=target,
                credential_contract_address=settings.web3_role_credential_address,
            )

        transport = UrllibJsonRpcTransport(
            configured_rpc_url=settings.web3_rpc_url,
            timeout_seconds=settings.web3_rpc_timeout_seconds,
        )
        # Persist the broadcast hash before waiting for finality, so refreshing
        # an interrupted local bootstrap resumes the same transaction.
        async with session.begin():
            current_actor, _ = await current_demo_actor(session, request)
            if (
                current_actor.user_id != actor_user_id
                or current_actor.role != "space_operator"
            ):
                raise HTTPException(status_code=403, detail="运营账号状态已变化")
            submitted_target = await session.scalar(
                select(WalletIdentityBinding)
                .where(
                    WalletIdentityBinding.id == binding_id,
                    WalletIdentityBinding.space_id == target_space_id,
                )
                .with_for_update()
            )
            if submitted_target is None or submitted_target.status != "pending":
                raise CredentialIssuanceError(
                    "operator wallet binding changed during bootstrap"
                )
            current_prepared = await _pending_issue_call(
                session,
                binding=submitted_target,
                credential_contract_address=settings.web3_role_credential_address,
            )
            if current_prepared != prepared:
                raise CredentialIssuanceError(
                    "operator wallet binding changed during bootstrap"
                )
            transaction_hash = submitted_target.credential_issuance_tx_hash
            if transaction_hash is None:
                transaction_hash = await asyncio.to_thread(
                    send_unlocked_local_transaction,
                    transport,
                    expected_chain_id=31337,
                    sender=settings.web3_credential_issuer_address,
                    contract_address=prepared.to,
                    calldata=prepared.data,
                )
                submitted_target.credential_issuance_tx_hash = transaction_hash
                submitted_target.updated_at = datetime.now(timezone.utc)
                submitted_target.row_version += 1
                await session.flush([submitted_target])

        raw_receipt = await asyncio.to_thread(
            load_local_transaction_receipt,
            transport,
            expected_chain_id=31337,
            transaction_hash=transaction_hash,
        )
        log_index = event_log_index(
            raw_receipt,
            contract_address=prepared.to,
            event_topic=EVENT_TOPICS["CredentialIssued"],
        )
        receipt = await _verify_credential_receipt(
            request,
            prepared_call=prepared,
            payload=CredentialReceiptRequest(
                transaction_hash=transaction_hash,
                log_index=log_index,
            ),
        )
        if not receipt.confirmed:
            raise CredentialIssuanceError("operator bootstrap receipt is not final")

        async with session.begin():
            current_actor, _ = await current_demo_actor(session, request)
            if current_actor.user_id != actor_user_id or current_actor.role != "space_operator":
                raise HTTPException(status_code=403, detail="运营账号状态已变化")
            operator_user = await session.get(User, current_actor.user_id)
            if operator_user is None:
                raise HTTPException(status_code=403, detail="空间运营账号不存在")
            locked_target = await session.scalar(
                select(WalletIdentityBinding).where(
                    WalletIdentityBinding.id == binding_id,
                    WalletIdentityBinding.space_id == target_space_id,
                )
                .with_for_update()
            )
            if locked_target is None or locked_target.status != "pending":
                raise CredentialIssuanceError("operator wallet binding changed during bootstrap")
            if locked_target.credential_issuance_tx_hash != transaction_hash:
                raise CredentialIssuanceError(
                    "operator bootstrap transaction changed before receipt application"
                )
            current_prepared = await _pending_issue_call(
                session,
                binding=locked_target,
                credential_contract_address=settings.web3_role_credential_address,
            )
            if current_prepared != prepared:
                raise CredentialIssuanceError("operator wallet binding changed during bootstrap")
            result = await apply_credential_issued_receipt(
                session,
                binding_id=binding_id,
                prepared_call=current_prepared,
                operator_user=operator_user,
                issuer_address=settings.web3_credential_issuer_address,
                receipt=receipt,
                minimum_confirmations=settings.web3_required_confirmations,
            )
            updated = await session.get(WalletIdentityBinding, binding_id)
            assert updated is not None
        return {
            **_binding_payload(updated).model_dump(mode="json"),
            "automatic_bootstrap": "completed",
            "idempotent_replay": result.idempotent_replay,
            "transaction_hash": transaction_hash,
            "chain_event_receipt_id": str(result.chain_event_receipt_id),
            "security_boundary": (
                "仅本地 Hardhat 首个运营资格自举；生产环境必须由独立身份治理流程签发"
            ),
        }
    except HTTPException:
        raise
    except (
        CredentialIssuanceError,
        LocalDemoRelayerError,
        RpcReceiptError,
        WalletIdentityError,
        ValueError,
    ) as exc:
        if isinstance(exc, WalletIdentityError):
            _raise_service_error(exc)
        raise web3_http_error(exc) from exc


@router.post("/bindings/{binding_id}/credential/prepare")
async def prepare_binding_credential(
    binding_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Prepare the exact SBT issue transaction for one reviewed binding."""

    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            _, operator_binding, _ = await _operator_context(session, request)
            _assert_issuer_wallet(request, operator_binding)
            target = await session.get(WalletIdentityBinding, binding_id)
            if target is None:
                raise HTTPException(status_code=404, detail="钱包绑定不存在")
            _assert_same_space(operator_binding, target)
            prepared = await _pending_issue_call(
                session,
                binding=target,
                credential_contract_address=settings.web3_role_credential_address,
            )
        return {
            **_binding_payload(target).model_dump(mode="json"),
            "holder_wallet": target.wallet_address,
            "credential_scope_digest": prepared.credential_scope_digest,
            "credential_expires_at": prepared.expires_at.isoformat(),
            "expected_event": prepared.expected_event,
            "expected_event_topic": EVENT_TOPICS[prepared.expected_event],
            "transaction": transaction_payload(
                to=prepared.to,
                data=prepared.data,
                label="签发可撤销、可过期的平台角色资格凭证",
                from_wallet=operator_binding.wallet_address,
            ),
            "security_boundary": (
                "该凭证仅证明平台已审核的角色范围，不替代法定 KYC、"
                "医疗资质或线下机构核验"
            ),
        }
    except HTTPException:
        raise
    except (CredentialIssuanceError, WalletIdentityError, ValueError) as exc:
        if isinstance(exc, WalletIdentityError):
            _raise_service_error(exc)
        raise web3_http_error(exc) from exc


@router.post("/bindings/{binding_id}/credential/receipts")
async def synchronize_binding_credential(
    binding_id: UUID,
    payload: CredentialReceiptRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Verify a finalized CredentialIssued event, then activate its binding."""

    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            _, operator_binding, _ = await _operator_context(session, request)
            _assert_issuer_wallet(request, operator_binding)
            target = await session.get(WalletIdentityBinding, binding_id)
            if target is None:
                raise HTTPException(status_code=404, detail="钱包绑定不存在")
            _assert_same_space(operator_binding, target)
            if target.status in {"active", "revoked"}:
                replay = await _find_applied_credential_replay(
                    session,
                    binding=target,
                    transaction_hash=payload.transaction_hash,
                    log_index=payload.log_index,
                    credential_contract_address=settings.web3_role_credential_address,
                    issuer_address=operator_binding.wallet_address,
                )
                if replay is None:
                    raise CredentialIssuanceError(
                        "credential receipt does not match this activated binding"
                    )
                return {
                    **_binding_payload(target).model_dump(mode="json"),
                    "chain_event_receipt_id": str(replay.id),
                    "receipt_status": "confirmed",
                    "confirmations": replay.confirmations,
                    "applied": True,
                    "idempotent_replay": True,
                }
            prepared = await _pending_issue_call(
                session,
                binding=target,
                credential_contract_address=settings.web3_role_credential_address,
            )

        # RPC access is deliberately outside the database transaction.  The
        # configured server transport, never a client URL, supplies finality.
        receipt = await _verify_credential_receipt(
            request, prepared_call=prepared, payload=payload
        )
        if not receipt.confirmed:
            return {
                **_binding_payload(target).model_dump(mode="json"),
                "receipt_status": receipt.status.value,
                "confirmations": receipt.confirmations,
                "required_confirmations": receipt.minimum_confirmations,
                "applied": False,
                "idempotent_replay": False,
            }

        async with session.begin():
            _, current_operator_binding, operator_user = await _operator_context(
                session, request
            )
            _assert_issuer_wallet(request, current_operator_binding)
            locked_target = await session.scalar(
                select(WalletIdentityBinding)
                .where(WalletIdentityBinding.id == binding_id)
                .with_for_update()
            )
            if locked_target is None:
                raise HTTPException(status_code=404, detail="钱包绑定不存在")
            _assert_same_space(current_operator_binding, locked_target)
            if locked_target.status in {"active", "revoked"}:
                replay = await _find_applied_credential_replay(
                    session,
                    binding=locked_target,
                    transaction_hash=payload.transaction_hash,
                    log_index=payload.log_index,
                    credential_contract_address=settings.web3_role_credential_address,
                    issuer_address=current_operator_binding.wallet_address,
                )
                if replay is None:
                    raise CredentialIssuanceError(
                        "credential receipt conflicts with this activated binding"
                    )
                return {
                    **_binding_payload(locked_target).model_dump(mode="json"),
                    "chain_event_receipt_id": str(replay.id),
                    "receipt_status": receipt.status.value,
                    "confirmations": receipt.confirmations,
                    "applied": True,
                    "idempotent_replay": True,
                }
            current_prepared = await _pending_issue_call(
                session,
                binding=locked_target,
                credential_contract_address=settings.web3_role_credential_address,
            )
            if current_prepared != prepared:
                raise CredentialIssuanceError(
                    "wallet binding changed while the chain receipt was verified"
                )
            result = await apply_credential_issued_receipt(
                session,
                binding_id=binding_id,
                prepared_call=current_prepared,
                operator_user=operator_user,
                issuer_address=current_operator_binding.wallet_address,
                receipt=receipt,
                minimum_confirmations=settings.web3_required_confirmations,
            )
            updated = await session.get(WalletIdentityBinding, binding_id)
            assert updated is not None
        return {
            **_binding_payload(updated).model_dump(mode="json"),
            "chain_event_receipt_id": str(result.chain_event_receipt_id),
            "receipt_status": receipt.status.value,
            "confirmations": receipt.confirmations,
            "applied": True,
            "idempotent_replay": result.idempotent_replay,
        }
    except HTTPException:
        raise
    except (
        CredentialIssuanceError,
        RpcReceiptError,
        WalletIdentityError,
        ValueError,
    ) as exc:
        if isinstance(exc, WalletIdentityError):
            _raise_service_error(exc)
        raise web3_http_error(exc) from exc


@router.post("/bindings/{binding_id}/approve", response_model=BindingResponse)
async def approve_binding(
    binding_id: UUID,
    payload: ApproveBindingRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> BindingResponse:
    ensure_web3_enabled(request)
    try:
        async with session.begin():
            await ensure_web3_space_scope(session, request)
            operator_user, _, _ = await resolve_session_user(session, request)
            receipt = await load_verified_credential_receipt(
                session, payload.chain_event_receipt_id
            )
            binding = await approve_wallet_binding(
                session,
                binding_id=binding_id,
                operator_user=operator_user,
                receipt=receipt,
                minimum_confirmations=_policy(
                    request
                ).minimum_receipt_confirmations,
            )
    except WalletIdentityError as exc:
        _raise_service_error(exc)
    return BindingResponse(
        binding_id=binding.id,
        status=binding.status,
        credential_expires_at=binding.credential_expires_at,
    )


@router.post("/bindings/{binding_id}/credential/revoke/prepare")
async def prepare_binding_credential_revocation(
    binding_id: UUID,
    payload: CredentialRevocationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            _, operator_binding, _ = await _operator_context(session, request)
            _assert_issuer_wallet(request, operator_binding)
            binding = await session.get(WalletIdentityBinding, binding_id)
            if binding is None:
                raise HTTPException(status_code=404, detail="钱包绑定不存在")
            _assert_same_space(operator_binding, binding)
            prepared = prepare_credential_revocation(
                binding,
                credential_contract_address=settings.web3_role_credential_address,
                reason=payload.reason,
            )
        return {
            **_binding_payload(binding).model_dump(mode="json"),
            "reason_digest": prepared.reason_digest,
            "expected_event": prepared.expected_event,
            "expected_event_topic": EVENT_TOPICS[prepared.expected_event],
            "transaction": transaction_payload(
                to=prepared.to,
                data=prepared.data,
                label="撤销链上平台角色资格凭证",
                from_wallet=operator_binding.wallet_address,
            ),
        }
    except HTTPException:
        raise
    except (CredentialIssuanceError, WalletIdentityError, ValueError) as exc:
        if isinstance(exc, WalletIdentityError):
            _raise_service_error(exc)
        raise web3_http_error(exc) from exc


@router.post("/bindings/{binding_id}/credential/revoke/receipts")
async def synchronize_binding_credential_revocation(
    binding_id: UUID,
    payload: CredentialRevocationReceiptRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """Apply DB revocation only after the canonical chain event is final."""

    ensure_web3_enabled(request)
    settings = request.app.state.settings
    try:
        async with session.begin():
            _, operator_binding, _ = await _operator_context(session, request)
            _assert_issuer_wallet(request, operator_binding)
            binding = await session.get(WalletIdentityBinding, binding_id)
            if binding is None:
                raise HTTPException(status_code=404, detail="钱包绑定不存在")
            _assert_same_space(operator_binding, binding)
            if binding.status == "revoked":
                existing = await session.scalar(
                    select(ChainEventReceipt).where(
                        ChainEventReceipt.chain_id == binding.chain_id,
                        ChainEventReceipt.transaction_hash
                        == payload.transaction_hash.lower(),
                        ChainEventReceipt.log_index == payload.log_index,
                    )
                )
                prepared = reconstruct_credential_revocation(
                    binding,
                    credential_contract_address=settings.web3_role_credential_address,
                    reason=payload.reason,
                )
                replay = _assert_revocation_replay(
                    existing,
                    binding=binding,
                    prepared_call=prepared,
                    issuer_address=operator_binding.wallet_address,
                )
                return {
                    **_binding_payload(binding).model_dump(mode="json"),
                    "chain_event_receipt_id": str(replay.id),
                    "applied": True,
                    "idempotent_replay": True,
                }
            prepared = prepare_credential_revocation(
                binding,
                credential_contract_address=settings.web3_role_credential_address,
                reason=payload.reason,
            )

        receipt = await _verify_credential_revocation_receipt(
            request,
            prepared_call=prepared,
            payload=payload,
        )
        if not receipt.confirmed:
            return {
                **_binding_payload(binding).model_dump(mode="json"),
                "receipt_status": receipt.status.value,
                "confirmations": receipt.confirmations,
                "required_confirmations": receipt.minimum_confirmations,
                "applied": False,
                "idempotent_replay": False,
            }
        _validate_revocation_event(
            receipt,
            binding=binding,
            prepared_call=prepared,
            issuer_address=operator_binding.wallet_address,
        )

        async with session.begin():
            _, current_operator_binding, operator_user = await _operator_context(
                session, request
            )
            _assert_issuer_wallet(request, current_operator_binding)
            locked_binding = await session.scalar(
                select(WalletIdentityBinding)
                .where(WalletIdentityBinding.id == binding_id)
                .with_for_update()
            )
            if locked_binding is None:
                raise HTTPException(status_code=404, detail="钱包绑定不存在")
            _assert_same_space(current_operator_binding, locked_binding)
            current_prepared = prepare_credential_revocation(
                locked_binding,
                credential_contract_address=settings.web3_role_credential_address,
                reason=payload.reason,
            )
            if current_prepared != prepared:
                raise CredentialIssuanceError(
                    "wallet binding changed while the revocation receipt was verified"
                )
            existing = await session.scalar(
                select(ChainEventReceipt).where(
                    ChainEventReceipt.chain_id == receipt.chain_id,
                    ChainEventReceipt.transaction_hash == receipt.tx_hash,
                    ChainEventReceipt.log_index == receipt.expected_log_index,
                )
            )
            if existing is not None:
                raise CredentialIssuanceError(
                    "chain event position is already assigned to another operation"
                )
            now = datetime.now(timezone.utc)
            snapshot = _revocation_snapshot(
                locked_binding,
                current_prepared,
                issuer_address=current_operator_binding.wallet_address,
            )
            chain_receipt = ChainEventReceipt(
                space_id=locked_binding.space_id,
                chain_id=receipt.chain_id,
                contract_address=current_prepared.to,
                transaction_hash=receipt.tx_hash,
                log_index=receipt.expected_log_index,
                block_number=receipt.block_number,
                block_hash=receipt.block_hash,
                event_name="CredentialRevoked",
                subject_type="wallet_identity_binding",
                subject_key=str(locked_binding.id),
                actor_wallet=current_operator_binding.wallet_address,
                payload_snapshot=snapshot,
                payload_digest=canonical_json_digest_v1(snapshot),
                confirmations=receipt.confirmations,
                status="finalized",
                first_seen_at=now,
                finalized_at=now,
            )
            session.add(chain_receipt)
            await session.flush()
            updated = await revoke_wallet_binding(
                session,
                binding_id=binding_id,
                operator_user=operator_user,
                reason=current_prepared.reason,
            )
            chain_receipt.status = "applied"
            chain_receipt.applied_at = now
            await session.flush()
        return {
            **_binding_payload(updated).model_dump(mode="json"),
            "chain_event_receipt_id": str(chain_receipt.id),
            "receipt_status": receipt.status.value,
            "confirmations": receipt.confirmations,
            "applied": True,
            "idempotent_replay": False,
        }
    except HTTPException:
        raise
    except (
        CredentialIssuanceError,
        RpcReceiptError,
        WalletIdentityError,
        ValueError,
    ) as exc:
        if isinstance(exc, WalletIdentityError):
            _raise_service_error(exc)
        raise web3_http_error(exc) from exc
