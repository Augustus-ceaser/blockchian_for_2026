from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api.routes.web3_operations import _automatic_settlement_target
from app.modules.commerce.models import CommercialOrder
from app.modules.web3.escrow import Web3EscrowError
from app.modules.web3.models import ContractChainAnchor


ORDER_ID = UUID("11111111-1111-4111-8111-111111111111")
SPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
CONTRACT_ID = UUID("33333333-3333-4333-8333-333333333333")
REQUESTER_ORG_ID = UUID("44444444-4444-4444-8444-444444444444")
REVISION_ID = UUID("99999999-9999-4999-8999-999999999999")
RUN_ID = UUID("55555555-5555-4555-8555-555555555555")
PACKAGE_ID = UUID("66666666-6666-4666-8666-666666666666")
ANCHOR_ID = UUID("77777777-7777-4777-8777-777777777777")
CONTENT_DIGEST = "sha256:" + "88" * 32


class _Rows:
    def __init__(self, rows: list[tuple[UUID, UUID]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[UUID, UUID]]:
        return self._rows


class _Session:
    def __init__(
        self,
        rows: list[tuple[UUID, UUID]],
        *,
        order: object | None = None,
    ) -> None:
        self.rows = rows
        self.order = order or SimpleNamespace(
            space_id=SPACE_ID,
            id=ORDER_ID,
            contract_id=CONTRACT_ID,
            requester_organization_id=REQUESTER_ORG_ID,
            agreement_snapshot={
                "contract_id": str(CONTRACT_ID),
                "active_revision_id": str(REVISION_ID),
                "contract_content_digest": CONTENT_DIGEST,
            },
        )
        self.anchor = SimpleNamespace(
            id=ANCHOR_ID,
            space_id=SPACE_ID,
            contract_id=CONTRACT_ID,
            contract_revision_id=REVISION_ID,
            content_digest=CONTENT_DIGEST,
            status="active",
        )
        self.statement = None

    async def get(self, model: object, object_id: UUID) -> object | None:
        if model is CommercialOrder:
            assert object_id == ORDER_ID
            return self.order
        if model is ContractChainAnchor:
            assert object_id == ANCHOR_ID
            return self.anchor
        raise AssertionError(model)

    async def execute(self, statement: object) -> _Rows:
        self.statement = statement
        return _Rows(self.rows)


def _binding() -> object:
    return SimpleNamespace(
        commercial_order_id=ORDER_ID,
        contract_chain_anchor_id=ANCHOR_ID,
        space_id=SPACE_ID,
    )


def test_automatic_settlement_selects_only_approved_quarantined_artifact() -> None:
    session = _Session([(RUN_ID, PACKAGE_ID)])

    target = asyncio.run(_automatic_settlement_target(session, binding=_binding()))

    assert target == (RUN_ID, PACKAGE_ID)
    assert session.statement is not None
    parameters = session.statement.compile().params.values()
    assert "quarantined" in parameters
    assert "released" not in parameters
    assert "succeeded" in parameters
    assert "available" in parameters


def test_automatic_settlement_waits_until_one_approved_package_exists() -> None:
    with pytest.raises(Web3EscrowError, match="waiting for"):
        asyncio.run(_automatic_settlement_target(_Session([]), binding=_binding()))


def test_automatic_settlement_rejects_ambiguous_approved_packages() -> None:
    other_package = UUID("77777777-7777-4777-8777-777777777777")
    with pytest.raises(Web3EscrowError, match="multiple eligible"):
        asyncio.run(
            _automatic_settlement_target(
                _Session([(RUN_ID, PACKAGE_ID), (RUN_ID, other_package)]),
                binding=_binding(),
            )
        )
