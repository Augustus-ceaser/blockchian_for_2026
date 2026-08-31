from __future__ import annotations

from datetime import datetime, timezone
import asyncio
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pytest
from sqlalchemy.orm import Session, make_transient_to_detached

from app.modules.audit.models import AUDIT_EVENT_TYPES, AUDIT_SUBJECT_TYPES
from app.modules.audit.services import EVENT_SHAPES, canonical_json_digest_v1
from app.modules.commerce.models import (
    CommercialDownloadGrant,
    CommerceInvariantError,
    CommercialOrder,
    guard_commerce_mutations,
)
from app.modules.commerce.gating import (
    CommercialExecutionBlocked,
    commercial_bundle_requirement,
    commercial_execution_decision,
    require_paid_execution_entitlement,
)
from app.modules.commerce.packages import (
    CommercialPackageError,
    PATHMNIST_DATA_DELIVERY_VERSION_ID,
    PATHMNIST_MODEL_LICENSE_VERSION_ID,
    build_delivery_zip,
)
from app.modules.commerce.pricing import (
    channel_fee_for,
    resolve_offer_snapshot,
    round_basis_points,
    split_gross_amount,
)
from app.modules.commerce.services import (
    CommerceError,
    _grant_token,
    commercial_offer_payload_for_role,
    commercial_order_payload,
    provider_settlements,
    require_contract_checkout_security,
)
from app.modules.policy_control.services import (
    PolicyControlError,
    accept_execution_consumption,
    issue_order,
)


def test_integer_fee_split_is_balanced_and_uses_half_up_rounding() -> None:
    assert round_basis_points(1, 5_000) == 1
    assert split_gross_amount(68_000) == (3_400, 64_600)
    assert split_gross_amount(39_900) == (1_995, 37_905)
    assert channel_fee_for(107_900) == 647
    platform, provider = split_gross_amount(1_980_000)
    assert platform == 99_000
    assert provider == 1_881_000
    assert platform + provider == 1_980_000


def test_frozen_demo_offers_are_cny_backend_authoritative() -> None:
    data = resolve_offer_snapshot(
        product_kind="data",
        version_id=str(uuid4()),
        service_mode="controlled_compute",
        policy={},
        is_demo=True,
        demo_price_plan_eligible=True,
    )
    model = resolve_offer_snapshot(
        product_kind="model",
        version_id=str(uuid4()),
        service_mode="model_artifact_license",
        policy={},
        is_demo=True,
        demo_price_plan_eligible=True,
    )
    public_license = resolve_offer_snapshot(
        product_kind="data",
        version_id=str(uuid4()),
        service_mode="deidentified_data_delivery",
        policy={},
        is_demo=True,
        demo_price_plan_eligible=True,
    )
    assert data["unit_amount_minor"] == 4_900
    assert model["unit_amount_minor"] == 1_980_000
    assert public_license["unit_amount_minor"] == 0
    assert data["currency"] == "CNY"
    assert data["platform_fee_rate_bps"] == 500
    assert data["includes_platform_fee"] is True
    assert data["pricing_source"] == "versioned_demo_price_plan"
    assert data["pricing_plan_version"] == "medtrust-demo-price-plan/2026-08-29-v2"


def test_offer_projection_hides_platform_economics_from_market_roles() -> None:
    offer = resolve_offer_snapshot(
        product_kind="data",
        version_id=str(uuid4()),
        service_mode="controlled_compute",
        policy={},
        is_demo=True,
        demo_price_plan_eligible=True,
    )

    operator = commercial_offer_payload_for_role(offer, role="space_operator")
    assert operator["unit_amount_minor"] == 4_900
    assert operator["platform_fee_rate_bps"] == 500
    assert operator["channel_fee_rate_bps"] == 60
    assert operator["pricing_plan_version"] == "medtrust-demo-price-plan/2026-08-29-v2"

    for role in ("data_requester", "data_provider", "model_provider"):
        projected = commercial_offer_payload_for_role(offer, role=role)
        assert projected["unit_amount_minor"] == 4_900
        assert projected["service_mode"] == "controlled_compute"
        for internal_field in (
            "platform_fee_rate_bps",
            "provider_share_rate_bps",
            "channel_fee_rate_bps",
            "includes_platform_fee",
            "pricing_source",
            "pricing_plan_version",
            "revenue_basis",
        ):
            assert internal_field not in projected


