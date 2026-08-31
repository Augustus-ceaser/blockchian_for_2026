from __future__ import annotations

import asyncio
from copy import deepcopy
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.demo.phase4 import ensure_phase4_demo_initial
from app.demo.service_market import (
    SERVICE_MARKET_DATA_PRODUCT_CODE,
    SERVICE_MARKET_MODEL_PRODUCT_CODE,
    ensure_phase4_service_market_products,
)
from app.modules.commerce.pricing import FROZEN_DEMO_PRICE_PLAN_VERSION
from app.modules.commerce.services import commercial_offers_for_version
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
)
from app.modules.marketplace.models import (
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.service_modes import (
    CONTROLLED_COMPUTE,
    DEIDENTIFIED_DATA_DELIVERY,
    MODEL_ARTIFACT_LICENSE,
    resolve_service_modes,
)


DATABASE_URL = os.getenv("MEDTRUST_PHASE5_TEST_DATABASE_URL")
WORKSPACE = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE5_TEST_DATABASE_URL is not configured",
    ),
]


def test_service_market_seed_is_idempotent_and_preserves_source_versions() -> None:
    assert DATABASE_URL is not None

    async def exercise() -> None:
        engine = create_async_engine(DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async with session.begin():
                    context = await ensure_phase4_demo_initial(
                        session,
                        workspace=WORKSPACE,
                    )
                    source_data = await session.get(
                        DataProductVersion, context.data_version_id
                    )
                    source_model = await session.get(
                        ModelVersion, context.model_version_id
                    )
                    assert source_data is not None
                    assert source_model is not None
                    source_data_before = (
                        source_data.status,
                        source_data.default_policy_digest,
                        source_data.snapshot_digest,
                        deepcopy(source_data.default_policy_template),
                    )
                    source_model_before = (
                        source_model.status,
                        source_model.default_policy_digest,
                        source_model.snapshot_digest,
                        deepcopy(source_model.default_policy_template),
                    )

                    first = await ensure_phase4_service_market_products(
                        session,
                        context,
                        workspace=WORKSPACE,
                    )
                    second = await ensure_phase4_service_market_products(
                        session,
                        context,
                        workspace=WORKSPACE,
                    )

                    assert first.data_product_id == second.data_product_id
                    assert first.data_version_id == second.data_version_id
                    assert first.model_product_id == second.model_product_id
                    assert first.model_version_id == second.model_version_id
                    assert second.data_created is False
                    assert second.model_created is False

                    data_version = await session.get(
                        DataProductVersion, first.data_version_id
                    )
                    model_version = await session.get(
                        ModelVersion, first.model_version_id
                    )
                    assert data_version is not None
                    assert model_version is not None
                    assert data_version.status == "approved"
                    assert model_version.status == "approved"
                    assert resolve_service_modes(
                        "data", data_version.default_policy_template
                    ) == (CONTROLLED_COMPUTE, DEIDENTIFIED_DATA_DELIVERY)
                    assert resolve_service_modes(
                        "model", model_version.default_policy_template
                    ) == (CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE)
                    assert "commercial_offer" not in data_version.default_policy_template
                    assert "commercial_offer" not in model_version.default_policy_template

                    data_offers = await commercial_offers_for_version(
                        session,
                        space_id=context.space_id,
                        product_kind="data",
                        version_id=data_version.id,
                    )
                    model_offers = await commercial_offers_for_version(
                        session,
                        space_id=context.space_id,
                        product_kind="model",
                        version_id=model_version.id,
                    )
                    assert {item["service_mode"] for item in data_offers} == {
                        CONTROLLED_COMPUTE,
                        DEIDENTIFIED_DATA_DELIVERY,
                    }
                    assert {item["service_mode"] for item in model_offers} == {
                        CONTROLLED_COMPUTE,
                        MODEL_ARTIFACT_LICENSE,
                    }
                    assert all(
                        item["pricing_source"] == "versioned_demo_price_plan"
                        and item["pricing_plan_version"]
                        == FROZEN_DEMO_PRICE_PLAN_VERSION
                        for item in [*data_offers, *model_offers]
                    )

                    assert await session.scalar(
                        select(func.count())
                        .select_from(DataProductPublication)
                        .where(
                            DataProductPublication.data_product_version_id
                            == data_version.id,
                            DataProductPublication.status == "active",
                        )
                    ) == 1
                    assert await session.scalar(
                        select(func.count())
                        .select_from(ModelPublication)
                        .where(
                            ModelPublication.model_version_id == model_version.id,
                            ModelPublication.status == "active",
                        )
                    ) == 1
                    assert await session.scalar(
                        select(func.count())
                        .select_from(DataProduct)
                        .where(
                            DataProduct.space_id == context.space_id,
                            DataProduct.product_code
                            == SERVICE_MARKET_DATA_PRODUCT_CODE,
                        )
                    ) == 1
                    assert await session.scalar(
                        select(func.count())
                        .select_from(ModelProduct)
                        .where(
                            ModelProduct.space_id == context.space_id,
                            ModelProduct.product_code
                            == SERVICE_MARKET_MODEL_PRODUCT_CODE,
                        )
                    ) == 1

                    assert source_data_before == (
                        source_data.status,
                        source_data.default_policy_digest,
                        source_data.snapshot_digest,
                        source_data.default_policy_template,
                    )
                    assert source_model_before == (
                        source_model.status,
                        source_model.default_policy_digest,
                        source_model.snapshot_digest,
                        source_model.default_policy_template,
                    )
        finally:
            await engine.dispose()

    asyncio.run(exercise())
