from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.applications.models import ApplicationItem
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
    DataResource,
)
from app.modules.contracts.models import Contract
from .models import CommercialFulfillment, CommercialOrder
from .pricing import demo_price_plan_eligible, has_commercial_offer


class CommercialExecutionBlocked(ValueError):
    pass


async def _published_data_product_code(
    session: AsyncSession, *, version: DataProductVersion
) -> str | None:
    return await session.scalar(
            select(DataProduct.product_code)
            .join(
                DataProductPublication,
                (DataProductPublication.data_product_id == DataProduct.id)
                & (
                    DataProductPublication.data_product_version_id
                    == version.id
                ),
            )
            .where(
                DataProduct.id == version.data_product_id,
                DataProduct.space_id == version.space_id,
                DataProductPublication.status == "active",
                DataProduct.lifecycle_status == "active",
            )
        )


async def _published_model_product_code(
    session: AsyncSession, *, version: Any
) -> str | None:
    from app.modules.marketplace.models import ModelProduct, ModelPublication

    return await session.scalar(
            select(ModelProduct.product_code)
            .join(
                ModelPublication,
                (ModelPublication.model_product_id == ModelProduct.id)
                & (ModelPublication.model_version_id == version.id),
            )
            .where(
                ModelProduct.id == version.model_product_id,
                ModelProduct.space_id == version.space_id,
                ModelPublication.status == "active",
                ModelProduct.lifecycle_status == "active",
            )
        )


def commercial_execution_decision(
    *,
    commerce_required: bool,
    order_exists: bool,
    order_status: str | None,
    entitlement_ready: bool,
) -> dict[str, Any]:
    if not order_exists:
        if commerce_required:
            raise CommercialExecutionBlocked(
                "commercial checkout is required before controlled execution"
            )
        return {
            "required": False,
            "decision": "legacy_contract_not_commercialized",
            "reason": None,
        }
    if order_status != "paid":
        raise CommercialExecutionBlocked(
            "commercial checkout must be paid before controlled execution"
        )
    if not entitlement_ready:
        raise CommercialExecutionBlocked(
            "paid commercial order has no ready execution entitlement"
        )
    return {"required": True, "decision": "paid_entitlement_ready", "reason": None}


async def _matching_commercial_data_version(
    session: AsyncSession, *, version: DataProductVersion
) -> UUID | None:
    resource_digests = set(
        (
            await session.scalars(
                select(DataResource.resource_digest).where(
                    DataResource.data_product_version_id == version.id
                )
            )
        ).all()
    )
    if not resource_digests:
        return None
    candidates = list(
        (
            await session.execute(
                select(DataProductVersion, DataProduct.product_code)
                .join(
                    DataProduct,
                    (DataProduct.id == DataProductVersion.data_product_id)
                    & (DataProduct.space_id == DataProductVersion.space_id),
                )
                .join(
                    DataProductPublication,
                    DataProductPublication.data_product_version_id
                    == DataProductVersion.id,
                )
                .join(
                    DataResource,
                    DataResource.data_product_version_id == DataProductVersion.id,
                )
                .where(
                    DataProductVersion.space_id == version.space_id,
                    DataProductVersion.id != version.id,
                    DataProductVersion.status == "approved",
                    DataProduct.lifecycle_status == "active",
                    DataProductPublication.status == "active",
                    DataResource.resource_digest.in_(resource_digests),
                )
                .distinct()
            )
        ).all()
    )
    return next(
        (
            candidate.id
            for candidate, product_code in candidates
            if demo_price_plan_eligible(
                product_kind="data", product_code=product_code
            )
            or has_commercial_offer(
                    candidate.default_policy_template, "controlled_compute"
                )
        ),
        None,
    )


async def _matching_commercial_model_version(
    session: AsyncSession, *, version: Any
) -> UUID | None:
    from app.modules.marketplace.models import (
        ModelProduct,
        ModelPublication,
        ModelVersion,
    )

    candidates = list(
        (
            await session.execute(
                select(ModelVersion, ModelProduct.product_code)
                .join(
                    ModelProduct,
                    (ModelProduct.id == ModelVersion.model_product_id)
                    & (ModelProduct.space_id == ModelVersion.space_id),
                )
                .join(
                    ModelPublication,
                    ModelPublication.model_version_id == ModelVersion.id,
                )
                .where(
                    ModelVersion.space_id == version.space_id,
                    ModelVersion.id != version.id,
                    ModelVersion.model_digest == version.model_digest,
                    ModelVersion.status == "approved",
                    ModelProduct.lifecycle_status == "active",
                    ModelPublication.status == "active",
                )
            )
        ).all()
    )
    return next(
        (
            candidate.id
            for candidate, product_code in candidates
            if demo_price_plan_eligible(
                product_kind="model", product_code=product_code
            )
            or has_commercial_offer(
                    candidate.default_policy_template, "controlled_compute"
                )
        ),
        None,
    )