def test_unlisted_demo_product_cannot_use_the_frozen_price_plan() -> None:
    with pytest.raises(ValueError, match="no published commercial offer"):
        resolve_offer_snapshot(
            product_kind="data",
            version_id=str(uuid4()),
            service_mode="controlled_compute",
            policy={},
            is_demo=True,
            demo_price_plan_eligible=False,
        )


def test_compute_checkout_requires_a_current_passing_contract_security_decision() -> None:
    require_contract_checkout_security({"overall": "PASS"})
    for overall in ("BLOCKER", "WARNING", None):
        with pytest.raises(CommerceError, match="安全合约验证未全部通过"):
            require_contract_checkout_security({"overall": overall})


def test_nested_product_policy_offer_is_used_and_labels_are_preserved() -> None:
    offer = resolve_offer_snapshot(
        product_kind="model",
        version_id=str(uuid4()),
        service_mode="controlled_compute",
        policy={
            "commercial_offer": {
                "currency": "CNY",
                "platform_fee_rate_bps": 500,
                "pricing_notice": "演示报价",
                "offerings": {
                    "controlled_compute": {
                        "unit_amount_minor": 39_900,
                        "unit_label": "每 200 张受控推理",
                        "price_label": "¥399/200 张",
                        "revenue_basis": "固定模型受控推理与验证服务",
                    }
                },
            }
        },
        is_demo=True,
    )
    assert offer["pricing_source"] == "product_policy"
    assert offer["unit_amount_minor"] == 39_900
    assert offer["unit_label"] == "每 200 张受控推理"
    assert offer["price_label"] == "¥399/200 张"
    assert offer["revenue_basis"] == "固定模型受控推理与验证服务"
    assert offer["pricing_notice"] == "演示报价"


def _detached_order() -> CommercialOrder:
    now = datetime.now(timezone.utc)
    order_id = uuid4()
    order = CommercialOrder(
        id=order_id,
        space_id=uuid4(),
        order_number="MTO-UNIT",
        requester_organization_id=uuid4(),
        requester_user_id=uuid4(),
        source_type="service_access",
        source_id=uuid4(),
        service_access_request_id=uuid4(),
        status="agreement_pending",
        currency="CNY",
        gross_amount_minor=68_000,
        platform_fee_rate_bps=500,
        platform_fee_minor=3_400,
        provider_net_minor=64_600,
        quote_snapshot={"amount": 68_000},
        quote_digest="sha256:" + "1" * 64,
        agreement_snapshot={"terms": "unit"},
        agreement_digest="sha256:" + "2" * 64,
        create_idempotency_digest="sha256:" + "3" * 64,
        created_at=now,
        updated_at=now,
        row_version=1,
    )
    # Keep the generic source and concrete source aligned after constructing.
    order.service_access_request_id = order.source_id
    make_transient_to_detached(order)
    return order


def test_commercial_order_state_machine_requires_validated_transition() -> None:
    session = Session()
    order = _detached_order()
    session.add(order)
    order.status = "awaiting_payment"
    order.agreement_accepted_at = datetime.now(timezone.utc)
    order.agreement_accepted_by = uuid4()
    order.agreement_idempotency_digest = "sha256:" + "4" * 64
    order.row_version = 2
    with pytest.raises(CommerceInvariantError, match="illegal"):
        guard_commerce_mutations(session, None, None)
    order._transition_validated = True
    guard_commerce_mutations(session, None, None)
    order.quote_snapshot = {"amount": 1}
    with pytest.raises(CommerceInvariantError, match="immutable"):
        guard_commerce_mutations(session, None, None)
    session.close()


def test_requester_cannot_read_another_organizations_order() -> None:
    order = _detached_order()
    actor = SimpleNamespace(
        role="data_requester", organization_id=uuid4(), user_id=uuid4()
    )
    with pytest.raises(CommerceError, match="another requester"):
        asyncio.run(
            commercial_order_payload(None, order=order, actor=actor)  # type: ignore[arg-type]
        )


