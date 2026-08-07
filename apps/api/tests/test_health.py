"""API scaffold smoke tests."""

from fastapi.testclient import TestClient

from surf_pier_api.main import app

client = TestClient(app)


def test_live_health() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "surf-pier-api"}


def test_ready_health() -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "surf-pier-api"}


def test_openapi_includes_health_contracts() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert set(response.json()["paths"]) == {"/health/live", "/health/ready"}
