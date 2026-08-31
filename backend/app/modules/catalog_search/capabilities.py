from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)
RetrievalMode = Literal["structured", "lexical", "hybrid"]


@dataclass(frozen=True)
class CatalogRetrievalCapabilities:
    semantic_requested: bool
    dialect: str
    vector_extension: bool = False
    embedding_index: bool = False
    ready_embeddings: bool = False

    @property
    def mode(self) -> RetrievalMode:
        if (
            self.semantic_requested
            and self.dialect == "postgresql"
            and self.vector_extension
            and self.embedding_index
            and self.ready_embeddings
        ):
            return "hybrid"
        return "lexical"


async def detect_catalog_retrieval_capabilities(
    *,
    session: object,
    semantic_enabled: bool,
) -> CatalogRetrievalCapabilities:
    if not semantic_enabled or not isinstance(session, AsyncSession):
        return CatalogRetrievalCapabilities(
            semantic_requested=semantic_enabled,
            dialect="unavailable",
        )

    dialect = session.get_bind().dialect.name
    if dialect != "postgresql":
        return CatalogRetrievalCapabilities(
            semantic_requested=True,
            dialect=dialect,
        )

    transaction = None
    try:
        transaction = await session.begin_nested()
        extension_and_table = (
            await session.execute(
                text(
                    """
                    SELECT
                      EXISTS (
                        SELECT 1 FROM pg_extension WHERE extname = 'vector'
                      ) AS vector_extension,
                      to_regclass('medtrust.catalog_search_embeddings') IS NOT NULL
                        AS embedding_index
                    """
                )
            )
        ).one()
        vector_extension = bool(extension_and_table.vector_extension)
        embedding_index = bool(extension_and_table.embedding_index)
        ready_embeddings = False
        if vector_extension and embedding_index:
            ready_embeddings = bool(
                await session.scalar(
                    text(
                        """
                        SELECT EXISTS (
                          SELECT 1
                          FROM medtrust.catalog_search_embeddings
                          WHERE status = 'ready'
                        )
                        """
                    )
                )
            )
        return CatalogRetrievalCapabilities(
            semantic_requested=True,
            dialect=dialect,
            vector_extension=vector_extension,
            embedding_index=embedding_index,
            ready_embeddings=ready_embeddings,
        )
    except SQLAlchemyError as exc:
        logger.warning(
            "Catalog semantic capability detection failed: error_type=%s",
            type(exc).__name__,
        )
        return CatalogRetrievalCapabilities(
            semantic_requested=True,
            dialect=dialect,
        )
    finally:
        if transaction is not None and transaction.is_active:
            await transaction.rollback()
