from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.session import get_db_session
from app.main import create_app
from app.modules.compute import (
    ComputeInvariantError,
    ComputeJob,
    ComputeRun,
    evaluate_compute_authorization,
)
from app.modules.contracts.models import ContractRevision
from app.modules.contracts.security import validate_contract_security
from app.modules.marketplace.models import (
    ContractModelObject,
    ModelProduct,
    ModelVersion,
)
from tests.integration.test_phase5_application_lifecycle_postgresql import (
    _prepare_published_options,
)
from tests.integration.test_phase5_execution_readiness_postgresql import (
    _create_active_contract,
    _headers,
    _prepare_eligibility,
)


DATABASE_URL = os.getenv("MEDTRUST_PHASE5_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE5_TEST_DATABASE_URL is not configured",
    ),
]


def test_phase56_dispatch_inherits_the_jobs_reserved_quota_slot() -> None:
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
    suffix = uuid4().hex[:10]
    try:
        with TestClient(app) as client:
            contract = _create_active_contract(client, suffix)

            async def assert_future_window_is_stage_sensitive() -> None:
                helper_engine = create_async_engine(DATABASE_URL)
                helper_factory = async_sessionmaker(
                    helper_engine, expire_on_commit=False
                )
                try:
                    async with helper_factory() as session:
                        revision = await session.get(
                            ContractRevision, contract["revision_id"]
                        )
                        assert revision is not None
                        assert revision.effective_from is not None
                        before_effective = revision.effective_from - timedelta(
                            seconds=1
                        )
                        confirm = await validate_contract_security(
                            session,
                            revision,
                            stage="confirm",
                            checked_at=before_effective,
                        )
                        activate = await validate_contract_security(
                            session,
                            revision,
                            stage="activate",
                            checked_at=before_effective,
                        )
                        execute = await validate_contract_security(
                            session,
                            revision,
                            stage="execute",
                            checked_at=before_effective,
                        )
                        confirm_window = next(
                            item for item in confirm["checks"]
                            if item["code"] == "effective_window"
                        )
                        assert confirm_window["result"] == "PENDING"
                        assert confirm["overall"] == "PENDING"
                        for decision in (activate, execute):
                            window = next(
                                item for item in decision["checks"]
                                if item["code"] == "effective_window"
                            )
                            assert window["result"] == "BLOCKER"
                            assert decision["overall"] == "BLOCKER"
                finally:
                    await helper_engine.dispose()

            async def set_model_product_status(status: str) -> None:
                helper_engine = create_async_engine(DATABASE_URL)
                helper_factory = async_sessionmaker(
                    helper_engine, expire_on_commit=False
                )
                try:
                    async with helper_factory.begin() as session:
                        model_object = await session.scalar(
                            select(ContractModelObject).where(
                                ContractModelObject.contract_revision_id
                                == contract["revision_id"]
                            )
                        )
                        assert model_object is not None
                        model_version = await session.get(
                            ModelVersion, model_object.model_version_id
                        )
                        assert model_version is not None
                        model_product = await session.get(
                            ModelProduct, model_version.model_product_id
                        )
                        assert model_product is not None
                        model_product.lifecycle_status = status
                finally:
                    await helper_engine.dispose()

            asyncio.run(assert_future_window_is_stage_sensitive())
            eligibility = _prepare_eligibility(
                client, contract["contract_id"], suffix
            )

            asyncio.run(set_model_product_status("unpublished"))
            rejected_job = client.post(
                f"/api/v1/execution-readiness/{contract['contract_id']}/jobs",
                headers=_headers(
                    "data_requester", f"phase56-retired-model-job-{suffix}"
                ),
                json={"eligibility_snapshot_id": eligibility["snapshot_id"]},
            )
            assert rejected_job.status_code == 409
            asyncio.run(set_model_product_status("active"))

            created = client.post(
                f"/api/v1/execution-readiness/{contract['contract_id']}/jobs",
                headers=_headers("data_requester", f"phase56-job-{suffix}"),
                json={"eligibility_snapshot_id": eligibility["snapshot_id"]},
            )
            assert created.status_code == 201
            job_id = created.json()["job_id"]

            detail = client.get(
                f"/api/v1/execution-readiness/{contract['contract_id']}",
                headers=_headers("space_operator"),
            )
            assert detail.status_code == 200
            assert detail.json()["jobs"][0]["run"] is None

            denied = client.post(
                f"/api/v1/execution-readiness/jobs/{job_id}/dispatch",
                headers=_headers("data_requester", f"phase56-denied-{suffix}"),
            )
            assert denied.status_code == 403

            asyncio.run(set_model_product_status("unpublished"))

            async def assert_direct_authorization_is_denied() -> None:
                helper_engine = create_async_engine(DATABASE_URL)
                helper_factory = async_sessionmaker(
                    helper_engine, expire_on_commit=False
                )
                try:
                    async with helper_factory() as session:
                        job = await session.get(ComputeJob, job_id)
                        assert job is not None
                        with pytest.raises(
                            ComputeInvariantError,
                            match="contracted ModelVersion is unavailable",
                        ):
                            await evaluate_compute_authorization(
                                session,
                                revision_id=job.contract_revision_id,
                                party_id=job.requester_contract_party_id,
                                contract_object_id=job.contract_object_id,
                                requester_organization_id=(
                                    job.requester_organization_id
                                ),
                                requester_user_id=job.requester_user_id,
                                purpose_code=job.purpose_code,
                                algorithm_digest=job.algorithm_spec_snapshot[
                                    "algorithm_digest"
                                ],
                                requested_output_types=list(
                                    job.requested_output_types
                                ),
                                exclude_job_id=job.id,
                            )
                finally:
                    await helper_engine.dispose()

            asyncio.run(assert_direct_authorization_is_denied())
            rejected_dispatch = client.post(
                f"/api/v1/execution-readiness/jobs/{job_id}/dispatch",
                headers=_headers(
                    "space_operator", f"phase56-retired-model-dispatch-{suffix}"
                ),
            )
            assert rejected_dispatch.status_code == 409
            asyncio.run(set_model_product_status("active"))

            dispatch_key = f"phase56-dispatch-{suffix}"
            dispatched = client.post(
                f"/api/v1/execution-readiness/jobs/{job_id}/dispatch",
                headers=_headers("space_operator", dispatch_key),
            )
            replay = client.post(
                f"/api/v1/execution-readiness/jobs/{job_id}/dispatch",
                headers=_headers("space_operator", dispatch_key),
            )
            assert dispatched.status_code == replay.status_code == 200
            assert dispatched.json()["replayed"] is False
            assert replay.json()["replayed"] is True
            assert dispatched.json()["run_id"] == replay.json()["run_id"]
            run_id = dispatched.json()["run_id"]

        async def verify() -> None:
            verify_engine = create_async_engine(DATABASE_URL)
            verify_factory = async_sessionmaker(verify_engine, expire_on_commit=False)
            try:
                async with verify_factory() as session:
                    job = await session.get(ComputeJob, job_id)
                    run = await session.get(ComputeRun, run_id)
                    assert job is not None and job.pre_dispatch_slot_ordinal == 1
                    assert run is not None and run.reservation_ordinal == 1
                    assert (
                        await session.scalar(
                            select(func.count(ComputeRun.id)).where(
                                ComputeRun.compute_job_id == job.id
                            )
                        )
                        == 1
                    )
                    context = await evaluate_compute_authorization(
                        session,
                        revision_id=job.contract_revision_id,
                        party_id=job.requester_contract_party_id,
                        contract_object_id=job.contract_object_id,
                        requester_organization_id=job.requester_organization_id,
                        requester_user_id=job.requester_user_id,
                        purpose_code=job.purpose_code,
                        algorithm_digest=job.algorithm_spec_snapshot[
                            "algorithm_digest"
                        ],
                        requested_output_types=list(job.requested_output_types),
                        exclude_run_id=run.id,
                        exclude_job_id=job.id,
                    )
                    assert context.run_limit == 1
            finally:
                await verify_engine.dispose()

        asyncio.run(verify())
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
