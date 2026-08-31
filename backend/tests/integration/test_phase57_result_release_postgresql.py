from __future__ import annotations

import asyncio
import hashlib
import io
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.session import get_db_session
from app.main import create_app
from app.modules.audit.models import AuditEvent
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.marketplace.models import (
    SAFE_RESULT_FILENAMES,
    ApprovedResultPackage,
    ArtifactReviewTask,
    ResultDownloadGrant,
)


DATABASE_URL = os.getenv("MEDTRUST_PHASE57_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE57_TEST_DATABASE_URL is not configured",
    ),
]


def _headers(identity: str, key: str | None = None) -> dict[str, str]:
    headers = {"X-Demo-Identity": identity}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _approval_payload() -> dict[str, object]:
    return {
        "decision": "approved",
        "reason_code": "phase57_verified",
        "comment": "Contract scope, aggregate outputs, digests and allowlist verified.",
        "purpose_and_scope_match": True,
        "aggregate_only": True,
        "no_patient_level_data": True,
        "no_reidentification_risk": True,
        "digest_verified": True,
        "schema_verified": True,
        "allowlist_verified": True,
        "approved_files": list(SAFE_RESULT_FILENAMES),
        "additional_conditions": "",
    }


def _decide(
    client: TestClient,
    *,
    artifact_id: str,
    review_type: str,
    identity: str,
    suffix: str,
) -> None:
    detail = client.get(
        f"/api/v1/result-artifacts/{artifact_id}",
        headers=_headers(identity),
    )
    assert detail.status_code == 200
    task = next(
        item
        for item in detail.json()["reviews"]
        if item["review_type"] == review_type
    )
    response = client.post(
        f"/api/v1/result-review-tasks/{task['task_id']}/decide",
        headers=_headers(identity, f"phase57-{review_type}-{suffix}"),
        json=_approval_payload(),
    )
    assert response.status_code == 200, response.text


