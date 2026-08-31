from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.db.session import close_database
from app.db.session import session_factory
from app.modules.identity.local_auth import LoginRateLimiter, authenticated_role_for_request


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_database()


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or get_settings()
    application = FastAPI(
        title=current_settings.app_name,
        version=current_settings.app_version,
        debug=current_settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if current_settings.docs_policy == "public" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if current_settings.docs_policy == "public" else None,
    )
    application.state.settings = current_settings
    application.state.auth_session_factory = session_factory
    application.state.login_rate_limiter = LoginRateLimiter(
        attempts=current_settings.login_rate_limit_attempts,
        window_seconds=current_settings.login_rate_limit_window_seconds,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=current_settings.allowed_host_list,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=current_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Idempotency-Key",
            "X-Demo-Role",
            "X-Demo-Identity",
            "X-Download-Token",
        ],
    )
    @application.middleware("http")
    async def inject_authenticated_demo_identity(request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.cookies.get("medtrust_local_session")
        ):
            supplied_origin = request.headers.get("origin")
            supplied_referer = request.headers.get("referer")
            candidate = supplied_origin
            if candidate is None and supplied_referer:
                from urllib.parse import urlsplit

                parsed = urlsplit(supplied_referer)
                candidate = f"{parsed.scheme}://{parsed.netloc}"
            if candidate and candidate.rstrip("/") not in current_settings.trusted_origin_list:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "请求来源未获授权"},
                )
        if request.url.path.startswith(f"{current_settings.api_v1_prefix}/auth/"):
            return await call_next(request)
        if request.url.path.startswith(f"{current_settings.api_v1_prefix}/health/"):
            return await call_next(request)
        if request.url.path.startswith(
            f"{current_settings.api_v1_prefix}/connector-control/bootstrap/"
        ) or request.url.path.startswith(
            f"{current_settings.api_v1_prefix}/connector-control/ingress/"
        ) or request.url.path.startswith(
            f"{current_settings.api_v1_prefix}/policy-control/ingress/"
        ):
            return await call_next(request)
        if request.url.path.startswith(f"{current_settings.api_v1_prefix}/"):
            if current_settings.app_env == "test":
                return await call_next(request)
            async with request.app.state.auth_session_factory() as session:
                try:
                    async with session.begin():
                        role = await authenticated_role_for_request(session, request)
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers,
                    )
            headers = [(key, value) for key, value in request.scope["headers"] if key.lower() != b"x-demo-identity"]
            headers.append((b"x-demo-identity", role.encode("ascii")))
            request.scope["headers"] = headers
        return await call_next(request)
    application.include_router(api_router, prefix=current_settings.api_v1_prefix)

    if current_settings.docs_policy == "operator":
        async def require_operator(request: Request) -> None:
            async with request.app.state.auth_session_factory() as session:
                async with session.begin():
                    role = await authenticated_role_for_request(session, request)
            if role != "space_operator":
                raise HTTPException(status_code=403, detail="仅运营方可访问 API 文档")

        async def protected_openapi(request):
            await require_operator(request)
            return JSONResponse(application.openapi())
        protected_openapi.__annotations__["request"] = Request

        async def protected_docs(request):
            await require_operator(request)
            return get_swagger_ui_html(
                openapi_url="/openapi.json",
                title=f"{current_settings.app_name} - API docs",
            )
        protected_docs.__annotations__["request"] = Request
        application.add_api_route(
            "/openapi.json", protected_openapi, include_in_schema=False
        )
        application.add_api_route("/docs", protected_docs, include_in_schema=False)

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": current_settings.app_name,
            "version": current_settings.app_version,
            "status": "operational-prototype",
        }

    return application


app = create_app()
