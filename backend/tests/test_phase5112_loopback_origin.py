from app.core.config import Settings


def test_loopback_gateway_can_be_an_explicit_trusted_origin() -> None:
    settings = Settings(
        deployment_mode="local",
        cors_origins=(
            "http://127.0.0.1:5173,"
            "http://localhost:5173,"
            "http://127.0.0.1:8080"
        ),
    )

    assert "http://127.0.0.1:8080" in settings.trusted_origin_list
