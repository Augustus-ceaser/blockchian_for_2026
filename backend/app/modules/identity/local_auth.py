from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import LocalDemoCredential, LocalDemoSession, User

SESSION_COOKIE = "medtrust_local_session"
SESSION_LIFETIME = timedelta(hours=12)
ROLE_BY_SUBJECT = {
    "phase4:space_operator": "space_operator",
    "phase4:data_provider": "data_provider",
    "phase4:model_provider": "model_provider",
    "phase4:data_requester": "data_requester",
    "phase4:catalog_curator": "catalog_curator",
}
ROLE_BY_IDENTITY_SUBJECT = {
    **ROLE_BY_SUBJECT,
    "public-alpha:space_operator": "space_operator",
    "public-alpha:data_provider": "data_provider",
    "public-alpha:model_provider": "model_provider",
    "public-alpha:data_requester": "data_requester",
    "public-alpha:catalog_curator": "catalog_curator",
}
USERNAME_BY_ROLE = {
    "space_operator": "operator.demo",
    "data_provider": "hospital.demo",
    "model_provider": "model.demo",
    "data_requester": "requester.demo",
    "catalog_curator": "catalog.curator.demo",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _password_hash(
    password: str, salt: bytes | None = None, *, min_length: int = 8
) -> str:
    if len(password) < min_length:
        raise ValueError(
            f"local demo password must be at least {min_length} characters"
        )
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$%s$%s" % (
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def session_digest(secret: str) -> str:
    return f"sha256:{hashlib.sha256(secret.encode('ascii')).hexdigest()}"


async def ensure_local_demo_credentials(
    session: AsyncSession,
    *,
    password: str | None = None,
    passwords: dict[str, str] | None = None,
    min_password_length: int = 8,
) -> None:
    """Idempotently bind the seeded Phase 4 users to local usernames."""

    for subject, role in ROLE_BY_SUBJECT.items():
        username = USERNAME_BY_ROLE[role]
        selected_password = (passwords or {}).get(username) or password
        if not selected_password:
            raise ValueError(f"local demo password is missing for {username}")
        user = await session.scalar(
            select(User).where(
                User.identity_issuer == "medtrust-demo",
                User.identity_subject == subject,
            )
        )
        if user is None:
            raise ValueError("Phase 4 demo identities must be initialized first")
        credential = await session.scalar(
            select(LocalDemoCredential).where(LocalDemoCredential.user_id == user.id)
        )
        if credential is None:
            session.add(
                LocalDemoCredential(
                    user_id=user.id,
                    username=username,
                    password_hash=_password_hash(
                        selected_password, min_length=min_password_length
                    ),
                    is_enabled=True,
                )
            )
            continue
        changed = (
            credential.username != username
            or not credential.is_enabled
            or not verify_password(selected_password, credential.password_hash)
        )
        if not changed:
            continue
        credential.username = username
        credential.password_hash = _password_hash(
            selected_password, min_length=min_password_length
        )
        credential.is_enabled = True
        credential.updated_at = utc_now()
        await session.execute(
            update(LocalDemoSession)
            .where(
                LocalDemoSession.user_id == user.id,
                LocalDemoSession.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )


async def authenticate_local_demo(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    session_lifetime: timedelta = SESSION_LIFETIME,
) -> tuple[User, LocalDemoSession, str]:
    credential = await session.scalar(
        select(LocalDemoCredential)
        .where(LocalDemoCredential.username == username.strip().lower())
        .with_for_update()
    )
    if credential is None or not credential.is_enabled or not verify_password(
        password, credential.password_hash
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码无效")
    user = await session.get(User, credential.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号不可用")
    raw_secret = secrets.token_urlsafe(32)
    now = utc_now()
    login_session = LocalDemoSession(
        user_id=user.id,
        session_digest=session_digest(raw_secret),
        expires_at=now + session_lifetime,
        created_at=now,
        last_seen_at=now,
    )
    user.last_authenticated_at = now
    session.add(login_session)
    await session.flush()
    return user, login_session, raw_secret


async def resolve_session_user(
    session: AsyncSession, request: Request
) -> tuple[User, str, LocalDemoSession]:
    raw_secret = request.cookies.get(SESSION_COOKIE)
    if not raw_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    now = utc_now()
    login_session = await session.scalar(
        select(LocalDemoSession)
        .where(
            LocalDemoSession.session_digest == session_digest(raw_secret),
            LocalDemoSession.revoked_at.is_(None),
            LocalDemoSession.expires_at > now,
        )
        .with_for_update()
    )
    if login_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录会话已失效")
    user = await session.get(User, login_session.user_id)
    role = (
        None
        if user is None
        else ROLE_BY_IDENTITY_SUBJECT.get(user.identity_subject)
    )
    if user is None or user.status != "active" or role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号无权访问演示门户")
    login_session.last_seen_at = now
    return user, role, login_session


def set_session_cookie(
    response: Response,
    secret: str,
    *,
    secure: bool = False,
    max_age: int = int(SESSION_LIFETIME.total_seconds()),
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        secret,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool = False) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=secure
    )


async def revoke_current_session(session: AsyncSession, request: Request) -> None:
    raw_secret = request.cookies.get(SESSION_COOKIE)
    if not raw_secret:
        return
    login_session = await session.scalar(
        select(LocalDemoSession).where(
            LocalDemoSession.session_digest == session_digest(raw_secret),
            LocalDemoSession.revoked_at.is_(None),
        )
    )
    if login_session is not None:
        login_session.revoked_at = utc_now()


async def authenticated_role_for_request(
    session: AsyncSession, request: Request
) -> str:
    _, role, _ = await resolve_session_user(session, request)
    requested_role = request.headers.get("x-demo-identity")
    enabled = bool(request.app.state.settings.enable_demo_role_switch)
    if requested_role and (not enabled or requested_role != role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="调试身份切换未获授权")
    return role


class LoginRateLimiter:
    """Single-process limiter for the one-worker Public Alpha backend."""

    def __init__(self, *, attempts: int, window_seconds: int) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _active(self, key: str, now: float) -> deque[float]:
        failures = self._failures[key]
        cutoff = now - self.window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def check(self, key: str) -> None:
        if len(self._active(key, time.monotonic())) >= self.attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
                headers={"Retry-After": str(self.window_seconds)},
            )

    def record_failure(self, key: str) -> None:
        self._active(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
