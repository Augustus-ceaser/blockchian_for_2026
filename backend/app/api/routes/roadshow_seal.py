from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.data_products import _actor
from app.core.config import get_settings
from app.db.session import get_db_session
from app.modules.roadshow_seal.services import read_business_state

router = APIRouter(prefix="/roadshow-seal", tags=["roadshow-seal"])


@router.get("/overview")
async def unified_roadshow_overview(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    await _actor(session, identity)
    state = await read_business_state(session)
    state["deployment_mode"] = get_settings().deployment_mode
    state["read_only"] = True
    return state
