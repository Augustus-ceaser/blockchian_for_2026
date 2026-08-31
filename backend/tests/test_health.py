from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db_session
from app.main import app


def test_root_identifies_operational_prototype() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["status"] == "operational-prototype"


def test_live_health_does_not_require_database() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "MedTrust Space API",
        "version": "0.1.0",
    }


def test_ready_health_reports_database_failure() -> None:
    class UnavailableSession:
        async def execute(self, _: object) -> None:
            raise SQLAlchemyError("database unavailable in test")

    async def unavailable_session():
        yield UnavailableSession()

    app.dependency_overrides[get_db_session] = unavailable_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not ready"}