def test_phase57_controlled_result_release_and_one_time_download() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session():
        async with factory() as session:
            yield session

    app = create_app(
        Settings(
            app_env="test",
            database_url=DATABASE_URL,
            demo_api_enabled=True,
            minio_release_bucket="medtrust-phase57-test-results",
            minio_quarantine_bucket="medtrust-phase56-quarantined-results",
        )
    )
    app.dependency_overrides[get_db_session] = test_session
    suffix = uuid4().hex[:10]

    async def baseline() -> tuple[int, int, list[str]]:
        baseline_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
        baseline_factory = async_sessionmaker(
            baseline_engine, expire_on_commit=False
        )
        try:
            async with baseline_factory() as session:
                assert await session.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "20260725_0032"
                jobs = await session.scalar(select(func.count(ComputeJob.id)))
                runs = await session.scalar(select(func.count(ComputeRun.id)))
                artifacts = list(
                    (
                        await session.scalars(
                            select(Artifact)
                            .where(Artifact.release_status == "quarantined")
                            .order_by(Artifact.created_at)
                        )
                    ).all()
                )
                assert len(artifacts) == 2
                return int(jobs or 0), int(runs or 0), [
                    str(item.id) for item in artifacts
                ]
        finally:
            await baseline_engine.dispose()

    try:
        job_count, run_count, artifact_ids = asyncio.run(baseline())
        with TestClient(app) as client:
            assert "/api/v1/artifacts/{artifact_id}/download" not in app.openapi()[
                "paths"
            ]
            packages: list[tuple[str, str]] = []
            tokens: list[tuple[str, str]] = []
            for index, artifact_id in enumerate(artifact_ids):
                plan = client.post(
                    f"/api/v1/result-artifacts/{artifact_id}/review-plan",
                    headers=_headers(
                        "space_operator", f"phase57-plan-{index}-{suffix}"
                    ),
                )
                assert plan.status_code == 200, plan.text
                assert {
                    item["review_type"] for item in plan.json()["items"]
                } == {
                    "data_provider_egress_review",
                    "model_provider_quality_review",
                    "platform_compliance_review",
                }
                assert all(item["required"] for item in plan.json()["items"])

                platform_task = next(
                    item
                    for item in plan.json()["items"]
                    if item["review_type"] == "platform_compliance_review"
                )
                early = client.post(
                    f"/api/v1/result-review-tasks/{platform_task['task_id']}/decide",
                    headers=_headers(
                        "space_operator", f"phase57-platform-early-{index}-{suffix}"
                    ),
                    json=_approval_payload(),
                )
                assert early.status_code == 409
                assert "must be last" in early.text

                hospital_task = next(
                    item
                    for item in plan.json()["items"]
                    if item["review_type"] == "data_provider_egress_review"
                )
                unauthorized = client.post(
                    f"/api/v1/result-review-tasks/{hospital_task['task_id']}/decide",
                    headers=_headers(
                        "data_requester",
                        f"phase57-unauthorized-{index}-{suffix}",
                    ),
                    json=_approval_payload(),
                )
                assert unauthorized.status_code == 403

                _decide(
                    client,
                    artifact_id=artifact_id,
                    review_type="data_provider_egress_review",
                    identity="data_provider",
                    suffix=f"{index}-{suffix}",
                )
                _decide(
                    client,
                    artifact_id=artifact_id,
                    review_type="model_provider_quality_review",
                    identity="model_provider",
                    suffix=f"{index}-{suffix}",
                )
                _decide(
                    client,
                    artifact_id=artifact_id,
                    review_type="platform_compliance_review",
                    identity="space_operator",
                    suffix=f"{index}-{suffix}",
                )

                package = client.post(
                    f"/api/v1/result-artifacts/{artifact_id}/package",
                    headers=_headers(
                        "space_operator", f"phase57-package-{index}-{suffix}"
                    ),
                )
                assert package.status_code == 200, package.text
                assert {
                    item["name"] for item in package.json()["files"]
                } == set(SAFE_RESULT_FILENAMES)
                packages.append((artifact_id, package.json()["package_id"]))

                grant = client.post(
                    f"/api/v1/result-packages/{package.json()['package_id']}/download-grants",
                    headers=_headers(
                        "data_requester", f"phase57-grant-{index}-{suffix}"
                    ),
                    json={"lifetime_seconds": 300},
                )
                assert grant.status_code == 200, grant.text
                tokens.append((grant.json()["grant_id"], grant.json()["token"]))

            def download(token: str, key: str):
                return client.post(
                    "/api/v1/result-downloads",
                    headers={
                        **_headers("data_requester", key),
                        "X-Download-Token": token,
                    },
                )

            concurrent_key = f"phase57-download-race-{suffix}"
            with ThreadPoolExecutor(max_workers=2) as executor:
                raced = list(
                    executor.map(
                        lambda _: download(tokens[0][1], concurrent_key),
                        range(2),
                    )
                )
            assert sorted(item.status_code for item in raced) == [200, 409]
            first_payload = next(item.content for item in raced if item.status_code == 200)
            with zipfile.ZipFile(io.BytesIO(first_payload)) as archive:
                assert archive.namelist() == sorted(SAFE_RESULT_FILENAMES)

            first = download(tokens[1][1], f"phase57-download-1-{suffix}")
            second = download(tokens[1][1], f"phase57-download-1-{suffix}")
            assert first.status_code == 200
            assert second.status_code == 409
            assert "invalid, expired or exhausted" in second.text
            with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
                assert archive.namelist() == sorted(SAFE_RESULT_FILENAMES)

            for artifact_id, _ in packages:
                detail = client.get(
                    f"/api/v1/result-artifacts/{artifact_id}",
                    headers=_headers("data_requester"),
                )
                assert detail.status_code == 200
                assert detail.json()["artifact_status"] == "quarantined"
                assert detail.json()["raw_artifact_download_allowed"] is False
                assert detail.json()["hard_isolation"] is False

        async def verify() -> None:
            verify_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
            verify_factory = async_sessionmaker(
                verify_engine, expire_on_commit=False
            )
            try:
                async with verify_factory() as session:
                    assert await session.scalar(
                        select(func.count(ComputeJob.id))
                    ) == job_count
                    assert await session.scalar(
                        select(func.count(ComputeRun.id))
                    ) == run_count
                    assert await session.scalar(
                        select(func.count(Artifact.id)).where(
                            Artifact.release_status == "quarantined"
                        )
                    ) == 2
                    assert await session.scalar(
                        select(func.count(ArtifactReviewTask.id))
                    ) == 6
                    assert await session.scalar(
                        select(func.count(ApprovedResultPackage.id))
                    ) == 2
                    assert await session.scalar(
                        select(func.count(ResultDownloadGrant.id))
                    ) == 2
                    grants = list(
                        (await session.scalars(select(ResultDownloadGrant))).all()
                    )
                    assert all(item.status == "exhausted" for item in grants)
                    assert all(item.download_count == 1 for item in grants)
                    expected_token_digests = {
                        f"sha256:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"
                        for _, token in tokens
                    }
                    assert {item.token_digest for item in grants} == (
                        expected_token_digests
                    )
                    assert all(
                        item.token_digest not in {token for _, token in tokens}
                        for item in grants
                    )
                    assert await session.scalar(
                        select(func.count(AuditEvent.event_id)).where(
                            AuditEvent.event_type == "result.download.completed"
                        )
                    ) == 2
                    assert await session.scalar(
                        select(func.count(AuditEvent.event_id)).where(
                            AuditEvent.event_type == "result.download.rejected"
                        )
                    ) == 2
                    chain = (
                        await session.execute(
                            text(
                                "SELECT * FROM medtrust.verify_audit_space_chain_v1("
                                "(SELECT space_id FROM medtrust.artifacts LIMIT 1))"
                            )
                        )
                    ).mappings().one()
                    assert chain["is_valid"] is True
            finally:
                await verify_engine.dispose()

        asyncio.run(verify())
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
