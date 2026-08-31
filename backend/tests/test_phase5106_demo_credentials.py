import pytest

from app.api.routes.auth import LoginRequest
from app.core.config import Settings
from app.modules.identity.local_auth import _password_hash, verify_password


def test_username_length_demo_password_is_hashed() -> None:
    password = "model.demo"
    encoded = _password_hash(password)
    assert password not in encoded
    assert verify_password(password, encoded)
    assert not verify_password("wrong-password", encoded)


def test_three_character_demo_credentials_require_explicit_local_allowance() -> None:
    settings = Settings(
        deployment_mode="local",
        allow_weak_local_demo_credentials=True,
        password_min_length=3,
        demo_model_password="123",
    )
    assert settings.demo_passwords["model.demo"] == "123"
    assert LoginRequest(username="model.demo", password="123").password == "123"


def test_three_character_demo_credentials_are_rejected_without_allowance() -> None:
    with pytest.raises(ValueError, match="explicit local-only allowance"):
        Settings(deployment_mode="local", demo_model_password="123")


@pytest.mark.parametrize(
    "mode", ["lan-roadshow", "remote-preview", "production-template"]
)
def test_weak_demo_credentials_are_rejected_for_remote_modes(mode: str) -> None:
    with pytest.raises(ValueError, match="only for the local deployment mode"):
        Settings(
            deployment_mode=mode,
            allow_weak_local_demo_credentials=True,
            password_min_length=3,
            demo_model_password="123",
        )


def test_catalog_curator_cannot_use_the_local_weak_password_allowance() -> None:
    with pytest.raises(ValueError, match="four public demo accounts"):
        Settings(
            deployment_mode="local",
            allow_weak_local_demo_credentials=True,
            password_min_length=3,
            demo_catalog_curator_password="123",
        )
