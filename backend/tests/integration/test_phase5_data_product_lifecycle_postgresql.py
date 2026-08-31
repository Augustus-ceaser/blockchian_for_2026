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
from app.demo.phase4 import ensure_phase4_demo_initial
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


def _payload(suffix: str, connector_id: str) -> dict:
    return {
        "basic": {
            "name": f"Phase 5.1 lifecycle product {suffix}",
            "short_name": f"P51-{suffix}",
            "department": "Pathology research department",
            "disease_domain": "colorectal pathology classification",
            "modality": "digital pathology image",
            "source_type": "public_demo_dataset",
            "description": (
                "Public demonstration metadata used only to validate the Phase 5.1 "
                "catalog lifecycle without patient data or source-file upload."
            ),
            "data_owner": "Demo data administrator",
            "contact_department": "Pathology research department",
            "is_demo": True,
        },
        "composition": {
            "case_count": 20,
            "slide_count": 0,
            "image_count": 20,
            "data_format": "NPZ metadata",
            "image_specification": "28 x 28 RGB",
            "annotation_type": "nine-class public benchmark label",
            "annotation_coverage": 100,
            "completeness_rate": 100,
            "quality_status": "passed",
            "data_version": "v1.0",
            "version_notes": (
                "Initial public demonstration metadata version for lifecycle verification."
            ),
            "resource_summary": (
                "Twenty authorized public demonstration images represented only by metadata."
            ),
        },
        "policy": {
            "allowed_purposes": ["research_analysis", "model_validation"],
            "prohibited_purposes": [
                "clinical diagnosis",
                "secondary distribution",
                "patient identification",
            ],
            "max_runs": 5,
            "valid_days": 30,
            "fixed_model_version": True,
            "requires_egress_review": True,
            "internet_allowed": False,
            "input_read_only": True,
            "allowed_outputs": [
                "aggregate_metrics",
                "confusion_matrix",
                "execution_summary",
            ],
            "prohibited_outputs": [
                "raw images",
                "sample predictions",
                "raw features",
                "model weights",
                "execution scripts",
                "connector credentials",
            ],
            "hard_isolation": False,
        },
        "binding": {
            "connector_id": connector_id,
            "resource_identifier": f"P51-{suffix}",
            "data_ready": True,
        },
    }


