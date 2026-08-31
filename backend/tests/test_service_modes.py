from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.applications import _application_options_payload
from app.api.routes.data_products import PolicyInformation
from app.api.routes.model_products import ModelPolicyInformation
from app.modules.applications.lifecycle import (
    ApplicationLifecycleError,
    _require_controlled_compute_offerings,
)
from app.modules.catalog.product_lifecycle import _documents as data_documents
from app.modules.marketplace.model_lifecycle import _documents as model_documents
from app.modules.marketplace.service_modes import (
    CONTROLLED_COMPUTE,
    DEIDENTIFIED_DATA_DELIVERY,
    MODEL_ARTIFACT_LICENSE,
    build_service_offerings,
    default_service_mode,
    resolve_service_modes,
    validate_service_modes,
)


def test_service_mode_validation_is_scoped_to_product_kind() -> None:
    assert validate_service_modes(
        "data", [CONTROLLED_COMPUTE, DEIDENTIFIED_DATA_DELIVERY]
    ) == (CONTROLLED_COMPUTE, DEIDENTIFIED_DATA_DELIVERY)
    assert validate_service_modes(
        "model", [CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE]
    ) == (CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE)

    with pytest.raises(ValueError, match="unsupported data service modes"):
        validate_service_modes("data", [MODEL_ARTIFACT_LICENSE])
    with pytest.raises(ValueError, match="must be unique"):
        validate_service_modes("model", [CONTROLLED_COMPUTE, CONTROLLED_COMPUTE])


def test_old_internal_policy_falls_back_but_external_has_no_offerings() -> None:
    assert resolve_service_modes("data", {}) == (CONTROLLED_COMPUTE,)
    assert resolve_service_modes("model", None) == (CONTROLLED_COMPUTE,)
    assert build_service_offerings(
        "data",
        {"service_modes": [DEIDENTIFIED_DATA_DELIVERY]},
        controlled_compute_requestable=False,
        authorization_requestable=True,
        external=True,
    ) == []
    assert default_service_mode(
        "data", {"service_modes": [DEIDENTIFIED_DATA_DELIVERY]}
    ) == DEIDENTIFIED_DATA_DELIVERY


def test_offerings_expose_requestability_without_claiming_delivery() -> None:
    offerings = build_service_offerings(
        "data",
        {
            "service_modes": [
                CONTROLLED_COMPUTE,
                DEIDENTIFIED_DATA_DELIVERY,
            ]
        },
        controlled_compute_requestable=False,
        authorization_requestable=True,
    )
    assert offerings == [
        {
            "mode": CONTROLLED_COMPUTE,
            "label": "受控调用计算",
            "requestable": False,
            "fulfillment_status": "unavailable",
            "requires_contract": True,
        },
        {
            "mode": DEIDENTIFIED_DATA_DELIVERY,
            "label": "脱敏数据授权交付",
            "requestable": True,
            "fulfillment_status": "requires_review",
            "requires_contract": True,
        },
    ]


def test_draft_offerings_fail_closed_until_publication() -> None:
    offerings = build_service_offerings(
        "model",
        {"service_modes": [CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE]},
        controlled_compute_requestable=True,
        authorization_requestable=False,
    )
    assert [offering["requestable"] for offering in offerings] == [False, False]
    assert [offering["fulfillment_status"] for offering in offerings] == [
        "unavailable",
        "unavailable",
    ]


def test_request_schemas_keep_legacy_default_and_reject_duplicates() -> None:
    data_policy = PolicyInformation(
        allowed_purposes=["research_analysis"],
        prohibited_purposes=["clinical_diagnosis"],
        max_runs=1,
        valid_days=30,
        allowed_outputs=["aggregate_metrics"],
        prohibited_outputs=["raw_images"],
    )
    model_policy = ModelPolicyInformation(
        allowed_purposes=["research_analysis"],
        prohibited_purposes=["clinical_diagnosis"],
        max_runs=1,
        valid_days=30,
    )
    assert data_policy.service_modes == [CONTROLLED_COMPUTE]
    assert model_policy.service_modes == [CONTROLLED_COMPUTE]

    with pytest.raises(ValidationError, match="must be unique"):
        PolicyInformation(
            service_modes=[CONTROLLED_COMPUTE, CONTROLLED_COMPUTE],
            allowed_purposes=["research_analysis"],
            prohibited_purposes=["clinical_diagnosis"],
            max_runs=1,
            valid_days=30,
            allowed_outputs=["aggregate_metrics"],
            prohibited_outputs=["raw_images"],
        )