def test_order_payload_projects_amounts_by_actor_role() -> None:
    now = datetime.now(timezone.utc)
    requester_organization_id = uuid4()
    order = SimpleNamespace(
        id=uuid4(),
        order_number="MTO-ROLE-PROJECTION",
        space_id=uuid4(),
        requester_organization_id=requester_organization_id,
        status="paid",
        source_type="service_access",
        source_id=uuid4(),
        contract_id=None,
        service_access_request_id=uuid4(),
        currency="CNY",
        quote_digest="sha256:" + "1" * 64,
        agreement_digest="sha256:" + "2" * 64,
        agreement_snapshot={"terms": "unit", "platform_fee_included": True},
        agreement_accepted_at=now,
        gross_amount_minor=68_000,
        platform_fee_rate_bps=500,
        platform_fee_minor=3_400,
        provider_net_minor=64_600,
        created_at=now,
        updated_at=now,
    )
    provider_id = uuid4()
    line_offer = resolve_offer_snapshot(
        product_kind="data",
        version_id=str(uuid4()),
        service_mode="controlled_compute",
        policy={},
        is_demo=True,
        demo_price_plan_eligible=True,
    )
    line = SimpleNamespace(
        id=uuid4(),
        line_no=1,
        provider_organization_id=provider_id,
        product_kind="data",
        product_id=uuid4(),
        version_id=uuid4(),
        product_name="Public data controlled compute",
        service_mode="controlled_compute",
        currency="CNY",
        unit_amount_minor=68_000,
        gross_amount_minor=68_000,
        platform_fee_minor=3_400,
        provider_net_minor=64_600,
        offer_snapshot=line_offer,
    )
    payment = SimpleNamespace(
        id=uuid4(),
        method="alipay_demo",
        status="succeeded",
        transaction_number="DEMO-ROLE-PROJECTION",
        currency="CNY",
        amount_minor=68_000,
        channel_fee_rate_bps=60,
        channel_fee_minor=408,
        paid_at=datetime.now(timezone.utc),
    )
    provider = SimpleNamespace(id=provider_id, display_name="Provider")

    class ScalarRows:
        def __init__(self, rows: list[object]) -> None:
            self.rows = rows

        def all(self) -> list[object]:
            return self.rows

    class FakeSession:
        def __init__(self) -> None:
            self.row_sets = iter(([line], [], [provider]))

        async def scalars(self, _statement: object) -> ScalarRows:
            return ScalarRows(list(next(self.row_sets)))

        async def scalar(self, _statement: object) -> object:
            return payment

    async def payload(role: str, organization_id: object) -> dict[str, object]:
        return await commercial_order_payload(
            FakeSession(),  # type: ignore[arg-type]
            order=order,
            actor=SimpleNamespace(
                role=role,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
        )

    requester = asyncio.run(payload("data_requester", requester_organization_id))
    assert requester["gross_amount_minor"] == 68_000
    assert "platform_fee_minor" not in requester
    assert "provider_net_minor" not in requester
    assert "platform_fee_minor" not in requester["lines"][0]  # type: ignore[index]
    assert "provider_net_minor" not in requester["lines"][0]  # type: ignore[index]
    assert "channel_fee_minor" not in requester["payment"]  # type: ignore[operator]
    assert "platform_fee_included" not in requester["agreement"]["snapshot"]  # type: ignore[index]

    provider_payload = asyncio.run(payload("data_provider", provider_id))
    assert provider_payload["subtotal_amount_minor"] == 68_000
    assert provider_payload["provider_net_minor"] == 64_600
    assert "platform_fee_minor" not in provider_payload
    assert provider_payload["lines"][0]["provider_net_minor"] == 64_600  # type: ignore[index]
    assert "platform_fee_minor" not in provider_payload["lines"][0]  # type: ignore[index]
    assert "method" not in provider_payload["payment"]  # type: ignore[operator]

    operator = asyncio.run(payload("space_operator", uuid4()))
    assert operator["platform_fee_minor"] == 3_400
    assert operator["provider_net_minor"] == 64_600
    assert operator["lines"][0]["platform_fee_minor"] == 3_400  # type: ignore[index]
    assert operator["payment"]["channel_fee_minor"] == 408  # type: ignore[index]
    assert operator["agreement"]["snapshot"]["platform_fee_included"] is True  # type: ignore[index]


def test_provider_settlement_projection_hides_platform_costs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_id = uuid4()
    line = SimpleNamespace(
        provider_organization_id=provider_id,
        gross_amount_minor=68_000,
        platform_fee_minor=3_400,
        provider_net_minor=64_600,
    )
    order = SimpleNamespace(id=uuid4())
    organization = SimpleNamespace(id=provider_id, display_name="Provider")

    async def allow_actor(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "app.modules.commerce.services._require_actor",
        allow_actor,
    )

    class Rows:
        def __init__(self, rows: list[object]) -> None:
            self.rows = rows

        def all(self) -> list[object]:
            return self.rows

    class FakeSession:
        async def execute(self, _statement: object) -> Rows:
            return Rows([(line, order)])

        async def scalars(self, _statement: object) -> Rows:
            return Rows([organization])

    result = asyncio.run(
        provider_settlements(
            FakeSession(),  # type: ignore[arg-type]
            space_id=uuid4(),
            actor=SimpleNamespace(
                role="data_provider",
                organization_id=provider_id,
                user_id=uuid4(),
            ),
        )
    )
    assert result["summary"]["gross_amount_minor"] == 68_000
    assert result["summary"]["provider_net_minor"] == 64_600
    assert "platform_fee_minor" not in result["summary"]
    assert "channel_fee_minor" not in result["summary"]
    assert "platform_fee_minor" not in result["items"][0]


def test_download_grant_token_is_idempotent_for_same_command() -> None:
    grant_id = uuid4()
    first = _grant_token(grant_id=grant_id, raw_key="idem-command-001")
    replay = _grant_token(grant_id=grant_id, raw_key="idem-command-001")
    different = _grant_token(grant_id=grant_id, raw_key="idem-command-002")
    assert first == replay
    assert first != different
    assert first.startswith("mtg_")
    unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in CommercialDownloadGrant.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("fulfillment_id",) in unique_sets


@pytest.mark.parametrize(
    ("kind", "version_id", "product_kind", "forbidden_suffixes"),
    [
        (
            "data_document_package",
            PATHMNIST_DATA_DELIVERY_VERSION_ID,
            "data",
            (".csv", ".parquet", ".dcm"),
        ),
        (
            "model_license_package",
            PATHMNIST_MODEL_LICENSE_VERSION_ID,
            "model",
            (".pt", ".pth", ".onnx", ".bin"),
        ),
    ],
)
def test_safe_delivery_packages_contain_no_records_or_model_weights(
    kind: str,
    version_id: str,
    product_kind: str,
    forbidden_suffixes: tuple[str, ...],
) -> None:
    filename, payload = build_delivery_zip(
        kind=kind,
        entitlement_snapshot={
            "order_number": "MTO-UNIT",
            "kind": kind,
            "entitled_products": [
                {
                    "product_kind": product_kind,
                    "product_id": str(uuid4()),
                    "product_name": "fixed product",
                    "version_id": version_id,
                    "provider_organization_id": str(uuid4()),
                    "service_mode": (
                        "deidentified_data_delivery"
                        if product_kind == "data"
                        else "model_artifact_license"
                    ),
                    "offer_digest": "sha256:" + "a" * 64,
                }
            ],
        },
    )
    assert filename.endswith(".zip")
    with ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        assert all(not name.lower().endswith(forbidden_suffixes) for name in names)
        combined = b"\n".join(archive.read(name) for name in names).lower()
    if kind == "model_license_package":
        assert b'"weights_included": false' in combined
        assert b"no model weights" in combined
    else:
        assert b'"patient_data_included": false' in combined
        assert b"no patient-level records" in combined
        assert b"cc by 4.0" in combined
        assert b"https://zenodo.org/records/10519652" in combined
    assert version_id.encode("ascii") in combined


def test_unregistered_product_version_cannot_receive_an_automatic_package() -> None:
    with pytest.raises(CommercialPackageError, match="not registered"):
        build_delivery_zip(
            kind="data_document_package",
            entitlement_snapshot={
                "order_number": "MTO-UNIT",
                "entitled_products": [
                    {
                        "product_kind": "data",
                        "version_id": str(uuid4()),
                    }
                ],
            },
        )


def test_commercial_audit_vocabulary_is_registered() -> None:
    expected = {
        "commercial.order.created": ("commercial_order", "success"),
        "commercial.agreement.accepted": ("commercial_order", "success"),
        "commercial.payment.succeeded": ("commercial_payment", "success"),
        "commercial.fulfillment.created": ("commercial_fulfillment", "success"),
        "commercial.download.grant.created": (
            "commercial_download_grant",
            "success",
        ),
        "commercial.download.completed": (
            "commercial_download_grant",
            "success",
        ),
    }
    assert {name: EVENT_SHAPES[name] for name in expected} == expected
    assert expected.keys() <= set(AUDIT_EVENT_TYPES)
    assert {
        "commercial_order",
        "commercial_payment",
        "commercial_fulfillment",
        "commercial_download_grant",
    } <= set(AUDIT_SUBJECT_TYPES)


def test_commercial_execution_gate_preserves_legacy_and_blocks_unpaid_orders() -> None:
    assert commercial_execution_decision(
        commerce_required=False,
        order_exists=False,
        order_status=None,
        entitlement_ready=False,
    )["required"] is False
    with pytest.raises(CommercialExecutionBlocked, match="checkout is required"):
        commercial_execution_decision(
            commerce_required=True,
            order_exists=False,
            order_status=None,
            entitlement_ready=False,
        )
    for status in ("agreement_pending", "awaiting_payment"):
        with pytest.raises(CommercialExecutionBlocked, match="must be paid"):
            commercial_execution_decision(
                commerce_required=True,
                order_exists=True,
                order_status=status,
                entitlement_ready=False,
            )
    with pytest.raises(CommercialExecutionBlocked, match="no ready"):
        commercial_execution_decision(
            commerce_required=True,
            order_exists=True,
            order_status="paid",
            entitlement_ready=False,
        )
    decision = commercial_execution_decision(
        commerce_required=True,
        order_exists=True,
        order_status="paid",
        entitlement_ready=True,
    )
    assert decision == {
        "required": True,
        "decision": "paid_entitlement_ready",
        "reason": None,
    }


def test_partial_commercial_bundle_is_an_explicit_configuration_error() -> None:
    data_version_id = uuid4()
    model_version_id = uuid4()
    partial = commercial_bundle_requirement(
        data_version_ids=[data_version_id],
        model_version_ids=[model_version_id],
        matches=[
            {
                "product_kind": "data",
                "selected_version_id": str(data_version_id),
                "pricing_version_id": str(data_version_id),
                "match_type": "selected_version_offer",
            }
        ],
    )
    assert partial["required"] is False
    assert partial["decision"] == "incomplete_commercial_bundle"
    assert "exactly one selected data" in partial["configuration_error"]


def test_execution_entitlement_service_blocks_required_no_order_and_allows_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(self, scalar_values: list[object | None]) -> None:
            self.scalar_values = iter(scalar_values)

        async def scalar(self, _statement: object) -> object | None:
            return next(self.scalar_values)

    async def required_requirement(_session: object, *, contract_id: object):
        return {
            "required": True,
            "decision": "complete_published_commercial_bundle",
            "matches": [],
            "configuration_error": None,
        }

    monkeypatch.setattr(
        "app.modules.commerce.gating.contract_commerce_requirement",
        required_requirement,
    )
    with pytest.raises(CommercialExecutionBlocked, match="checkout is required"):
        asyncio.run(
            require_paid_execution_entitlement(
                FakeSession([None]), contract_id=uuid4()  # type: ignore[arg-type]
            )
        )

    order = SimpleNamespace(
        id=uuid4(),
        order_number="MTO-PAID",
        status="paid",
        quote_digest="sha256:" + "1" * 64,
        quote_snapshot={"lines": []},
    )
    fulfillment = SimpleNamespace(
        id=uuid4(), entitlement_digest="sha256:" + "2" * 64
    )
    result = asyncio.run(
        require_paid_execution_entitlement(
            FakeSession([order, fulfillment]),  # type: ignore[arg-type]
            contract_id=uuid4(),
        )
    )
    assert result["decision"] == "paid_entitlement_ready"
    assert result["entitlement_digest"] == fulfillment.entitlement_digest


def test_fixed_execution_order_issuance_calls_commercial_gate_and_blocks_unpaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    space_id = uuid4()
    bundle_id = uuid4()
    contract_id = uuid4()
    version_id = uuid4()
    bundle = SimpleNamespace(
        id=bundle_id,
        space_id=space_id,
        status="active",
        current_version_id=version_id,
        connector_id=uuid4(),
        control_readiness_id=uuid4(),
        contract_id=contract_id,
    )
    version = SimpleNamespace(
        signature="signed",
        signing_key_id="unit-key",
        execution_authorized=True,
    )
    signing_key = SimpleNamespace(
        status="active",
        valid_to=now.replace(year=now.year + 1),
    )

    class FakeSession:
        def __init__(self) -> None:
            self.scalar_values = iter((None, signing_key, 0))

        async def scalar(self, _statement: object) -> object | None:
            return next(self.scalar_values)

        async def get(
            self, model: type[object], object_id: object, **_kwargs: object
        ) -> object | None:
            if model.__name__ == "PolicyBundle" and object_id == bundle_id:
                return bundle
            if model.__name__ == "PolicyBundleVersion" and object_id == version_id:
                return version
            return None

    observed_contract_ids: list[object] = []

    async def block_unpaid(_session: object, *, contract_id: object) -> None:
        observed_contract_ids.append(contract_id)
        raise CommercialExecutionBlocked(
            "commercial checkout must be paid before controlled execution"
        )

    monkeypatch.setattr(
        "app.modules.commerce.gating.require_paid_execution_entitlement",
        block_unpaid,
    )
    with pytest.raises(
        PolicyControlError, match="COMMERCIAL_EXECUTION_BLOCKED:.*must be paid"
    ):
        asyncio.run(
            issue_order(
                FakeSession(),  # type: ignore[arg-type]
                actor=SimpleNamespace(role="space_operator"),
                space_id=space_id,
                bundle_id=bundle_id,
                idempotency_key="unit-fixed-order-unpaid",
            )
        )
    assert observed_contract_ids == [contract_id]


def test_execution_consumption_calls_commercial_gate_and_blocks_unpaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    order_id = uuid4()
    connector_id = uuid4()
    bundle_id = uuid4()
    contract_id = uuid4()
    order_digest = "sha256:" + "d" * 64
    payload = {
        "schema_version": "phase5.13E-2C-R1/execution-consumption/v1",
        "consumption_receipt_id": str(uuid4()),
        "execution_order_id": str(order_id),
        "execution_order_digest": order_digest,
        "authorization_snapshot_id": str(uuid4()),
        "authorization_snapshot_digest": "sha256:" + "1" * 64,
        "task_manifest_id": str(uuid4()),
        "task_manifest_digest": "sha256:" + "2" * 64,
        "runtime_session_id": str(uuid4()),
        "runtime_digest": "sha256:" + "3" * 64,
        "reference_execution_id": str(uuid4()),
        "request_digest": "sha256:" + "4" * 64,
        "consumed_at": now.isoformat(),
        "remaining_validity_seconds": 300,
        "local_audit_head": None,
        "execution_started": False,
        "hard_isolation": False,
    }
    order = SimpleNamespace(
        id=order_id,
        connector_id=connector_id,
        execution_authorized=True,
        order_mode="FIXED_REFERENCE_EXECUTION",
        status="accepted",
        payload_digest=order_digest,
        not_before=now.replace(year=now.year - 1),
        expires_at=now.replace(year=now.year + 1),
        policy_bundle_id=bundle_id,
    )
    bundle = SimpleNamespace(id=bundle_id, contract_id=contract_id)

    class FakeSession:
        async def get(
            self, model: type[object], object_id: object, **_kwargs: object
        ) -> object | None:
            if model.__name__ == "ExecutionOrder" and object_id == order_id:
                return order
            if model.__name__ == "PolicyBundle" and object_id == bundle_id:
                return bundle
            return None

    observed_contract_ids: list[object] = []

    async def block_unpaid(_session: object, *, contract_id: object) -> None:
        observed_contract_ids.append(contract_id)
        raise CommercialExecutionBlocked(
            "commercial checkout must be paid before controlled execution"
        )

    monkeypatch.setattr(
        "app.modules.commerce.gating.require_paid_execution_entitlement",
        block_unpaid,
    )
    with pytest.raises(
        PolicyControlError, match="COMMERCIAL_EXECUTION_BLOCKED:.*must be paid"
    ):
        asyncio.run(
            accept_execution_consumption(
                FakeSession(),  # type: ignore[arg-type]
                connector=SimpleNamespace(id=connector_id),
                payload=payload,
                digest=canonical_json_digest_v1(payload),
                signature="not-reached",
            )
        )
    assert observed_contract_ids == [contract_id]