def test_phase5_data_product_lifecycle_authorization_idempotency_and_catalog() -> None:
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
        Settings(
            app_env="test",
            database_url=DATABASE_URL,
            demo_api_enabled=True,
        )
    )
    app.dependency_overrides[get_db_session] = test_session
    suffix = uuid4().hex[:10]
    hospital = {"X-Demo-Identity": "data_provider"}
    operator = {"X-Demo-Identity": "space_operator"}
    requester = {"X-Demo-Identity": "data_requester"}
    model_provider = {"X-Demo-Identity": "model_provider"}
    try:
        with TestClient(app) as client:
            connectors = client.get("/api/v1/data-product-connectors", headers=hospital)
            assert connectors.status_code == 200
            connector_id = connectors.json()["items"][0]["id"]
            payload = _payload(suffix, connector_id)

            denied_create = client.post(
                "/api/v1/data-products",
                headers={**model_provider, "Idempotency-Key": f"denied-create-{suffix}"},
                json=payload,
            )
            assert denied_create.status_code == 403

            unsafe = _payload(f"unsafe-{suffix}", connector_id)
            unsafe["policy"]["allowed_outputs"].append("raw_images")
            unsafe_create = client.post(
                "/api/v1/data-products",
                headers={**hospital, "Idempotency-Key": f"unsafe-create-{suffix}"},
                json=unsafe,
            )
            assert unsafe_create.status_code == 409

            create_key = f"create-product-{suffix}"
            first = client.post(
                "/api/v1/data-products",
                headers={**hospital, "Idempotency-Key": create_key},
                json=payload,
            )
            replay = client.post(
                "/api/v1/data-products",
                headers={**hospital, "Idempotency-Key": create_key},
                json=payload,
            )
            assert first.status_code == replay.status_code == 201
            assert first.json() == replay.json()
            version_id = first.json()["version_id"]
            product_code = first.json()["product_code"]

            catalog_before = client.get("/api/v1/data-product-catalog", headers=requester)
            assert catalog_before.status_code == 200
            assert version_id not in {
                item["version_id"] for item in catalog_before.json()["items"]
            }
            hidden_detail = client.get(
                f"/api/v1/data-product-versions/{version_id}", headers=requester
            )
            assert hidden_detail.status_code == 404

            payload["basic"]["name"] += " updated"
            update = client.patch(
                f"/api/v1/data-product-versions/{version_id}",
                headers={**hospital, "Idempotency-Key": f"update-a-{suffix}"},
                json=payload,
            )
            assert update.status_code == 200
            denied_update = client.patch(
                f"/api/v1/data-product-versions/{version_id}",
                headers={**model_provider, "Idempotency-Key": f"denied-update-{suffix}"},
                json=payload,
            )
            assert denied_update.status_code == 403

            submit = client.post(
                f"/api/v1/data-product-versions/{version_id}/submit",
                headers={**hospital, "Idempotency-Key": f"submit-a-{suffix}"},
            )
            submit_replay = client.post(
                f"/api/v1/data-product-versions/{version_id}/submit",
                headers={**hospital, "Idempotency-Key": f"submit-a-{suffix}"},
            )
            assert submit.status_code == submit_replay.status_code == 200
            assert submit.json() == submit_replay.json()
            assert submit.json()["status"] == "under_review"

            queue = client.get("/api/v1/data-product-review-queue", headers=operator)
            assert queue.status_code == 200
            assert version_id in {item["version_id"] for item in queue.json()["items"]}

            returned = client.post(
                f"/api/v1/data-product-versions/{version_id}/return",
                headers={**operator, "Idempotency-Key": f"return-{suffix}"},
                json={
                    "review_opinion": "Clarify the public demonstration source statement.",
                    "additional_conditions": "",
                    "requested_materials": "Add explicit demonstration-only provenance.",
                    "risk_level": "low",
                    "allow_catalog": False,
                },
            )
            assert returned.status_code == 200
            assert returned.json()["status"] == "draft"
            detail = client.get(
                f"/api/v1/data-product-versions/{version_id}", headers=hospital
            ).json()
            assert detail["latest_return"]["requested_materials"]

            payload["composition"]["version_notes"] = (
                "Returned draft updated with explicit public-source and "
                "demonstration-only provenance."
            )
            second_update = client.patch(
                f"/api/v1/data-product-versions/{version_id}",
                headers={**hospital, "Idempotency-Key": f"update-b-{suffix}"},
                json=payload,
            )
            assert second_update.status_code == 200
            second_submit = client.post(
                f"/api/v1/data-product-versions/{version_id}/submit",
                headers={**hospital, "Idempotency-Key": f"submit-b-{suffix}"},
            )
            assert second_submit.status_code == 200

            approval_key = f"approve-{suffix}"
            approval_payload = {
                "review_opinion": "Metadata, policy and demonstration boundary are complete.",
                "additional_conditions": "Use only the controlled-compute request path.",
                "requested_materials": "",
                "risk_level": "low",
                "allow_catalog": True,
            }
            approved = client.post(
                f"/api/v1/data-product-versions/{version_id}/approve",
                headers={**operator, "Idempotency-Key": approval_key},
                json=approval_payload,
            )
            approved_replay = client.post(
                f"/api/v1/data-product-versions/{version_id}/approve",
                headers={**operator, "Idempotency-Key": approval_key},
                json=approval_payload,
            )
            assert approved.status_code == approved_replay.status_code == 200
            assert approved.json() == approved_replay.json()
            assert approved.json()["status"] == "published"

            catalog_after = client.get("/api/v1/data-product-catalog", headers=requester)
            assert catalog_after.status_code == 200
            published = next(
                item
                for item in catalog_after.json()["items"]
                if item["version_id"] == version_id
            )
            assert published["product_code"] == product_code
            assert published["service_capability"]["service_mode"] == "controlled_compute"
            assert published["service_capability"]["requestability"] == "eligible"
            assert published["service_capability"]["runtime_availability"] == "ready"
            assert published["execution_readiness"] == "ready"
            assert published["application_eligibility"] is True
            serialized = str(published).lower()
            assert "connector_id" not in serialized
            assert "patient_id" not in serialized
            assert "patient_identifier" not in serialized
            assert "raw_image" not in serialized
            assert ":\\" not in serialized

            audit = client.get(
                f"/api/v1/data-product-versions/{version_id}/audit-events",
                headers=hospital,
            )
            assert audit.status_code == 200
            assert audit.json()["audit_chain_valid"] is True
            event_types = [item["event_type"] for item in audit.json()["items"]]
            assert event_types == [
                "data_product.version.published",
                "data_product.version.approved",
                "data_product.version.submitted",
                "data_product.version.updated",
                "data_product.version.returned",
                "data_product.version.submitted",
                "data_product.version.updated",
                "data_product.version.created",
            ]
    finally:
        app.dependency_overrides.clear()
