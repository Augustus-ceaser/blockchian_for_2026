from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.api.routes import external_catalog, external_model_catalog


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _DatasetSession:
    def __init__(
        self,
        records: list[SimpleNamespace],
        publications: list[tuple[UUID, UUID]],
    ) -> None:
        self.records = records
        self.publications = publications
        self.execute_calls = 0
        self.execute_statements: list[object] = []
        self.scalar_statements: list[object] = []

    async def scalars(self, statement: object) -> _Rows:
        self.scalar_statements.append(statement)
        return _Rows(self.records)

    async def execute(self, statement: object) -> _Rows:
        self.execute_calls += 1
        self.execute_statements.append(statement)
        return _Rows(self.publications)


class _ModelSession(_DatasetSession):
    async def scalar(self, _statement: object) -> int:
        return len(self.records)


def _dataset(name: str) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        external_id=f"external-{name}",
        canonical_name=name,
        display_name_cn=None,
        display_name_en=name,
        source_catalog="public-dataset-catalog",
        modalities=["CT"],
        disease_areas=["fracture"],
        organs=["bone"],
        sample_count=20,
        patient_count=20,
        approximate_size_bytes=1024,
        license_name="research",
        license_status="research_only",
        access_level="open_download",
        link_status="verified",
        quality_flags=[],
        duplicate_group_id=None,
        first_seen_at=now,
        last_seen_at=now,
        status="active",
    )


def _model(name: str) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        external_model_id=f"external-{name}",
        canonical_name=name,
        display_name_cn=None,
        display_name_en=name,
        model_categories=["classification"],
        modalities=["CT"],
        task_types=["image_classification"],
        disease_areas=["fracture"],
        organs=["bone"],
        framework="pytorch",
        license_name="research",
        license_status="research_only",
        access_status="open",
        weights_status="public_available",
        estimated_weights_size_bytes=2048,
        revision="v1",
        gated=False,
        execution_status="not_materialized",
        quality_flags=[],
        status="active",
        first_seen_at=now,
        last_seen_at=now,
    )


async def _context(*_args: object, **_kwargs: object):
    return SimpleNamespace(space_id=uuid4()), SimpleNamespace()


def test_dataset_page_marks_only_active_published_product_versions(monkeypatch) -> None:
    published = _dataset("published")
    candidate = _dataset("candidate")
    version_id = uuid4()
    session = _DatasetSession(
        [published, candidate],
        [(published.id, version_id)],
    )
    monkeypatch.setattr(external_catalog, "_actor", _context)

    result = asyncio.run(
        external_catalog.list_datasets(
            identity="data_requester",
            q=None,
            modality=None,
            disease=None,
            disease_or_organ=None,
            license_status=None,
            quality_flag=None,
            status="active",
            offset=0,
            limit=50,
            session=session,
        )
    )

    assert result["total"] == 2
    assert result["offset"] == 0
    assert result["limit"] == 50
    assert result["items"][0]["published_product_version_id"] == str(version_id)
    assert result["items"][1]["published_product_version_id"] is None
    assert session.execute_calls == 1
    projection_sql = str(session.execute_statements[0])
    assert "data_product_publications.space_id" in projection_sql
    assert "data_product_publications.status" in projection_sql
    catalog_sql = str(session.scalar_statements[0])
    assert "external_dataset_records.source_id" in catalog_sql
    assert "external_dataset_records.id" in catalog_sql


def test_model_page_marks_only_active_published_product_versions(monkeypatch) -> None:
    published = _model("published")
    candidate = _model("candidate")
    version_id = uuid4()
    session = _ModelSession(
        [published, candidate],
        [(published.id, version_id)],
    )
    monkeypatch.setattr(external_model_catalog, "_actor", _context)

    result = asyncio.run(
        external_model_catalog.list_models(
            identity="data_requester",
            q=None,
            category=None,
            weights_status=None,
            limit=25,
            offset=0,
            session=session,
        )
    )

    assert result["total"] == 2
    assert result["items"][0]["published_product_version_id"] == str(version_id)
    assert result["items"][1]["published_product_version_id"] is None
    assert session.execute_calls == 1
    projection_sql = str(session.execute_statements[0])
    assert "model_publications.space_id" in projection_sql
    assert "model_publications.status" in projection_sql
    catalog_sql = str(session.scalar_statements[0])
    assert "external_model_records.source_id" in catalog_sql
    assert "external_model_records.id" in catalog_sql
