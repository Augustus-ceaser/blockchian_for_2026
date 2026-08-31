from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.modules.role_assistant.orchestrator import query_role_assistant

from app.modules.role_assistant.planner import (
    PUBLIC_ROLES,
    RoleAssistantPlan,
    contains_sensitive_identifier,
    plan_role_assistant,
)
from app.modules.role_assistant.schemas import RoleAssistantQueryResponse

router = APIRouter(prefix="/role-assistant", tags=["role-assistant"])


class RoleAssistantPlanRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    conversation_id: UUID | None = None

    @field_validator("message", mode="before")
    @classmethod
    def strip_message(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


@router.post("/plan", response_model=RoleAssistantPlan)
async def create_role_assistant_plan(
    payload: RoleAssistantPlanRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
) -> RoleAssistantPlan:
    # Outside tests, the application middleware replaces this header with the
    # role from the authenticated server-side session before the route runs.
    if identity not in PUBLIC_ROLES:
        raise HTTPException(status_code=403, detail="当前账号不能使用角色助手")
    return await plan_role_assistant(
        role=identity,
        message=payload.message,
        settings=request.app.state.settings,
    )


@router.post("/query", response_model=RoleAssistantQueryResponse)
async def execute_role_assistant_query(
    payload: RoleAssistantPlanRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
) -> RoleAssistantQueryResponse:
    if identity not in PUBLIC_ROLES:
        raise HTTPException(status_code=403, detail="当前账号不能使用角色助手")
    if contains_sensitive_identifier(payload.message):
        raise HTTPException(
            status_code=422,
            detail="请先移除姓名、病历号、住院号、手机号、邮箱或身份证号后再查询",
        )
    return await query_role_assistant(
        role=identity,
        message=payload.message,
        settings=request.app.state.settings,
        session=session,
        conversation_id=payload.conversation_id,
    )
