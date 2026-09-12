from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.routes import web3_common


SPACE_ID = UUID("0967d9f7-509a-5583-b214-df66c2eae6de")
SPACE_SCOPE_DIGEST = (
    "0xefe853181e1944f116cb2faa6341619a88a8bf5a5b5bcdb235ca40d17b84e09a"
)


def _request(digest: str) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(web3_space_scope_digest=digest)
            )
        )
    )


def test_phase4_space_scope_digest_matches_contract_deployment_domain() -> None:
    assert web3_common.phase4_space_scope_digest(SPACE_ID) == SPACE_SCOPE_DIGEST


def test_space_scope_accepts_only_the_current_phase4_space(monkeypatch) -> None:
    context = SimpleNamespace(space_id=SPACE_ID)

    async def current_context(session):
        return context

    monkeypatch.setattr(web3_common, "get_phase4_context", current_context)

    accepted = asyncio.run(
        web3_common.ensure_web3_space_scope(object(), _request(SPACE_SCOPE_DIGEST))
    )
    assert accepted is context

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            web3_common.ensure_web3_space_scope(
                object(), _request("0x" + "aa" * 32)
            )
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == "链上资格合约与当前协作空间不匹配"
