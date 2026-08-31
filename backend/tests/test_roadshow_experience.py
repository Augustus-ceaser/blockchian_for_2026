import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.routes.roadshow_experience import (
    _application_for_access,
    _short_digest,
    router,
)
from app.demo.phase4 import DemoActor
from app.main import create_app


def test_roadshow_experience_routes_are_read_only_and_registered() -> None:
    local_routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert {route.path for route in local_routes} == {
        "/roadshow-experience/chains",
        "/roadshow-experience/chains/{application_id}",
        "/roadshow-experience/chains/{application_id}/events",
        "/roadshow-experience/health",
    }
    assert all(route.methods == {"GET"} for route in local_routes)

    paths = create_app().openapi()["paths"]
    for path in (
        "/api/v1/roadshow-experience/chains",
        "/api/v1/roadshow-experience/chains/{application_id}",
        "/api/v1/roadshow-experience/chains/{application_id}/events",
        "/api/v1/roadshow-experience/health",
    ):
        assert path in paths
        assert set(paths[path]) == {"get"}


def test_roadshow_digest_projection_is_short_and_non_secret() -> None:
    digest = "sha256:" + "a" * 64
    assert _short_digest(None) is None
    assert _short_digest("short") == "short"
    projected = _short_digest(digest)
    assert projected == f"{digest[:18]}..."
    assert projected != digest


def test_model_provider_cannot_read_a_draft_application() -> None:
    class FakeSession:
        scalar_called = False

        async def get(self, _model, _application_id):
            return SimpleNamespace(id=application_id, status="draft")

        async def scalar(self, _query):
            self.scalar_called = True
            return uuid4()

    application_id = uuid4()
    session = FakeSession()
    actor = DemoActor(
        role="model_provider",
        organization_id=uuid4(),
        user_id=uuid4(),
        organization_name="model provider",
        user_name="model reviewer",
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_application_for_access(session, application_id, actor))

    assert caught.value.status_code == 403
    assert session.scalar_called is False
