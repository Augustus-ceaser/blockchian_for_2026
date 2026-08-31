from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.session import get_db_session
from app.demo.phase4 import ensure_phase4_demo_initial, load_pathmnist_model_registry
from app.main import create_app


DATABASE_URL = os.getenv("MEDTRUST_PHASE5_TEST_DATABASE_URL")
WORKSPACE = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE5_TEST_DATABASE_URL is not configured",
    ),
]


def _payload(suffix: str) -> dict:
    entry = load_pathmnist_model_registry(WORKSPACE).require_enabled(
        "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
    )
    return {
        "basic": {
            "name": f"Phase 5.2 lifecycle model {suffix}",
            "short_name": f"M52-{suffix}",
            "team": "Medical AI engineering",
            "task_type": "image_classification",
            "task_description": "nine-class pathology image classification",
            "disease_domain": "colorectal pathology classification",
            "modality": "digital_pathology",
            "description": (
                "Fixed allowlisted non-clinical model metadata used only for "
                "Phase 5.2 catalog lifecycle verification."
            ),
            "source_type": "platform_allowlisted",
            "model_owner": "Demo model owner",
            "contact_department": "Medical AI engineering",
            "is_demo": True,
            "clinical_use": False,
        },
        "runtime": {
            "version_label": "v1.0",
            "version_notes": "Initial allowlisted model product lifecycle version.",
            "framework": "PyTorch",
            "runtime": entry.runtime,
            "model_digest": entry.model_digest,
            "entrypoint_id": entry.entrypoint_id,
            "input_schema_version": entry.input_schema_version,
            "output_schema_version": entry.output_schema_version,
            "device": "cpu",
            "cpu_limit": entry.cpu_limit,
            "memory_limit_mb": entry.memory_limit,
            "timeout_seconds": entry.timeout_seconds,
            "network_access": False,
            "input_read_only": True,
            "dynamic_dependencies": False,
            "arbitrary_code": False,
            "model_ready": True,
            "executor_type": "local_builtin",
        },
        "schema": {
            "input_schema": {
                "type": "image",
                "modality": "digital_pathology",
                "width": 28,
                "height": 28,
                "channels": 3,
                "dtype": "uint8",
                "batch_supported": True,
            },
            "output_schema": {
                "aggregate_accuracy": True,
                "mean_confidence": True,
                "confusion_matrix": True,
                "execution_summary": True,
            },
            "allowed_outputs": [
                "aggregate_metrics",
                "confusion_matrix",
                "execution_summary",
            ],
            "prohibited_outputs": [
                "model weights",
                "intermediate features",
                "raw input images",
                "arbitrary scripts",
                "unapproved sample predictions",
                "runtime credentials",
            ],
        },
        "policy": {
            "allowed_purposes": ["research validation", "teaching demo"],
            "prohibited_purposes": ["clinical diagnosis", "redistribution"],
            "max_runs": 5,
            "valid_days": 30,
            "multi_center_validation": False,
            "commercial_validation": False,
            "research_publication": True,
            "provider_result_confirmation": True,
            "model_download": False,
            "reverse_engineering": False,
            "redistribution": False,
            "dynamic_script_execution": False,
            "unauthorized_network": False,
        },
    }


