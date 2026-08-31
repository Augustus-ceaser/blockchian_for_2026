from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.modules.catalog.models import DataProduct
from app.modules.identity.local_auth import (
    ROLE_BY_SUBJECT,
    authenticate_local_demo,
    clear_session_cookie,
    resolve_session_user,
    revoke_current_session,
    set_session_cookie,
)
from app.modules.identity.models import (
    LocalDemoCredential,
    LocalDemoSession,
    Organization,
    OrganizationMember,
    User,
)
from app.modules.lifecycle.models import ProductLifecycleRequest

router = APIRouter(prefix="/auth", tags=["local-demo-auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=3, max_length=256)


async def _profile(session: AsyncSession, request: Request) -> dict[str, str]:
    user, role, _ = await resolve_session_user(session, request)
    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.status == "active",
        )
    )
    organization = None if membership is None else await session.get(Organization, membership.organization_id)
    if organization is None:
        raise HTTPException(status_code=403, detail="账号没有有效机构成员关系")
    return {
        "role": role,
        "user_name": user.display_name,
        "organization": organization.display_name,
        "organization_id": str(organization.id),
    }


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
):
    client_host = request.client.host if request.client is not None else "unknown"
    rate_limit_key = f"{client_host}:{payload.username.strip().lower()}"
    request.app.state.login_rate_limiter.check(rate_limit_key)
    settings = request.app.state.settings
    short_password = len(payload.password) < 12
    loopback_client = client_host in {"127.0.0.1", "::1", "localhost"}
    test_client = settings.app_env == "test" and client_host == "testclient"
    if (
        len(payload.password) < settings.password_min_length
        or short_password
        and not (
            settings.allow_weak_local_demo_credentials
            and settings.deployment_mode == "local"
            and (loopback_client or test_client)
        )
    ):
        request.app.state.login_rate_limiter.record_failure(rate_limit_key)
        raise HTTPException(status_code=401, detail="账号或密码无效")
    async with session.begin():
        try:
            _, _, secret = await authenticate_local_demo(
                session,
                username=payload.username,
                password=payload.password,
                session_lifetime=timedelta(
                    seconds=settings.session_lifetime_seconds
                ),
            )
        except HTTPException:
            request.app.state.login_rate_limiter.record_failure(rate_limit_key)
            raise
    request.app.state.login_rate_limiter.reset(rate_limit_key)
    set_session_cookie(
        response,
        secret,
        secure=settings.cookie_secure,
        max_age=settings.session_lifetime_seconds,
    )
    return {"authenticated": True}


@router.get("/me")
async def me(request: Request, session: AsyncSession = Depends(get_db_session)):
    async with session.begin():
        return await _profile(session, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    async with session.begin():
        await revoke_current_session(session, request)
    response.status_code = status.HTTP_204_NO_CONTENT
    clear_session_cookie(
        response, secure=request.app.state.settings.cookie_secure
    )
    return response


@router.get("/status")
async def local_demo_status(
    request: Request, session: AsyncSession = Depends(get_db_session)
):
    from app.modules.marketplace.models import ModelProduct

    async with session.begin():
        profile = await _profile(session, request)
        if profile["role"] != "space_operator":
            raise HTTPException(status_code=403, detail="仅空间运营账号可查看系统状态")
        now = datetime.now(timezone.utc)
        return {
            "accounts": int(await session.scalar(select(func.count()).select_from(LocalDemoCredential)) or 0),
            "active_sessions": int(
                await session.scalar(
                    select(func.count()).select_from(LocalDemoSession).where(
                        LocalDemoSession.revoked_at.is_(None),
                        LocalDemoSession.expires_at > now,
                    )
                )
                or 0
            ),
            "pending_lifecycle_requests": int(
                await session.scalar(
                    select(func.count()).select_from(ProductLifecycleRequest).where(
                        ProductLifecycleRequest.status == "pending"
                    )
                )
                or 0
            ),
            "unpublished_products": int(
                (await session.scalar(select(func.count()).select_from(DataProduct).where(DataProduct.lifecycle_status == "unpublished")) or 0)
                + (await session.scalar(select(func.count()).select_from(ModelProduct).where(ModelProduct.lifecycle_status == "unpublished")) or 0)
            ),
            "archived_products": int(
                (await session.scalar(select(func.count()).select_from(DataProduct).where(DataProduct.lifecycle_status == "archived")) or 0)
                + (await session.scalar(select(func.count()).select_from(ModelProduct).where(ModelProduct.lifecycle_status == "archived")) or 0)
            ),
        }


@router.get("/sessions")
async def active_demo_sessions(
    request: Request, session: AsyncSession = Depends(get_db_session)
):
    async with session.begin():
        profile = await _profile(session, request)
        if profile["role"] != "space_operator":
            raise HTTPException(status_code=403, detail="仅空间运营账号可查看设备会话")
        now = datetime.now(timezone.utc)
        rows = (
            await session.execute(
                select(LocalDemoSession, User, Organization)
                .join(User, User.id == LocalDemoSession.user_id)
                .join(
                    OrganizationMember,
                    OrganizationMember.user_id == User.id,
                )
                .join(
                    Organization,
                    Organization.id == OrganizationMember.organization_id,
                )
                .where(
                    OrganizationMember.status == "active",
                    LocalDemoSession.revoked_at.is_(None),
                    LocalDemoSession.expires_at > now,
                )
                .order_by(LocalDemoSession.last_seen_at.desc())
            )
        ).all()
        return {
            "sessions": [
                {
                    "role": ROLE_BY_SUBJECT.get(user.identity_subject, "unknown"),
                    "organization": organization.display_name,
                    "created_at": login_session.created_at,
                    "last_seen_at": login_session.last_seen_at,
                    "expires_at": login_session.expires_at,
                    "activity": (
                        "active"
                        if (now - login_session.last_seen_at).total_seconds() <= 120
                        else "recent"
                    ),
                }
                for login_session, user, organization in rows
            ]
        }
