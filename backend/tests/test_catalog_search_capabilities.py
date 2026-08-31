from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.modules.catalog_search.capabilities import (
    CatalogRetrievalCapabilities,
    detect_catalog_retrieval_capabilities,
)


def test_hybrid_mode_requires_every_capability() -> None:
    assert CatalogRetrievalCapabilities(
        semantic_requested=True,
        dialect="postgresql",
        vector_extension=True,
        embedding_index=True,
        ready_embeddings=True,
    ).mode == "hybrid"
    assert CatalogRetrievalCapabilities(
        semantic_requested=True,
        dialect="postgresql",
        vector_extension=True,
        embedding_index=True,
        ready_embeddings=False,
    ).mode == "lexical"


def test_disabled_and_non_postgresql_backends_fall_back_to_lexical() -> None:
    asyncio.run(_assert_sqlite_fallback())


async def _assert_sqlite_fallback() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        disabled = await detect_catalog_retrieval_capabilities(
            session=session,
            semantic_enabled=False,
        )
        enabled = await detect_catalog_retrieval_capabilities(
            session=session,
            semantic_enabled=True,
        )
    assert disabled.mode == "lexical"
    assert enabled.dialect == "sqlite"
    assert enabled.mode == "lexical"
    await engine.dispose()