def test_phase5_model_product_lifecycle_registry_authorization_and_catalog() -> None:
    assert DATABASE_URL is not None

    async def ensure_baseline() -> None:
        seed_engine = create_async_engine(DATABASE_URL)
        seed_factory = async_sessionmaker(seed_engine, expire_on_commit=False)
        try:
            async with seed_factory() as session:
                async with session.begin():
                    await ensure_phase4_demo_initial(session, workspace=WORKSPACE)
        finally:
            await seed_engine.dispose()

    asyncio.run(ensure_baseline())
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session():
        async with factory() as session:
            yield session

    app = create_app(
        Settings(app_env="test", database_url=DATABASE_URL, demo_api_enabled=True)
    )
    app.dependency_overrides[get_db_session] = test_session
    suffix = uuid4().hex[:10]
    provider = {"X-Demo-Identity": "model_provider"}
    operator = {"X-Demo-Identity": "space_operator"}
    requester = {"X-Demo-Identity": "data_requester"}
    hospital = {"X-Demo-Identity": "data_provider"}
    try:
        with TestClient(app) as client:
            assets = client.get("/api/v1/model-assets", headers=provider)
            assert assets.status_code == 200
            assert assets.json()["items"][0]["entrypoint_id"] == "pathmnist_resnet18_v1"
            payload = _payload(suffix)

            denied = client.post(
                "/api/v1/model-products",
                headers={**hospital, "Idempotency-Key": f"denied-model-{suffix}"},
                json=payload,
            )
            assert denied.status_code == 403

            unsafe = _payload(f"unsafe-{suffix}")
            unsafe["policy"]["model_download"] = True
            unsafe_create = client.post(
                "/api/v1/model-products",
                headers={**provider, "Idempotency-Key": f"unsafe-model-{suffix}"},
                json=unsafe,
            )
            assert unsafe_create.status_code == 409

            create_key = f"create-model-{suffix}"
            first = client.post(
                "/api/v1/model-products",
                headers={**provider, "Idempotency-Key": create_key},
                json=payload,
            )
            replay = client.post(
                "/api/v1/model-products",
                headers={**provider, "Idempotency-Key": create_key},
                json=payload,
            )
            assert first.status_code == replay.status_code == 201
            assert first.json() == replay.json()
            version_id = first.json()["version_id"]
            product_code = first.json()["product_code"]

            catalog_before = client.get("/api/v1/model-product-catalog", headers=requester)
            assert version_id not in {
                item["version_id"] for item in catalog_before.json()["items"]
            }
            hidden = client.get(
                f"/api/v1/model-product-versions/{version_id}", headers=requester
            )
            assert hidden.status_code == 404

            payload["basic"]["name"] += " updated"
            updated = client.patch(
                f"/api/v1/model-product-versions/{version_id}",
                headers={**provider, "Idempotency-Key": f"update-model-a-{suffix}"},
                json=payload,
            )
            assert updated.status_code == 200

            submitted = client.post(
                f"/api/v1/model-product-versions/{version_id}/submit",
                headers={**provider, "Idempotency-Key": f"submit-model-a-{suffix}"},
            )
            submitted_replay = client.post(
                f"/api/v1/model-product-versions/{version_id}/submit",
                headers={**provider, "Idempotency-Key": f"submit-model-a-{suffix}"},
            )
            assert submitted.status_code == submitted_replay.status_code == 200
            assert submitted.json() == submitted_replay.json()
            assert submitted.json()["status"] == "under_review"

            queue = client.get("/api/v1/model-product-review-queue", headers=operator)
            assert version_id in {item["version_id"] for item in queue.json()["items"]}

            returned = client.post(
                f"/api/v1/model-product-versions/{version_id}/return",
                headers={**operator, "Idempotency-Key": f"return-model-{suffix}"},
                json={
                    "review_opinion": "Clarify the non-clinical license statement.",
                    "technical_risk": "low",
                    "license_risk": "medium",
                    "additional_conditions": "",
                    "requested_materials": "Add explicit fixed-asset wording.",
                    "allow_catalog": False,
                },
            )
            assert returned.status_code == 200
            assert returned.json()["status"] == "draft"

            payload["runtime"]["version_notes"] = (
                "Returned draft now explicitly states fixed-asset and non-clinical use."
            )
            revised = client.patch(
                f"/api/v1/model-product-versions/{version_id}",
                headers={**provider, "Idempotency-Key": f"update-model-b-{suffix}"},
                json=payload,
            )
            assert revised.status_code == 200
            revised_detail = client.get(
                f"/api/v1/model-product-versions/{version_id}", headers=provider
            )
            assert revised_detail.status_code == 200
            assert revised_detail.json()["compatibility"]["version_notes"] == (
                "Returned draft now explicitly states fixed-asset and non-clinical use."
            )
            assert client.post(
                f"/api/v1/model-product-versions/{version_id}/submit",
                headers={**provider, "Idempotency-Key": f"submit-model-b-{suffix}"},
            ).status_code == 200

            approval_payload = {
                "review_opinion": "Registry, runtime, schema and license boundaries are complete.",
                "technical_risk": "low",
                "license_risk": "low",
                "additional_conditions": "Use only through a separately approved demand.",
                "requested_materials": "",
                "allow_catalog": True,
            }
            approve_key = f"approve-model-{suffix}"
            approved = client.post(
                f"/api/v1/model-product-versions/{version_id}/approve",
                headers={**operator, "Idempotency-Key": approve_key},
                json=approval_payload,
            )
            approved_replay = client.post(
                f"/api/v1/model-product-versions/{version_id}/approve",
                headers={**operator, "Idempotency-Key": approve_key},
                json=approval_payload,
            )
            assert approved.status_code == approved_replay.status_code == 200
            assert approved.json() == approved_replay.json()

            catalog_after = client.get("/api/v1/model-product-catalog", headers=requester)
            published = next(
                item for item in catalog_after.json()["items"]
                if item["version_id"] == version_id
            )
            assert published["product_code"] == product_code
            serialized = str(published).lower()
            assert "model_digest" not in serialized
            assert "entrypoint_id" not in serialized
            assert "asset_locator" not in serialized
            assert ":\\" not in serialized

            audit = client.get(
                f"/api/v1/model-product-versions/{version_id}/audit-events",
                headers=provider,
            )
            assert audit.status_code == 200
            assert audit.json()["audit_chain_valid"] is True
            assert [item["event_type"] for item in audit.json()["items"]] == [
                "model_product.version.published",
                "model_product.version.approved",
                "model_product.version.submitted",
                "model_product.version.updated",
                "model_product.version.returned",
                "model_product.version.submitted",
                "model_product.version.updated",
                "model_product.version.created",
            ]
    finally:
        app.dependency_overrides.clear()
