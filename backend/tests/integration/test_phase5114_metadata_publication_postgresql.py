from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.session import get_db_session
from app.demo.phase4 import ensure_phase4_demo_initial
from app.main import create_app
from app.modules.applications.models import Application
from app.modules.audit.models import AuditEvent
from app.modules.catalog.models import DataProduct, DataProductVersion
from app.modules.compute.models import ComputeJob
from app.modules.external_catalog.eligibility import (
    DATA_PRODUCT_NOT_MATERIALIZED,
    ExternalDataProductEligibilityError,
    require_materialized_data_product,
)
from app.modules.external_catalog.models import DataProductExternalSourceLink


DATABASE_URL = os.getenv("MEDTRUST_PHASE5114_TEST_DATABASE_URL")
WORKSPACE = Path(__file__).resolve().parents[3]
SELECTED = {"CPTAC-COAD", "CAMELYON17", "HyperKvasir"}
REMAINING = {"Hungarian-Colorectal-Screening", "4D-Lung"}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE5114_TEST_DATABASE_URL is not configured",
    ),
]


def test_phase5114_metadata_publication_and_compute_denial() -> None:
    assert DATABASE_URL is not None
    async def seed_and_snapshot():
        seed_engine = create_async_engine(DATABASE_URL)
        seed_factory = async_sessionmaker(seed_engine, expire_on_commit=False)
        try:
            async with seed_factory() as session:
                async with session.begin():
                    await ensure_phase4_demo_initial(session, workspace=WORKSPACE)
                rows = (
                    await session.execute(
                        select(DataProduct, DataProductVersion)
                        .join(
                            DataProductExternalSourceLink,
                            DataProductExternalSourceLink.data_product_id
                            == DataProduct.id,
                        )
                        .join(
                            DataProductVersion,
                            DataProductVersion.id
                            == DataProductExternalSourceLink.data_product_version_id,
                        )
                    )
                ).all()
                products = {
                    product.name: {
                        "product_id": str(product.id),
                        "version_id": str(version.id),
                        "lifecycle": product.lifecycle_status,
                        "version_status": version.status,
                    }
                    for product, version in rows
                }
                return (
                    products,
                    int(
                        await session.scalar(
                            select(func.count()).select_from(Application)
                        )
                        or 0
                    ),
                    int(
                        await session.scalar(
                            select(func.count()).select_from(ComputeJob)
                        )
                        or 0
                    ),
                )
        finally:
            await seed_engine.dispose()

    products, applications_before, jobs_before = asyncio.run(seed_and_snapshot())
    assert SELECTED | REMAINING <= products.keys()
    archived = next(
        item for item in products.values() if item["lifecycle"] == "archived"
    )

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
            enable_demo_role_switch=True,
        )
    )
    app.dependency_overrides[get_db_session] = test_session
    curator = {"X-Demo-Identity": "catalog_curator"}
    operator = {"X-Demo-Identity": "space_operator"}
    requester = {"X-Demo-Identity": "data_requester"}
    hospital = {"X-Demo-Identity": "data_provider"}
    model_provider = {"X-Demo-Identity": "model_provider"}
    review = {
        "review_opinion": "Metadata evidence and non-computable policy verified.",
        "additional_conditions": "Revalidate upstream terms before materialization.",
        "requested_materials": "",
        "risk_level": "low",
        "allow_catalog": True,
    }
    try:
        with TestClient(app) as client:
            denied = client.post(
                f"/api/v1/data-product-versions/{products['CPTAC-COAD']['version_id']}/submit",
                headers={**operator, "Idempotency-Key": "phase5114-test-wrong-submitter"},
            )
            assert denied.status_code == 409

            archived_submit = client.post(
                f"/api/v1/data-product-versions/{archived['version_id']}/submit",
                headers={**curator, "Idempotency-Key": "phase5114-test-archived-submit"},
            )
            assert archived_submit.status_code == 409

            for name in sorted(SELECTED):
                version_id = products[name]["version_id"]
                submit_key = f"phase5114-test-submit-{name.lower()}"
                submitted = client.post(
                    f"/api/v1/data-product-versions/{version_id}/submit",
                    headers={**curator, "Idempotency-Key": submit_key},
                )
                replay = client.post(
                    f"/api/v1/data-product-versions/{version_id}/submit",
                    headers={**curator, "Idempotency-Key": submit_key},
                )
                assert submitted.status_code == 200, submitted.text
                assert replay.status_code == 200, replay.text
                assert submitted.json() == replay.json()
                assert submitted.json()["status"] == "under_review"

                approved = client.post(
                    f"/api/v1/data-product-versions/{version_id}/approve",
                    headers={
                        **operator,
                        "Idempotency-Key": f"phase5114-test-approve-{name.lower()}",
                    },
                    json=review,
                )
                assert approved.status_code == 200, approved.text
                assert approved.json()["status"] == "published"

            catalog_versions = None
            for headers in (requester, hospital, model_provider, curator, operator):
                response = client.get("/api/v1/data-product-catalog", headers=headers)
                assert response.status_code == 200
                current = {
                    item["version_id"]: item for item in response.json()["items"]
                }
                assert all(
                    products[name]["version_id"] in current for name in SELECTED
                )
                for name in SELECTED:
                    item = current[products[name]["version_id"]]
                    assert item["source_kind"] == "external_public_metadata"
                    assert item["materialization_status"] == "metadata_only"
                    assert item["execution_readiness"] == "not_ready"
                    assert item["application_eligibility"] is False
                    assert item["service_capability"]["service_mode"] == "metadata_only"
                    assert item["service_capability"]["requestability"] == "not_eligible"
                    assert (
                        item["service_capability"]["runtime_availability"]
                        == "not_applicable"
                    )
                catalog_versions = set(current)

            options = client.get("/api/v1/application-options", headers=requester)
            assert options.status_code == 200
            option_versions = {
                item["version_id"] for item in options.json()["data_products"]
            }
            assert not {
                products[name]["version_id"] for name in SELECTED
            } & option_versions
            assert catalog_versions is not None
    finally:
        app.dependency_overrides.clear()

    async def verify_final() -> None:
        verify_engine = create_async_engine(DATABASE_URL)
        verify_factory = async_sessionmaker(verify_engine, expire_on_commit=False)
        try:
            async with verify_factory() as session:
                rows = (
                    await session.execute(
                        select(DataProduct, DataProductVersion)
                        .join(
                            DataProductExternalSourceLink,
                            DataProductExternalSourceLink.data_product_id
                            == DataProduct.id,
                        )
                        .join(
                            DataProductVersion,
                            DataProductVersion.id
                            == DataProductExternalSourceLink.data_product_version_id,
                        )
                    )
                ).all()
                current = {
                    product.name: (product.lifecycle_status, version.status)
                    for product, version in rows
                }
                assert all(current[name] == ("active", "approved") for name in SELECTED)
                assert all(current[name] == ("draft", "draft") for name in REMAINING)
                assert current["CPTAC-BRCA"] == ("archived", "draft")
                assert (
                    int(
                        await session.scalar(
                            select(func.count()).select_from(Application)
                        )
                        or 0
                    )
                    == applications_before
                )
                assert (
                    int(
                        await session.scalar(
                            select(func.count()).select_from(ComputeJob)
                        )
                        or 0
                    )
                    == jobs_before
                )
                event_counts = dict(
                    (
                        await session.execute(
                            select(AuditEvent.event_type, func.count())
                            .where(
                                AuditEvent.event_type.in_(
                                    (
                                        "external_catalog.product.submitted",
                                        "external_catalog.product.published",
                                    )
                                )
                            )
                            .group_by(AuditEvent.event_type)
                        )
                    ).all()
                )
                assert event_counts == {
                    "external_catalog.product.published": 3,
                    "external_catalog.product.submitted": 3,
                }
                for name in SELECTED:
                    with pytest.raises(
                        ExternalDataProductEligibilityError,
                        match=DATA_PRODUCT_NOT_MATERIALIZED,
                    ):
                        await require_materialized_data_product(
                            session, products[name]["version_id"]
                        )
        finally:
            await verify_engine.dispose()

    asyncio.run(verify_final())
