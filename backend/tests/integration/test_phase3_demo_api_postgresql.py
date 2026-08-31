from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.session import get_db_session
from app.main import create_app


DEMO_DATABASE_URL = os.getenv("MEDTRUST_PHASE3_DEMO_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DEMO_DATABASE_URL,
        reason="MEDTRUST_PHASE3_DEMO_DATABASE_URL is not configured",
    ),
]


def test_phase3_demo_queries_and_idempotent_run_command() -> None:
    assert DEMO_DATABASE_URL is not None
    engine = create_async_engine(DEMO_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def demo_session():
        async with factory() as session:
            yield session

    app = create_app(
        Settings(
            app_env="test",
            database_url=DEMO_DATABASE_URL,
            demo_api_enabled=True,
        )
    )
    app.dependency_overrides[get_db_session] = demo_session
    idempotency_key = f"phase3-api-test-{uuid4().hex}"
    try:
        with TestClient(app) as client:
            overview = client.get("/api/v1/overview")
            assert overview.status_code == 200
            assert overview.json()["capability"]["hard_isolation"] is False
            assert overview.json()["verified_baseline_metrics"]["sample_count"] == 20

            for path in (
                "/api/v1/data-products",
                "/api/v1/applications",
                "/api/v1/contracts",
                "/api/v1/compute-jobs",
                "/api/v1/audit-events",
                "/api/v1/connectors",
            ):
                response = client.get(path)
                assert response.status_code == 200, path
                assert response.json()["capability"]["demo"] is True

            headers = {
                "Idempotency-Key": idempotency_key,
                "X-Demo-Role": "ai_company",
            }
            first = client.post(
                "/api/v1/demo/pathmnist/runs", headers=headers, json={}
            )
            second = client.post(
                "/api/v1/demo/pathmnist/runs", headers=headers, json={}
            )
            assert first.status_code == 202
            assert second.status_code == 202
            assert first.json()["job_id"] == second.json()["job_id"]
            assert first.json()["run_id"] == second.json()["run_id"]
            assert first.json()["run_count"]["ordinal"] >= 1
            assert second.json()["replayed"] is True

            run = client.get(first.json()["status_url"])
            assert run.status_code == 200
            assert run.json()["id"] == first.json()["run_id"]
            assert "execution_environment_snapshot" not in run.json()
    finally:
        app.dependency_overrides.clear()
