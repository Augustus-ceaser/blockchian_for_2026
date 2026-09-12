from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.marketplace.services import (
    MarketplaceServiceError,
    _require_web3_result_delivery_unlocked,
)


SPACE_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ARTIFACT_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
JOB_ID = UUID("33333333-3333-4333-8333-333333333333")
CONTRACT_ID = UUID("44444444-4444-4444-8444-444444444444")
REQUESTER_ID = UUID("55555555-5555-4555-8555-555555555555")


class FakeSession:
    def __init__(self, *, order: object | None, escrow: object | None) -> None:
        self.order = order
        self.escrow = escrow
        self.scalar_calls = 0

    async def get(self, model: object, key: UUID) -> object | None:
        if model is Artifact and key == ARTIFACT_ID:
            return SimpleNamespace(compute_run_id=RUN_ID)
        if model is ComputeRun and key == RUN_ID:
            return SimpleNamespace(compute_job_id=JOB_ID)
        if model is ComputeJob and key == JOB_ID:
            return SimpleNamespace(contract_id=CONTRACT_ID)
        return None

    async def scalar(self, _query: object) -> object | None:
        self.scalar_calls += 1
        return self.order if self.scalar_calls == 1 else self.escrow


def _package() -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=ARTIFACT_ID,
        space_id=SPACE_ID,
        requester_organization_id=REQUESTER_ID,
    )


def test_legacy_result_delivery_remains_available_without_web3_order() -> None:
    asyncio.run(
        _require_web3_result_delivery_unlocked(
            FakeSession(order=None, escrow=None), _package()
        )
    )


def test_web3_result_delivery_waits_for_canonical_settlement() -> None:
    session = FakeSession(
        order=SimpleNamespace(id=UUID("66666666-6666-4666-8666-666666666666")),
        escrow=SimpleNamespace(status="funded"),
    )
    with pytest.raises(MarketplaceServiceError, match="双证明结算"):
        asyncio.run(_require_web3_result_delivery_unlocked(session, _package()))


def test_web3_result_delivery_unlocks_after_settlement() -> None:
    session = FakeSession(
        order=SimpleNamespace(id=UUID("66666666-6666-4666-8666-666666666666")),
        escrow=SimpleNamespace(status="claimable"),
    )
    asyncio.run(_require_web3_result_delivery_unlocked(session, _package()))
