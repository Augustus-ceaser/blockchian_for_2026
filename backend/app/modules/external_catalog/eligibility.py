from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ModelProductExternalSourceLink,
)


DATA_PRODUCT_NOT_MATERIALIZED = "DATA_PRODUCT_NOT_MATERIALIZED"
MODEL_PRODUCT_NOT_MATERIALIZED = "MODEL_PRODUCT_NOT_MATERIALIZED"


class ExternalDataProductEligibilityError(ValueError):
    pass


class ExternalModelProductEligibilityError(ValueError):
    pass


async def external_source_link_for_version(
    session: AsyncSession, version_id: UUID
) -> DataProductExternalSourceLink | None:
    return await session.scalar(
        select(DataProductExternalSourceLink).where(
            DataProductExternalSourceLink.data_product_version_id == version_id
        )
    )


async def require_materialized_data_product(
    session: AsyncSession, version_id: UUID
) -> None:
    link = await external_source_link_for_version(session, version_id)
    if link is None:
        return
    if (
        link.materialization_status != "materialized"
        or link.execution_readiness != "ready"
    ):
        raise ExternalDataProductEligibilityError(DATA_PRODUCT_NOT_MATERIALIZED)


async def require_materialized_model_product(
    session: AsyncSession, version_id: UUID
) -> None:
    link = await session.scalar(
        select(ModelProductExternalSourceLink).where(
            ModelProductExternalSourceLink.model_version_id == version_id
        )
    )
    if link is None:
        return
    if (
        link.materialization_status != "materialized"
        or link.execution_readiness != "ready"
        or link.platform_validation != "validated"
    ):
        raise ExternalModelProductEligibilityError(MODEL_PRODUCT_NOT_MATERIALIZED)
