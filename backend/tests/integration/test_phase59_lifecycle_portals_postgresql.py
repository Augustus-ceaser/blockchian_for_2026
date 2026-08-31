from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.session import get_db_session
from app.demo.phase4 import (
    approve_data_listing_command,
    approve_model_listing_command,
    ensure_phase4_demo_initial,
    submit_data_listing_command,
    submit_model_listing_command,
)
from app.main import create_app
from app.modules.identity.local_auth import ensure_local_demo_credentials
from tests.integration.test_phase5_data_product_lifecycle_postgresql import (
    _payload as data_product_payload,
)
from tests.integration.test_phase5_model_product_lifecycle_postgresql import (
    _payload as model_product_payload,
)

DATABASE_URL = os.getenv("MEDTRUST_PHASE59_TEST_DATABASE_URL")
WORKSPACE = Path(__file__).resolve().parents[3]
PASSWORD = "phase59-integration-password"
ROTATED_PASSWORD = "phase59-rotated-password"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE59_TEST_DATABASE_URL is not configured",
    ),
]


def test_four_sessions_and_symmetric_product_lifecycle() -> None:
    assert DATABASE_URL is not None
    async def seed() -> tuple[str, str]:
        seed_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
        seed_factory = async_sessionmaker(seed_engine, expire_on_commit=False)
        try:
            async with seed_factory() as session:
                async with session.begin():
                    context = await ensure_phase4_demo_initial(session, workspace=WORKSPACE)
                    await ensure_local_demo_credentials(session, password=PASSWORD)
                    await submit_data_listing_command(session, context, raw_key="phase59-seed-data")
                    await approve_data_listing_command(session, context, raw_key="phase59-seed-data")
                    await submit_model_listing_command(
                        session, context, workspace=WORKSPACE, raw_key="phase59-seed-model"
                    )
                    await approve_model_listing_command(
                        session, context, workspace=WORKSPACE, raw_key="phase59-seed-model"
                    )
                    return str(context.data_product_id), str(context.model_product_id)
        finally:
            await seed_engine.dispose()

    data_product_id, model_product_id = asyncio.run(seed())
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session():
        async with factory() as session:
            yield session

    app = create_app(
        Settings(
            app_env="development",
            database_url=DATABASE_URL,
            demo_api_enabled=True,
        )
    )
    app.state.auth_session_factory = factory
    app.dependency_overrides[get_db_session] = test_session

    usernames = {
        "space_operator": "operator.demo",
        "data_provider": "hospital.demo",
        "model_provider": "model.demo",
        "data_requester": "requester.demo",
    }

    try:
        clients = {role: TestClient(app) for role in usernames}
        for role, client in clients.items():
            login = client.post(
                "/api/v1/auth/login",
                json={"username": usernames[role], "password": PASSWORD},
            )
            assert login.status_code == 200
            profile = client.get("/api/v1/auth/me")
            assert profile.status_code == 200
            assert profile.json()["role"] == role

        hospital = clients["data_provider"]
        model_provider = clients["model_provider"]
        operator = clients["space_operator"]
        requester = clients["data_requester"]

        denied = requester.post(
            f"/api/v1/data-products/{data_product_id}/lifecycle-requests",
            headers={"Idempotency-Key": f"phase59-denied-{uuid4()}"},
            json={"action": "unpublish", "reason": "unauthorized requester"},
        )
        assert denied.status_code == 403

        for target_type, product_id, owner in (
            ("data", data_product_id, hospital),
            ("model", model_product_id, model_provider),
        ):
            create = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"phase59-{target_type}-unpublish-{uuid4()}"},
                json={
                    "action": "unpublish",
                    "reason": "Lifecycle acceptance unpublish request",
                    "existing_cooperation_note": "Historical contracts remain unchanged",
                },
            )
            assert create.status_code == 200, create.text
            request_id = create.json()["id"]
            approve = operator.post(
                f"/api/v1/product-lifecycle-requests/{request_id}/decision",
                headers={"Idempotency-Key": f"phase59-{target_type}-approve-{uuid4()}"},
                json={"decision": "approved", "comment": "Impact analysis has no blockers"},
            )
            assert approve.status_code == 200, approve.text
            assert approve.json()["status"] == "approved"
            detail = owner.get(
                f"/api/v1/{target_type}-product-versions/"
                f"{approve.json()['target_version_id']}"
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["status"] == "unpublished"
            assert detail.json()["published_at"] is not None
            assert detail.json()["unpublished_at"] is not None

            protected_archive = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"phase59-{target_type}-protected-{uuid4()}"},
                json={"action": "archive", "reason": "Protected product archive check"},
            )
            assert protected_archive.status_code == 409
            assert "受保护" in protected_archive.text

        data_catalog = requester.get("/api/v1/data-product-catalog")
        model_catalog = requester.get("/api/v1/model-product-catalog")
        assert data_catalog.status_code == model_catalog.status_code == 200
        assert data_catalog.json()["total"] == len(data_catalog.json()["items"])
        assert model_catalog.json()["total"] == len(model_catalog.json()["items"])
        assert data_product_id not in {item["product_id"] for item in data_catalog.json()["items"]}
        assert model_product_id not in {item["product_id"] for item in model_catalog.json()["items"]}

        for target_type, product_id, owner in (
            ("data", data_product_id, hospital),
            ("model", model_product_id, model_provider),
        ):
            create = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"phase59-{target_type}-relist-{uuid4()}"},
                json={"action": "relist", "reason": "Original withdrawal reason has been resolved"},
            )
            assert create.status_code == 200, create.text
            approve = operator.post(
                f"/api/v1/product-lifecycle-requests/{create.json()['id']}/decision",
                headers={"Idempotency-Key": f"phase59-{target_type}-relist-approve-{uuid4()}"},
                json={"decision": "approved", "comment": "Approved immutable-version relist"},
            )
            assert approve.status_code == 200, approve.text

        assert data_product_id in {
            item["product_id"]
            for item in requester.get("/api/v1/data-product-catalog").json()["items"]
        }
        assert model_product_id in {
            item["product_id"]
            for item in requester.get("/api/v1/model-product-catalog").json()["items"]
        }
        assert hospital.get("/api/v1/auth/me").json()["role"] == "data_provider"
        assert model_provider.get("/api/v1/auth/me").json()["role"] == "model_provider"
        assert requester.get("/api/v1/auth/me").json()["role"] == "data_requester"
        assert operator.get("/api/v1/auth/me").json()["role"] == "space_operator"

        suffix = uuid4().hex[:10]
        connector_id = hospital.get("/api/v1/data-product-connectors").json()["items"][0]["id"]
        data_created = hospital.post(
            "/api/v1/data-products",
            headers={"Idempotency-Key": f"phase59-extra-data-create-{suffix}"},
            json=data_product_payload(f"phase59-{suffix}", connector_id),
        )
        assert data_created.status_code == 201, data_created.text
        extra_data_id = data_created.json()["product_id"]
        extra_data_version = data_created.json()["version_id"]
        assert hospital.post(
            f"/api/v1/data-product-versions/{extra_data_version}/submit",
            headers={"Idempotency-Key": f"phase59-extra-data-submit-{suffix}"},
        ).status_code == 200
        assert operator.post(
            f"/api/v1/data-product-versions/{extra_data_version}/approve",
            headers={"Idempotency-Key": f"phase59-extra-data-approve-{suffix}"},
            json={
                "review_opinion": "Extra lifecycle acceptance product is complete.",
                "additional_conditions": "Use only for Phase 5.9 lifecycle acceptance.",
                "requested_materials": "",
                "risk_level": "low",
                "allow_catalog": True,
            },
        ).status_code == 200

        model_created = model_provider.post(
            "/api/v1/model-products",
            headers={"Idempotency-Key": f"phase59-extra-model-create-{suffix}"},
            json=model_product_payload(f"phase59-{suffix}"),
        )
        assert model_created.status_code == 201, model_created.text
        extra_model_id = model_created.json()["product_id"]
        extra_model_version = model_created.json()["version_id"]
        assert model_provider.post(
            f"/api/v1/model-product-versions/{extra_model_version}/submit",
            headers={"Idempotency-Key": f"phase59-extra-model-submit-{suffix}"},
        ).status_code == 200
        assert operator.post(
            f"/api/v1/model-product-versions/{extra_model_version}/approve",
            headers={"Idempotency-Key": f"phase59-extra-model-approve-{suffix}"},
            json={
                "review_opinion": "Extra lifecycle acceptance model is complete.",
                "technical_risk": "low",
                "license_risk": "low",
                "additional_conditions": "Use only for Phase 5.9 lifecycle acceptance.",
                "requested_materials": "",
                "allow_catalog": True,
            },
        ).status_code == 200

        for target_type, product_id, owner in (
            ("data", extra_data_id, hospital),
            ("model", extra_model_id, model_provider),
        ):
            active_archive = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"phase59-{target_type}-active-archive-{suffix}"},
                json={"action": "archive", "reason": "Archive must follow unpublish"},
            )
            assert active_archive.status_code == 409

            unpublish_key = f"phase59-{target_type}-race-unpublish-{suffix}"
            unpublish = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": unpublish_key},
                json={"action": "unpublish", "reason": "Concurrent decision acceptance"},
            )
            assert unpublish.status_code == 200, unpublish.text
            duplicate = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": unpublish_key},
                json={"action": "unpublish", "reason": "Concurrent decision acceptance"},
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["id"] == unpublish.json()["id"]
            competing = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"{unpublish_key}-other"},
                json={"action": "unpublish", "reason": "Competing pending request"},
            )
            assert competing.status_code == 409

            decision_keys = (
                f"phase59-{target_type}-decision-a-{suffix}",
                f"phase59-{target_type}-decision-b-{suffix}",
            )

            def approve_request(key: str):
                return operator.post(
                    f"/api/v1/product-lifecycle-requests/{unpublish.json()['id']}/decision",
                    headers={"Idempotency-Key": key},
                    json={"decision": "approved", "comment": "Concurrent operator approval"},
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                decisions = list(executor.map(approve_request, decision_keys))
            assert sorted(response.status_code for response in decisions) == [200, 409]
            winning_index = next(
                index for index, response in enumerate(decisions) if response.status_code == 200
            )
            exact_replay = approve_request(decision_keys[winning_index])
            assert exact_replay.status_code == 200

            changed_relist = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"phase59-{target_type}-changed-relist-{suffix}"},
                json={
                    "action": "relist",
                    "reason": "Changed content must create a new version",
                    "content_changed": True,
                },
            )
            assert changed_relist.status_code == 409

            archive = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"phase59-{target_type}-archive-{suffix}"},
                json={"action": "archive", "reason": "Logical archive acceptance"},
            )
            assert archive.status_code == 200, archive.text

            def finish_archive(mode: str):
                if mode == "cancel":
                    return owner.post(
                        f"/api/v1/product-lifecycle-requests/{archive.json()['id']}/cancel",
                        headers={"Idempotency-Key": f"phase59-{target_type}-cancel-{suffix}"},
                    )
                return operator.post(
                    f"/api/v1/product-lifecycle-requests/{archive.json()['id']}/decision",
                    headers={"Idempotency-Key": f"phase59-{target_type}-archive-approve-{suffix}"},
                    json={"decision": "approved", "comment": "Archive impact is acceptable"},
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                archive_race = list(executor.map(finish_archive, ("cancel", "approve")))
            assert sorted(response.status_code for response in archive_race) == [200, 409]
            archive_status = next(
                response.json()["status"]
                for response in archive_race
                if response.status_code == 200
            )
            if archive_status == "cancelled":
                archive = owner.post(
                    f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                    headers={"Idempotency-Key": f"phase59-{target_type}-archive-retry-{suffix}"},
                    json={"action": "archive", "reason": "Logical archive after cancellation"},
                )
                assert archive.status_code == 200
                approved_archive = operator.post(
                    f"/api/v1/product-lifecycle-requests/{archive.json()['id']}/decision",
                    headers={"Idempotency-Key": f"phase59-{target_type}-archive-final-{suffix}"},
                    json={"decision": "approved", "comment": "Archive impact is acceptable"},
                )
                assert approved_archive.status_code == 200

            archived_relist = owner.post(
                f"/api/v1/{target_type}-products/{product_id}/lifecycle-requests",
                headers={"Idempotency-Key": f"phase59-{target_type}-archived-relist-{suffix}"},
                json={"action": "relist", "reason": "Archived products cannot be relisted"},
            )
            assert archived_relist.status_code == 409

        async def rotate_password() -> None:
            rotate_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
            rotate_factory = async_sessionmaker(rotate_engine, expire_on_commit=False)
            try:
                async with rotate_factory() as session:
                    async with session.begin():
                        await ensure_local_demo_credentials(
                            session, password=ROTATED_PASSWORD
                        )
            finally:
                await rotate_engine.dispose()

        asyncio.run(rotate_password())
        assert operator.get("/api/v1/auth/me").status_code == 401
        rotated_login = operator.post(
            "/api/v1/auth/login",
            json={"username": "operator.demo", "password": ROTATED_PASSWORD},
        )
        assert rotated_login.status_code == 200
        assert operator.get("/api/v1/auth/me").json()["role"] == "space_operator"
        assert operator.post("/api/v1/auth/logout").status_code == 204
        assert operator.get("/api/v1/auth/me").status_code == 401
    finally:
        for client in locals().get("clients", {}).values():
            client.close()
        asyncio.run(engine.dispose())