def test_lifecycle_documents_persist_explicit_service_modes() -> None:
    data_document = {
        "basic": {
            "short_name": "demo",
            "department": "骨科",
            "data_owner": "医院",
            "contact_department": "骨科",
            "source_type": "public_demo",
        },
        "composition": {
            "case_count": 10,
            "slide_count": 0,
            "image_count": 10,
            "data_format": "PNG",
            "image_specification": "224x224 RGB",
            "annotation_type": "classification",
            "annotation_coverage": 1.0,
            "completeness_rate": 1.0,
            "quality_status": "passed",
            "resource_summary": "public demonstration images",
        },
        "policy": {
            "service_modes": [
                CONTROLLED_COMPUTE,
                DEIDENTIFIED_DATA_DELIVERY,
            ],
            "allowed_purposes": ["research_analysis"],
            "prohibited_purposes": ["clinical_diagnosis"],
            "max_runs": 1,
            "valid_days": 30,
            "fixed_model_version": True,
            "requires_egress_review": True,
            "internet_allowed": False,
            "input_read_only": True,
            "allowed_outputs": ["aggregate_metrics"],
            "prohibited_outputs": ["raw_images"],
        },
        "binding": {
            "connector_id": uuid4(),
            "resource_identifier": "demo-resource",
            "data_ready": True,
        },
    }
    data_policy = data_documents(data_document, "DATA-DEMO")["policy"]
    assert data_policy["service_modes"] == [
        CONTROLLED_COMPUTE,
        DEIDENTIFIED_DATA_DELIVERY,
    ]

    model_document = {
        "basic": {
            "short_name": "demo",
            "team": "模型团队",
            "task_type": "risk_prediction",
            "task_description": "骨折风险预测",
            "modality": "X-ray",
            "source_type": "fixed_demo",
            "model_owner": "模型方",
            "contact_department": "研发部",
        },
        "runtime": {
            "version_notes": "fixed demonstration version",
            "framework": "PyTorch",
            "device": "CPU",
        },
        "schema": {
            "input_schema": {"modality": "X-ray"},
            "output_schema": {"type": "risk_score"},
            "allowed_outputs": ["aggregate_metrics"],
            "prohibited_outputs": ["model_weights"],
        },
        "policy": {
            "service_modes": [CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE],
            "allowed_purposes": ["research_analysis"],
            "prohibited_purposes": ["clinical_diagnosis"],
            "max_runs": 1,
            "valid_days": 30,
            "multi_center_validation": False,
            "commercial_validation": False,
            "research_publication": True,
            "provider_result_confirmation": True,
        },
    }
    entry = SimpleNamespace(cpu_limit=1, memory_limit=256, timeout_seconds=30)
    model_policy = model_documents(model_document, entry)["policy"]
    assert model_policy["service_modes"] == [
        CONTROLLED_COMPUTE,
        MODEL_ARTIFACT_LICENSE,
    ]


def test_application_lifecycle_rejects_delivery_only_selection() -> None:
    legacy_data = SimpleNamespace(default_policy_template={})
    compute_model = SimpleNamespace(
        default_policy_template={"service_modes": [CONTROLLED_COMPUTE]}
    )
    _require_controlled_compute_offerings(legacy_data, compute_model)

    delivery_only_data = SimpleNamespace(
        default_policy_template={
            "service_modes": [DEIDENTIFIED_DATA_DELIVERY]
        }
    )
    with pytest.raises(ApplicationLifecycleError, match="data product version"):
        _require_controlled_compute_offerings(delivery_only_data, compute_model)

    compute_data = SimpleNamespace(
        default_policy_template={"service_modes": [CONTROLLED_COMPUTE]}
    )
    license_only_model = SimpleNamespace(
        default_policy_template={"service_modes": [MODEL_ARTIFACT_LICENSE]}
    )
    with pytest.raises(ApplicationLifecycleError, match="model product version"):
        _require_controlled_compute_offerings(compute_data, license_only_model)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _statement):
        return _FakeResult(self._results.pop(0))


def _data_option_row(name: str, modes: list[str] | None):
    policy = {} if modes is None else {"service_modes": modes}
    product = SimpleNamespace(
        id=uuid4(),
        product_code=f"DATA-{name}",
        name=name,
        domain="orthopedics",
        is_demo=True,
    )
    version = SimpleNamespace(
        id=uuid4(),
        version_no=1,
        version_label="v1",
        linkage_metadata={"modality": "X-ray"},
        scope_metadata={},
        quality_report={},
        default_policy_template=policy,
        snapshot_digest=f"sha256:{'1' * 64}",
    )
    provider = SimpleNamespace(id=uuid4(), display_name="医院")
    return product, version, provider


def _model_option_row(name: str, modes: list[str] | None):
    policy = {} if modes is None else {"service_modes": modes}
    product = SimpleNamespace(
        id=uuid4(),
        product_code=f"MODEL-{name}",
        name=name,
        domain="orthopedics",
        is_demo=True,
    )
    version = SimpleNamespace(
        id=uuid4(),
        version_no=1,
        version_label="v1",
        compatibility_metadata={
            "task_type": "risk_prediction",
            "modality": "X-ray",
            "input_schema": {},
            "output_schema": {},
            "non_clinical": True,
        },
        default_policy_template=policy,
        license_metadata={},
        entrypoint_id="demo_model",
        snapshot_digest=f"sha256:{'2' * 64}",
        registry_digest=f"sha256:{'3' * 64}",
    )
    provider = SimpleNamespace(id=uuid4(), display_name="模型方")
    return product, version, provider


def test_application_options_only_include_controlled_compute_products() -> None:
    session = _FakeSession(
        [
            [
                _data_option_row("legacy", None),
                _data_option_row("delivery", [DEIDENTIFIED_DATA_DELIVERY]),
            ],
            [
                _model_option_row("compute", [CONTROLLED_COMPUTE]),
                _model_option_row("license", [MODEL_ARTIFACT_LICENSE]),
            ],
        ]
    )
    payload = asyncio.run(_application_options_payload(session, uuid4()))
    assert [item["name"] for item in payload["data_products"]] == ["legacy"]
    assert [item["name"] for item in payload["model_products"]] == ["compute"]
