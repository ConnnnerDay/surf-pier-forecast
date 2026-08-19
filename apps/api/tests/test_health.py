from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_live() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_ready() -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
