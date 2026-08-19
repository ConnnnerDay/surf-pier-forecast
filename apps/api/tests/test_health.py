from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_live() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    # sprint 6 CI-failure proof: deliberately wrong expected value (pytest)
    assert resp.json() == {"status": "definitely-not-ok"}


def test_health_ready() -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
