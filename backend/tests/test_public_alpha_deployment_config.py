from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.identity.local_auth import LoginRateLimiter


def _secret(tmp_path: Path, name: str, value: str) -> Path:
    path = tmp_path / name
    path.write_text(value + "\n", encoding="utf-8")
    return path


def test_secret_files_override_development_defaults(tmp_path: Path) -> None:
    database_url = "postgresql+asyncpg://medtrust:secret@postgres:5432/medtrust"
    settings = Settings(
        database_url_file=_secret(tmp_path, "database_url", database_url),
        minio_access_key_file=_secret(tmp_path, "minio_user", "alpha-user"),
        minio_secret_key_file=_secret(tmp_path, "minio_password", "strong-secret"),
    )

    assert settings.database_url == database_url
    assert settings.minio_access_key == "alpha-user"
    assert settings.minio_secret_key == "strong-secret"


def test_secret_file_must_be_absolute_and_nonempty(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="absolute path"):
        Settings(database_url_file=Path("relative-secret"))

    with pytest.raises(ValidationError, match="empty"):
        Settings(database_url_file=_secret(tmp_path, "empty", ""))


def test_pre_icp_is_loopback_http_and_does_not_set_secure_cookie() -> None:
    settings = Settings(
        deployment_mode="pre-icp",
        public_origin="http://127.0.0.1:18080",
        allowed_hosts="127.0.0.1,localhost",
    )

    assert settings.cookie_secure is False
    assert settings.docs_policy == "disabled"
    assert settings.allowed_host_list == ["127.0.0.1", "localhost"]

    with pytest.raises(ValidationError, match="loopback HTTP"):
        Settings(
            deployment_mode="pre-icp",
            public_origin="http://example.invalid",
        )


def test_public_alpha_requires_https_and_secure_cookie() -> None:
    settings = Settings(
        deployment_mode="public-alpha",
        public_origin="https://alpha.example.com",
        allowed_hosts="gateway",
    )

    assert settings.cookie_secure is True
    assert settings.docs_policy == "disabled"
    assert settings.allowed_host_list == ["gateway", "alpha.example.com"]

    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            deployment_mode="public-alpha",
            public_origin="http://alpha.example.com",
        )


def test_login_rate_limiter_fails_closed_and_can_reset() -> None:
    limiter = LoginRateLimiter(attempts=3, window_seconds=300)
    key = "127.0.0.1:operator.demo"
    for _ in range(3):
        limiter.record_failure(key)

    with pytest.raises(HTTPException) as exc_info:
        limiter.check(key)
    assert exc_info.value.status_code == 429

    limiter.reset(key)
    limiter.check(key)
