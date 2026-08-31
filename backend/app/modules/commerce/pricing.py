from __future__ import annotations

from typing import Any, Mapping


CURRENCY = "CNY"
PLATFORM_FEE_RATE_BPS = 500
PROVIDER_SHARE_RATE_BPS = 9500
CHANNEL_FEE_RATE_BPS = 60
FROZEN_DEMO_PRICE_PLAN_VERSION = "medtrust-demo-price-plan/2026-08-29-v2"
FROZEN_DEMO_PRICE_PLAN_PRODUCTS = {
    ("data", "PATHMNIST-SERVICE-MARKET-DATA-V1"),
    ("model", "PATHMNIST-SERVICE-MARKET-MODEL-V1"),
}

FROZEN_DEMO_PRICES_MINOR = {
    ("data", "deidentified_data_delivery"): 0,
    ("data", "controlled_compute"): 4_900,
    ("model", "controlled_compute"): 39_900,
    ("model", "model_artifact_license"): 1_980_000,
}

OFFER_LABELS = {
    ("data", "deidentified_data_delivery"): "公开数据授权资料包",
    ("data", "controlled_compute"): "数据受控计算治理服务",
    ("model", "controlled_compute"): "模型受控调用服务",
    ("model", "model_artifact_license"): "模型成果使用许可",
}

FULFILLMENT_TYPES = {
    ("data", "deidentified_data_delivery"): "data_document_package",
    ("data", "controlled_compute"): "execution_entitlement",
    ("model", "controlled_compute"): "execution_entitlement",
    ("model", "model_artifact_license"): "model_license_package",
}


class CommercialPricingError(ValueError):
    pass


def round_basis_points(amount_minor: int, rate_bps: int) -> int:
    """Round half-up to one currency minor unit without using floats."""

    if amount_minor < 0 or not 0 <= rate_bps <= 10_000:
        raise CommercialPricingError("amount and basis-point rate are invalid")
    return (amount_minor * rate_bps + 5_000) // 10_000


def split_gross_amount(amount_minor: int) -> tuple[int, int]:
    platform_fee = round_basis_points(amount_minor, PLATFORM_FEE_RATE_BPS)
    return platform_fee, amount_minor - platform_fee


def channel_fee_for(amount_minor: int) -> int:
    return round_basis_points(amount_minor, CHANNEL_FEE_RATE_BPS)


def _mode_offer(policy: Mapping[str, Any], service_mode: str) -> Mapping[str, Any] | None:
    commercial = policy.get("commercial_offer")
    if not isinstance(commercial, Mapping):
        return None
    candidates = (
        commercial.get(service_mode),
        (
            commercial.get("offerings", {}).get(service_mode)
            if isinstance(commercial.get("offerings"), Mapping)
            else None
        ),
        (
            commercial.get("service_modes", {}).get(service_mode)
            if isinstance(commercial.get("service_modes"), Mapping)
            else None
        ),
        commercial if commercial.get("service_mode") == service_mode else None,
    )
    return next((item for item in candidates if isinstance(item, Mapping)), None)


def has_commercial_offer(
    policy: Mapping[str, Any] | None, service_mode: str
) -> bool:
    """Return whether a product explicitly publishes an offer for this mode.

    Demo fallback prices are deliberately excluded: they may price an order once
    checkout starts, but they must not silently turn every legacy contract into a
    paid contract.
    """

    return isinstance(policy, Mapping) and _mode_offer(policy, service_mode) is not None


def demo_price_plan_eligible(*, product_kind: str, product_code: str) -> bool:
    return (product_kind, product_code) in FROZEN_DEMO_PRICE_PLAN_PRODUCTS


def resolve_offer_snapshot(
    *,
    product_kind: str,
    version_id: str,
    service_mode: str,
    policy: Mapping[str, Any] | None,
    is_demo: bool,
    demo_price_plan_eligible: bool = False,
) -> dict[str, Any]:
    key = (product_kind, service_mode)
    if key not in FROZEN_DEMO_PRICES_MINOR:
        raise CommercialPricingError("unsupported commercial service mode")
    policy = policy or {}
    commercial = policy.get("commercial_offer")
    commercial_root = commercial if isinstance(commercial, Mapping) else {}
    configured = _mode_offer(policy, service_mode)
    if configured is None:
        if not (is_demo and demo_price_plan_eligible):
            raise CommercialPricingError(
                "this product version has no published commercial offer"
            )
        amount = FROZEN_DEMO_PRICES_MINOR[key]
        source = "versioned_demo_price_plan"
        pricing_plan_version = FROZEN_DEMO_PRICE_PLAN_VERSION
        validity_days = 365 if service_mode != "controlled_compute" else None
    else:
        raw_amount = configured.get(
            "unit_amount_minor",
            configured.get("amount_minor", configured.get("list_price_minor")),
        )
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
            raise CommercialPricingError(
                "commercial_offer unit_amount_minor must be an integer"
            )
        if raw_amount < 0:
            raise CommercialPricingError("commercial offer cannot be negative")
        currency = configured.get(
            "currency", commercial_root.get("currency", CURRENCY)
        )
        if currency != CURRENCY:
            raise CommercialPricingError("only CNY commercial offers are supported")
        amount = raw_amount
        source = "product_policy"
        pricing_plan_version = str(
            commercial_root.get(
                "price_plan_version",
                commercial_root.get(
                    "schema_version", "product-policy-commercial-offer/v1"
                ),
            )
        )
        validity_days = configured.get("validity_days")
        if validity_days is not None and (
            isinstance(validity_days, bool)
            or not isinstance(validity_days, int)
            or not 1 <= validity_days <= 3650
        ):
            raise CommercialPricingError("commercial offer validity_days is invalid")

        configured_rate = commercial_root.get(
            "platform_fee_rate_bps", PLATFORM_FEE_RATE_BPS
        )
        if configured_rate != PLATFORM_FEE_RATE_BPS:
            raise CommercialPricingError(
                "commercial offer platform fee must match the frozen 500 bps rate"
            )

    result = {
        "schema_version": "medtrust.commercial-offer/v1",
        "product_kind": product_kind,
        "version_id": version_id,
        "service_mode": service_mode,
        "label": OFFER_LABELS[key],
        "currency": CURRENCY,
        "unit_amount_minor": amount,
        "quantity": 1,
        "platform_fee_rate_bps": PLATFORM_FEE_RATE_BPS,
        "provider_share_rate_bps": PROVIDER_SHARE_RATE_BPS,
        "includes_platform_fee": True,
        "channel_fee_rate_bps": CHANNEL_FEE_RATE_BPS,
        "fulfillment_type": FULFILLMENT_TYPES[key],
        "validity_days": validity_days,
        "pricing_source": source,
        "pricing_plan_version": pricing_plan_version,
        "delivery_boundary": (
            "public_manifest_and_authorization_documents_only"
            if key == ("data", "deidentified_data_delivery")
            else "model_card_manifest_and_license_only_no_weights"
            if key == ("model", "model_artifact_license")
            else "controlled_execution_only_no_raw_data_or_model_weights"
        ),
        "payment_mode": "local_demo_simulation",
    }
    for field in (
        "unit_label",
        "price_label",
        "revenue_basis",
        "fulfillment_scope",
        "model_weights_included",
    ):
        if configured is not None and field in configured:
            result[field] = configured[field]
    if "pricing_notice" in commercial_root:
        result["pricing_notice"] = commercial_root["pricing_notice"]
    return result
