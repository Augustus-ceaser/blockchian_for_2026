from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from eth_utils import keccak
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import (
    DemoActor,
    Phase4DemoContext,
    Phase4DemoError,
    get_phase4_context,
)
from app.modules.identity.local_auth import resolve_session_user
from app.modules.web3.evm_codec import normalize_address
from app.modules.web3.identity_service import binding_scope_digest
from app.modules.web3.models import WalletIdentityBinding
from app.modules.web3.rpc import RpcReceiptError


def ensure_web3_enabled(request: Request) -> None:
    if not request.app.state.settings.web3_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web3 演示功能尚未启用",
        )


def phase4_space_scope_digest(space_id: UUID) -> str:
    return "0x" + keccak(text=f"medtrust:space:{space_id}").hex()


async def ensure_web3_space_scope(
    session: AsyncSession, request: Request
) -> Phase4DemoContext:
    """Fail closed unless this deployment is bound to the current demo space."""

    try:
        context = await get_phase4_context(session)
    except Phase4DemoError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="当前协作空间尚未初始化，链上资格功能不可用",
        ) from exc
    configured = str(
        getattr(request.app.state.settings, "web3_space_scope_digest", "")
    ).strip().lower()
    if configured != phase4_space_scope_digest(context.space_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="链上资格合约与当前协作空间不匹配",
        )
    return context


def web3_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RpcReceiptError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": str(exc)},
        )
    detail = str(exc)
    denied = any(
        marker in detail.lower()
        for marker in (
            "only",
            "required",
            "credential",
            "not eligible",
            "wallet",
            "无权",
            "仅",
        )
    )
    return HTTPException(status_code=403 if denied else 409, detail=detail)


async def _current_demo_actor_context(
    session: AsyncSession, request: Request
) -> tuple[DemoActor, object, Phase4DemoContext]:
    user, role, login_session = await resolve_session_user(session, request)
    context = await ensure_web3_space_scope(session, request)
    actor = context.actors.get(role)
    if actor is None or actor.user_id != user.id:
        raise HTTPException(status_code=403, detail="当前账号不属于演示协作空间")
    return actor, login_session, context


async def current_demo_actor(
    session: AsyncSession, request: Request
) -> tuple[DemoActor, object]:
    actor, login_session, _ = await _current_demo_actor_context(session, request)
    return actor, login_session


async def require_siwe_binding(
    session: AsyncSession, request: Request
) -> tuple[DemoActor, WalletIdentityBinding]:
    actor, login_session, context = await _current_demo_actor_context(session, request)
    if (
        getattr(login_session, "auth_method", "password") != "siwe"
        or getattr(login_session, "wallet_binding_id", None) is None
    ):
        raise HTTPException(status_code=403, detail="该链上操作需要钱包签名会话")
    binding = await session.get(
        WalletIdentityBinding, login_session.wallet_binding_id
    )
    settings = request.app.state.settings
    expires_at = None if binding is None else binding.credential_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    try:
        configured_credential = normalize_address(
            settings.web3_role_credential_address
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="链上资格合约配置无效") from exc
    if (
        binding is None
        or binding.status != "active"
        or binding.user_id != actor.user_id
        or binding.organization_id != actor.organization_id
        or binding.role_code != actor.role
        or binding.space_id != context.space_id
        or binding.chain_id != settings.web3_chain_id
        or binding.credential_contract_address != configured_credential
        or binding.credential_scope_digest != binding_scope_digest(binding)
        or expires_at is None
        or expires_at <= datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=403, detail="钱包资格绑定无效、已撤销或已过期")
    return actor, binding


def transaction_payload(
    *, to: str, data: str, label: str, from_wallet: str | None = None
) -> dict[str, str]:
    payload = {"label": label, "to": to, "data": data, "value": "0x0"}
    if from_wallet is not None:
        payload["from"] = from_wallet
    return payload
