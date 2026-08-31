from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.session import get_db_session
from app.main import create_app
from app.modules.compute.models import (
    Artifact,
    ComputeJob,
    ComputeRun,
    ExecutionEligibilitySnapshot,
)
from app.modules.contracts.models import Contract
from app.modules.contracts.services import canonical_document_digest
from tests.integration.test_phase5_application_lifecycle_postgresql import (
    _application_payload,
    _prepare_published_options,
    _queue_item,
    _review_payload,
)


DATABASE_URL = os.getenv("MEDTRUST_PHASE5_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE5_TEST_DATABASE_URL is not configured",
    ),
]


def _headers(identity: str, key: str | None = None) -> dict[str, str]:
    headers = {"X-Demo-Identity": identity}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _create_active_contract(client: TestClient, suffix: str) -> dict:
    requester = _headers("data_requester")
    operator = _headers("space_operator")
    hospital = _headers("data_provider")
    model_provider = _headers("model_provider")
    options = client.get("/api/v1/application-options", headers=requester)
    assert options.status_code == 200
    payload = _application_payload(
        options.json()["sample"]["data_version_id"],
        options.json()["sample"]["model_version_id"],
        suffix,
    )
    created = client.post(
        "/api/v1/application-drafts",
        headers=_headers("data_requester", f"phase55-app-create-{suffix}"),
        json=payload,
    )
    assert created.status_code == 201
    application_id = created.json()["application_id"]
    checked = client.post(
        f"/api/v1/application-drafts/{application_id}/compatibility",
        headers=_headers("data_requester", f"phase55-app-check-{suffix}"),
    )
    assert checked.status_code == 200
    submitted = client.post(
        f"/api/v1/application-drafts/{application_id}/submit",
        headers=_headers("data_requester", f"phase55-app-submit-{suffix}"),
        json={"warnings_acknowledged": True},
    )
    assert submitted.status_code == 200
    for identity, role_headers in (
        ("space_operator", operator),
        ("data_provider", hospital),
        ("model_provider", model_provider),
    ):
        task = _queue_item(client, role_headers, application_id)
        decided = client.post(
            f"/api/v1/application-review-tasks/{task['task_id']}/decide",
            headers=_headers(identity, f"phase55-review-{identity}-{suffix}"),
            json=_review_payload(),
        )
        assert decided.status_code == 200
    contract = client.post(
        f"/api/v1/applications/{application_id}/contract",
        headers=_headers("space_operator", f"phase55-contract-{suffix}"),
    )
    assert contract.status_code == 200
    detail = contract.json()
    confirmation = {
        "contract_revision_id": detail["revision_id"],
        "content_digest": detail["content_digest"],
        "declaration_accepted": True,
    }
    for identity in ("data_requester", "data_provider", "model_provider"):
        response = client.post(
            f"/api/v1/digital-contracts/{detail['contract_id']}/confirm",
            headers=_headers(identity, f"phase55-sign-{identity}-{suffix}"),
            json=confirmation,
        )
        assert response.status_code == 200, response.text
    signed = client.post(
        f"/api/v1/digital-contracts/{detail['contract_id']}/confirm",
        headers=_headers("space_operator", f"phase55-sign-operator-{suffix}"),
        json=confirmation,
    )
    assert signed.status_code == 200
    activated = client.post(
        f"/api/v1/digital-contracts/{detail['contract_id']}/activate",
        headers=_headers("space_operator", f"phase55-activate-{suffix}"),
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    return detail


def _prepare_eligibility(client: TestClient, contract_id: str, suffix: str) -> dict:
    readiness_body = {
        "declaration_accepted": True,
        "confirmation_note": "Locked asset and provider-controlled execution boundary verified.",
    }
    denied = client.post(
        f"/api/v1/execution-readiness/{contract_id}/data-readiness",
        headers=_headers("data_requester", f"phase55-denied-data-{suffix}"),
        json=readiness_body,
    )
    assert denied.status_code == 403
    data_key = f"phase55-data-ready-{suffix}"
    data_ready = client.post(
        f"/api/v1/execution-readiness/{contract_id}/data-readiness",
        headers=_headers("data_provider", data_key),
        json=readiness_body,
    )
    data_replay = client.post(
        f"/api/v1/execution-readiness/{contract_id}/data-readiness",
        headers=_headers("data_provider", data_key),
        json=readiness_body,
    )
    assert data_ready.status_code == data_replay.status_code == 200, (
        data_ready.text,
        data_replay.text,
    )
    data_readiness = data_ready.json()["readiness"]["data_ready"]
    data_security_reference = data_readiness["target"]["contract_security"]
    assert data_security_reference["snapshot_digest"] == data_readiness[
        "evidence"
    ]["contract_security_validation"]["snapshot_digest"]
    assert (
        data_ready.json()["readiness"]["data_ready"]["id"]
        == data_replay.json()["readiness"]["data_ready"]["id"]
    )
    blocked = client.post(
        f"/api/v1/execution-readiness/{contract_id}/eligibility-check",
        headers=_headers("space_operator", f"phase55-blocked-{suffix}"),
    )
    assert blocked.status_code == 200
    assert blocked.json()["snapshot_id"] is None
    assert blocked.json()["report"]["overall"] == "BLOCKER"
    model_ready = client.post(
        f"/api/v1/execution-readiness/{contract_id}/model-readiness",
        headers=_headers("model_provider", f"phase55-model-ready-{suffix}"),
        json=readiness_body,
    )
    assert model_ready.status_code == 200
    model_readiness = model_ready.json()["readiness"]["model_ready"]
    model_security_reference = model_readiness["target"]["contract_security"]
    assert model_security_reference["snapshot_digest"] == model_readiness[
        "evidence"
    ]["contract_security_validation"]["snapshot_digest"]
    eligibility_key = f"phase55-eligibility-{suffix}"
    eligibility = client.post(
        f"/api/v1/execution-readiness/{contract_id}/eligibility-check",
        headers=_headers("space_operator", eligibility_key),
    )
    replay = client.post(
        f"/api/v1/execution-readiness/{contract_id}/eligibility-check",
        headers=_headers("space_operator", eligibility_key),
    )
    assert eligibility.status_code == replay.status_code == 200
    assert eligibility.json()["snapshot_id"] == replay.json()["snapshot_id"]
    assert eligibility.json()["report"]["overall"] == "WARNING", eligibility.json()[
        "report"
    ]
    results = {
        item["code"]: item["result"]
        for item in eligibility.json()["report"]["checks"]
    }
    assert results["hard_isolation"] == "WARNING"
    assert all(
        result == "PASS"
        for code, result in results.items()
        if code != "hard_isolation"
    )
    security_digest = eligibility.json()["report"][
        "contract_security_snapshot_digest"
    ]
    assert security_digest.startswith("sha256:")
    return eligibility.json()


def test_phase5_execution_readiness_job_creation_and_concurrency() -> None:
    assert DATABASE_URL is not None
    asyncio.run(_prepare_published_options(DATABASE_URL))
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session():
        async with factory() as session:
            yield session

    app = create_app(
        Settings(app_env="test", database_url=DATABASE_URL, demo_api_enabled=True)
    )
    app.dependency_overrides[get_db_session] = test_session
    suffix_a = uuid4().hex[:10]
    suffix_b = uuid4().hex[:10]
    try:
        with TestClient(app) as client:
            contract_a = _create_active_contract(client, suffix_a)
            contract_b = _create_active_contract(client, suffix_b)
            eligible_a = _prepare_eligibility(
                client, contract_a["contract_id"], suffix_a
            )
            eligible_b = _prepare_eligibility(
                client, contract_b["contract_id"], suffix_b
            )

            denied_job = client.post(
                f"/api/v1/execution-readiness/{contract_a['contract_id']}/jobs",
                headers=_headers("data_provider", f"phase55-denied-job-{suffix_a}"),
                json={"eligibility_snapshot_id": eligible_a["snapshot_id"]},
            )
            assert denied_job.status_code == 403

            job_key = f"phase55-job-{suffix_a}"
            created = client.post(
                f"/api/v1/execution-readiness/{contract_a['contract_id']}/jobs",
                headers=_headers("data_requester", job_key),
                json={"eligibility_snapshot_id": eligible_a["snapshot_id"]},
            )
            replay = client.post(
                f"/api/v1/execution-readiness/{contract_a['contract_id']}/jobs",
                headers=_headers("data_requester", job_key),
                json={"eligibility_snapshot_id": eligible_a["snapshot_id"]},
            )
            assert created.status_code == replay.status_code == 201
            assert created.json()["job_id"] == replay.json()["job_id"]
            assert created.json()["compute_run_count"] == 0
            assert created.json()["artifact_count"] == 0

            readiness_id = created.json()["detail"]["readiness"]["data_ready"]["id"]
            late_revoke = client.post(
                f"/api/v1/execution-readiness/readiness/{readiness_id}/revoke",
                headers=_headers("data_provider", f"phase55-late-revoke-{suffix_a}"),
                json={"reason_code": "provider_withdrawn_after_job"},
            )
            assert late_revoke.status_code == 409

            def create_competing_job(key: str):
                return client.post(
                    f"/api/v1/execution-readiness/{contract_b['contract_id']}/jobs",
                    headers=_headers("data_requester", key),
                    json={"eligibility_snapshot_id": eligible_b["snapshot_id"]},
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(
                    executor.map(
                        create_competing_job,
                        (
                            f"phase55-race-a-{suffix_b}",
                            f"phase55-race-b-{suffix_b}",
                        ),
                    )
                )
            assert sorted(response.status_code for response in responses) == [201, 409]

            for contract in (contract_a, contract_b):
                expected_eligibility = (
                    eligible_a
                    if contract["contract_id"] == contract_a["contract_id"]
                    else eligible_b
                )
                detail = client.get(
                    f"/api/v1/execution-readiness/{contract['contract_id']}",
                    headers=_headers("data_requester"),
                )
                assert detail.status_code == 200
                assert len(detail.json()["jobs"]) == 1
                assert detail.json()["jobs"][0]["status"] == "created"
                assert detail.json()["jobs"][0]["compute_run_created"] is False
                assert detail.json()["jobs"][0]["artifact_created"] is False
                events = client.get(
                    f"/api/v1/execution-readiness/{contract['contract_id']}/audit-events",
                    headers=_headers("data_requester"),
                )
                assert events.status_code == 200
                event_rows = events.json()["items"]
                event_types = [item["event_type"] for item in event_rows]
                assert "execution.eligibility.passed" in event_types
                assert "compute.job.created" in event_types
                assert "compute.job.pre_dispatch_slot_reserved" in event_types
                readiness_evidence_digests = {
                    item["evidence"]["evidence_digest"]
                    for item in event_rows
                    if item["event_type"] == "contract.readiness.confirmed"
                }
                for readiness_type in (
                    "data_ready",
                    "model_ready",
                    "platform_ready",
                ):
                    readiness = detail.json()["readiness"][readiness_type]
                    assert readiness["evidence_digest"] in readiness_evidence_digests
                    assert readiness["target"]["contract_security"][
                        "snapshot_digest"
                    ].startswith("sha256:")
                eligibility_event = next(
                    item
                    for item in event_rows
                    if item["event_type"] == "execution.eligibility.passed"
                )
                assert eligibility_event["evidence"][
                    "contract_security_validation"
                ]["snapshot_digest"] == expected_eligibility["report"][
                    "contract_security_snapshot_digest"
                ]

        async def verify_database() -> None:
            verify_engine = create_async_engine(DATABASE_URL)
            verify_factory = async_sessionmaker(verify_engine, expire_on_commit=False)
            try:
                async with verify_factory() as session:
                    contract_ids = [
                        contract_a["contract_id"],
                        contract_b["contract_id"],
                    ]
                    assert (
                        await session.scalar(
                            select(func.count(ComputeJob.id)).where(
                                ComputeJob.contract_id.in_(contract_ids)
                            )
                        )
                        == 2
                    )
                    assert (
                        await session.scalar(
                            select(func.count(ComputeRun.id)).where(
                                ComputeRun.contract_id.in_(contract_ids)
                            )
                        )
                        == 0
                    )
                    assert (
                        await session.scalar(
                            select(func.count(Artifact.id))
                            .join(
                                ComputeRun,
                                ComputeRun.id == Artifact.compute_run_id,
                            )
                            .where(ComputeRun.contract_id.in_(contract_ids))
                        )
                        == 0
                    )
                    assert (
                        await session.scalar(
                            select(func.count(ExecutionEligibilitySnapshot.id)).where(
                                ExecutionEligibilitySnapshot.contract_id.in_(contract_ids)
                            )
                        )
                        == 2
                    )
                    snapshots = list(
                        (
                            await session.scalars(
                                select(ExecutionEligibilitySnapshot).where(
                                    ExecutionEligibilitySnapshot.contract_id.in_(
                                        contract_ids
                                    )
                                )
                            )
                        ).all()
                    )
                    for snapshot in snapshots:
                        security_digest = snapshot.eligibility_snapshot[
                            "contract_security_snapshot_digest"
                        ]
                        assert security_digest.startswith("sha256:")
                        assert canonical_document_digest(
                            snapshot.eligibility_snapshot
                        ) == snapshot.eligibility_snapshot_digest
                    space_id = await session.scalar(
                        select(Contract.space_id).where(
                            Contract.id == contract_ids[0]
                        )
                    )
                    chain = (
                        await session.execute(
                            text(
                                "SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"
                            ),
                            {"space_id": space_id},
                        )
                    ).one()
                    assert bool(chain.is_valid) is True
            finally:
                await verify_engine.dispose()

        asyncio.run(verify_database())
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