def commercial_bundle_requirement(
    *,
    data_version_ids: list[UUID],
    model_version_ids: list[UUID],
    matches: list[dict[str, str]],
) -> dict[str, Any]:
    selected_count = len(data_version_ids) + len(model_version_ids)
    if not matches:
        decision = "legacy_contract_without_commercial_offer"
        required = False
        configuration_error = None
    elif (
        len(data_version_ids) == 1
        and len(model_version_ids) == 1
        and len(matches) == selected_count
    ):
        decision = "complete_published_commercial_bundle"
        required = True
        configuration_error = None
    else:
        decision = "incomplete_commercial_bundle"
        required = False
        configuration_error = (
            "commercial checkout configuration is incomplete: controlled-compute "
            "pricing must resolve for exactly one selected data version and one "
            "selected model version"
        )
    return {
        "required": required,
        "decision": decision,
        "selected_data_version_ids": [str(item) for item in data_version_ids],
        "selected_model_version_ids": [str(item) for item in model_version_ids],
        "matches": matches,
        "configuration_error": configuration_error,
    }


async def contract_commerce_requirement(
    session: AsyncSession, *, contract_id: UUID
) -> dict[str, Any]:
    """Resolve payment applicability from authoritative selected product versions."""

    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise CommercialExecutionBlocked("commercial contract context is unavailable")
    data_version_ids = list(
        (
            await session.scalars(
                select(ApplicationItem.data_product_version_id).where(
                    ApplicationItem.application_id == contract.application_id
                )
            )
        ).all()
    )
    from app.modules.marketplace.models import (
        ApplicationModelSelection,
        ModelVersion,
    )

    model_version_ids = list(
        (
            await session.scalars(
                select(ApplicationModelSelection.model_version_id).where(
                    ApplicationModelSelection.application_id == contract.application_id
                )
            )
        ).all()
    )
    matches: list[dict[str, str]] = []
    for version_id in data_version_ids:
        version = await session.get(DataProductVersion, version_id)
        if version is None:
            continue
        product_code = await _published_data_product_code(session, version=version)
        if product_code is not None and (
            demo_price_plan_eligible(
                product_kind="data", product_code=product_code
            )
            or has_commercial_offer(
                version.default_policy_template, "controlled_compute"
            )
        ):
            matches.append(
                {
                    "product_kind": "data",
                    "selected_version_id": str(version.id),
                    "pricing_version_id": str(version.id),
                    "match_type": "selected_version_offer",
                }
            )
            continue
        matching_id = await _matching_commercial_data_version(session, version=version)
        if matching_id is not None:
            matches.append(
                {
                    "product_kind": "data",
                    "selected_version_id": str(version.id),
                    "pricing_version_id": str(matching_id),
                    "match_type": "matching_published_asset_offer",
                }
            )
    for version_id in model_version_ids:
        version = await session.get(ModelVersion, version_id)
        if version is None:
            continue
        product_code = await _published_model_product_code(session, version=version)
        if product_code is not None and (
            demo_price_plan_eligible(
                product_kind="model", product_code=product_code
            )
            or has_commercial_offer(
                version.default_policy_template, "controlled_compute"
            )
        ):
            matches.append(
                {
                    "product_kind": "model",
                    "selected_version_id": str(version.id),
                    "pricing_version_id": str(version.id),
                    "match_type": "selected_version_offer",
                }
            )
            continue
        matching_id = await _matching_commercial_model_version(session, version=version)
        if matching_id is not None:
            matches.append(
                {
                    "product_kind": "model",
                    "selected_version_id": str(version.id),
                    "pricing_version_id": str(matching_id),
                    "match_type": "matching_published_asset_offer",
                }
            )
    return commercial_bundle_requirement(
        data_version_ids=data_version_ids,
        model_version_ids=model_version_ids,
        matches=matches,
    )


async def require_paid_execution_entitlement(
    session: AsyncSession, *, contract_id: UUID
) -> dict[str, Any]:
    order = await session.scalar(
        select(CommercialOrder).where(
            CommercialOrder.source_type == "contract",
            CommercialOrder.source_id == contract_id,
        )
    )
    if order is None:
        requirement = await contract_commerce_requirement(
            session, contract_id=contract_id
        )
        if requirement["configuration_error"] is not None:
            raise CommercialExecutionBlocked(requirement["configuration_error"])
        decision = commercial_execution_decision(
            commerce_required=requirement["required"],
            order_exists=False,
            order_status=None,
            entitlement_ready=False,
        )
        return {**decision, "requirement": requirement}
    requirement = {
        "required": True,
        "decision": "existing_commercial_order",
        "matches": [
            {
                "product_kind": line.get("product_kind"),
                "pricing_version_id": line.get("version_id"),
                "match_type": line.get("offer_snapshot", {}).get(
                    "contract_asset_match_type", "order_quote_snapshot"
                ),
            }
            for line in order.quote_snapshot.get("lines", [])
            if isinstance(line, dict)
        ],
        "configuration_error": None,
    }
    fulfillment = await session.scalar(
        select(CommercialFulfillment).where(
            CommercialFulfillment.order_id == order.id,
            CommercialFulfillment.kind == "execution_entitlement",
            CommercialFulfillment.status == "ready",
            CommercialFulfillment.contract_id == contract_id,
        )
    )
    decision = commercial_execution_decision(
        commerce_required=requirement["required"],
        order_exists=True,
        order_status=order.status,
        entitlement_ready=fulfillment is not None,
    )
    return {
        **decision,
        "order_id": str(order.id),
        "order_number": order.order_number,
        "order_status": order.status,
        "quote_digest": order.quote_digest,
        "fulfillment_id": str(fulfillment.id) if fulfillment else None,
        "entitlement_digest": fulfillment.entitlement_digest if fulfillment else None,
        "requirement": requirement,
    }
