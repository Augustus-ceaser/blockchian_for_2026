from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_local_is_safe_default_with_public_docs() -> None:
    settings = Settings(app_env="test")
    assert settings.deployment_mode == "local"
    assert settings.cookie_secure is False
    assert settings.docs_policy == "public"
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 200
        payload = client.get("/api/v1/health/deployment").json()
        assert payload["mode"] == "local"
        assert payload["join_enabled"] is False
        assert payload["hard_isolation"] is False
        assert payload["executor"] == "unknown"
        assert payload["demo_credentials"] == "standard"


def test_lan_uses_operator_docs_and_http_cookie_policy() -> None:
    settings = Settings(
        app_env="test",
        deployment_mode="lan-roadshow",
        public_origin="http://192.0.2.10:8080",
        trusted_origins="http://192.0.2.10:8080",
    )
    assert settings.cookie_secure is False
    assert settings.docs_policy == "operator"
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 401
        payload = client.get("/api/v1/health/deployment").json()
        assert payload["join_enabled"] is True


def test_local_reports_weak_credentials_without_exposing_values() -> None:
    settings = Settings(
        app_env="test",
        deployment_mode="local",
        allow_weak_local_demo_credentials=True,
        password_min_length=3,
        demo_model_password="123",
    )
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/v1/health/deployment").json()
        assert payload["demo_credentials"] == "weak-local-only"
        assert "123" not in str(payload)


def test_short_login_is_rejected_without_the_local_allowance() -> None:
    settings = Settings(app_env="test", deployment_mode="local")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "operator.demo", "password": "123"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "账号或密码无效"


def test_remote_disables_docs_and_requires_secure_cookie_policy() -> None:
    settings = Settings(
        app_env="test",
        deployment_mode="remote-preview",
        public_origin="https://preview.example.invalid",
    )
    assert settings.cookie_secure is True
    assert settings.docs_policy == "disabled"
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_authenticated_browser_mutation_rejects_untrusted_origin() -> None:
    settings = Settings(
        deployment_mode="lan-roadshow",
        public_origin="http://192.0.2.10:8080",
        trusted_origins="http://192.0.2.10:8080",
    )
    with TestClient(create_app(settings)) as client:
        client.cookies.set("medtrust_local_session", "not-a-real-session")
        response = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "https://untrusted.example"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "请求来源未获授权"
