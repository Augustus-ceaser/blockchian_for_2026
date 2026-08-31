from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict, cast


ProductKind = Literal["data", "model"]
ServiceMode = Literal[
    "controlled_compute",
    "deidentified_data_delivery",
    "model_artifact_license",
]

CONTROLLED_COMPUTE: ServiceMode = "controlled_compute"
DEIDENTIFIED_DATA_DELIVERY: ServiceMode = "deidentified_data_delivery"
MODEL_ARTIFACT_LICENSE: ServiceMode = "model_artifact_license"

SERVICE_MODE_LABELS: dict[ServiceMode, str] = {
    CONTROLLED_COMPUTE: "受控调用计算",
    DEIDENTIFIED_DATA_DELIVERY: "脱敏数据授权交付",
    MODEL_ARTIFACT_LICENSE: "模型使用许可",
}

_ALLOWED_BY_KIND: dict[ProductKind, tuple[ServiceMode, ...]] = {
    "data": (CONTROLLED_COMPUTE, DEIDENTIFIED_DATA_DELIVERY),
    "model": (CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE),
}


class ServiceOffering(TypedDict):
    mode: ServiceMode
    label: str
    requestable: bool
    fulfillment_status: str
    requires_contract: bool


def allowed_service_modes(product_kind: ProductKind) -> tuple[ServiceMode, ...]:
    return _ALLOWED_BY_KIND[product_kind]


def validate_service_modes(
    product_kind: ProductKind,
    modes: Iterable[str],
) -> tuple[ServiceMode, ...]:
    normalized = tuple(str(mode).strip() for mode in modes)
    if not normalized:
        raise ValueError("at least one service mode is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError("service modes must be unique")
    allowed = set(allowed_service_modes(product_kind))
    invalid = sorted(set(normalized) - allowed)
    if invalid:
        raise ValueError(
            f"unsupported {product_kind} service modes: {', '.join(invalid)}"
        )
    return cast(tuple[ServiceMode, ...], normalized)


def resolve_service_modes(
    product_kind: ProductKind,
    policy: Mapping[str, Any] | None,
    *,
    external: bool = False,
) -> tuple[ServiceMode, ...]:
    if external:
        return ()
    raw_modes = None if policy is None else policy.get("service_modes")
    if raw_modes is None:
        # Existing internal versions predate explicit service offerings.
        return (CONTROLLED_COMPUTE,)
    if isinstance(raw_modes, str) or not isinstance(raw_modes, Iterable):
        raise ValueError("service_modes must be a list")
    return validate_service_modes(product_kind, raw_modes)


def service_mode_enabled(
    product_kind: ProductKind,
    policy: Mapping[str, Any] | None,
    mode: ServiceMode,
    *,
    external: bool = False,
) -> bool:
    return mode in resolve_service_modes(product_kind, policy, external=external)


def default_service_mode(
    product_kind: ProductKind,
    policy: Mapping[str, Any] | None,
) -> ServiceMode:
    modes = resolve_service_modes(product_kind, policy)
    return CONTROLLED_COMPUTE if CONTROLLED_COMPUTE in modes else modes[0]


def build_service_offerings(
    product_kind: ProductKind,
    policy: Mapping[str, Any] | None,
    *,
    controlled_compute_requestable: bool,
    authorization_requestable: bool,
    external: bool = False,
) -> list[ServiceOffering]:
    offerings: list[ServiceOffering] = []
    for mode in resolve_service_modes(product_kind, policy, external=external):
        if mode == CONTROLLED_COMPUTE:
            requestable = (
                authorization_requestable and controlled_compute_requestable
            )
            fulfillment_status = (
                "controlled_ready" if requestable else "unavailable"
            )
        else:
            requestable = authorization_requestable
            fulfillment_status = (
                "requires_review" if requestable else "unavailable"
            )
        offerings.append(
            {
                "mode": mode,
                "label": SERVICE_MODE_LABELS[mode],
                "requestable": requestable,
                "fulfillment_status": fulfillment_status,
                "requires_contract": True,
            }
        )
    return offerings
