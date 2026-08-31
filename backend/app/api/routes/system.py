from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session

router = APIRouter(prefix="/health", tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    service: str
    version: str


class DeploymentResponse(BaseModel):
    mode: Literal[
        "local",
        "lan-roadshow",
        "remote-preview",
        "production-template",
        "pre-icp",
        "public-alpha",
    ]
    label: str
    join_enabled: bool
    public_origin: str
    gateway_port: int
    hard_isolation: bool = False
    executor: str = "unknown"
    demo_credentials: Literal["standard", "weak-local-only"] = "standard"


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
    )


@router.get("/ready", response_model=HealthResponse)
async def ready(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HealthResponse:
    settings = request.app.state.settings
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not ready",
        ) from exc

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
    )


@router.get("/deployment", response_model=DeploymentResponse)
async def deployment(request: Request) -> DeploymentResponse:
    settings = request.app.state.settings
    labels = {
        "local": "本机演示模式",
        "lan-roadshow": "局域网路演模式",
        "remote-preview": "受控远程预览",
        "production-template": "生产部署模板",
    }
    labels["pre-icp"] = "Pre-ICP loopback-only Alpha"
    labels["public-alpha"] = "Public Alpha"
    return DeploymentResponse(
        mode=settings.deployment_mode,
        label=labels[settings.deployment_mode],
        join_enabled=settings.deployment_mode == "lan-roadshow",
        public_origin=settings.public_origin.rstrip("/"),
        gateway_port=settings.gateway_port,
        demo_credentials=(
            "weak-local-only"
            if any(
                password and (len(password) < 12 or password == username)
                for username, password in settings.demo_passwords.items()
            )
            else "standard"
        ),
    )
