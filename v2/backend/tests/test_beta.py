from fastapi.testclient import TestClient


def test_beta_request(client: TestClient) -> None:
    resp = client.post("/beta-requests", json={"email": "hopeful@example.com"})
    assert resp.status_code == 201
    assert resp.json() == {"status": "received"}
