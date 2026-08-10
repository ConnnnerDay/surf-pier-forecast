from unittest.mock import patch

from fastapi.testclient import TestClient


def _signup_and_get_headers(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_list_species_requires_auth(client: TestClient) -> None:
    resp = client.get("/regulations/species")
    assert resp.status_code == 401


def test_list_species_returns_real_catalog(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.get("/regulations/species", headers=headers)
    assert resp.status_code == 200
    names = resp.json()
    assert len(names) > 100
    assert names == sorted(names)


def test_lookup_requires_auth(client: TestClient) -> None:
    resp = client.get("/regulations/lookup", params={"species": "Red drum", "state": "NC"})
    assert resp.status_code == 401


def test_lookup_strips_internal_fields(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    fake_reg = {
        "min_size": "18 in TL",
        "slot": "18-27 in",
        "bag_limit": "1 per day",
        "season": "Open year-round",
        "notes": "Verify emergency proclamations.",
        "official_source": "https://example.gov/regs",
        "source_file": "/home/user/surf-pier-forecast/v2/backend/storage/regulations_data.json",
        "is_stale": False,
    }
    with patch("app.api.routes.regulations.lookup_regulation", return_value=fake_reg):
        resp = client.get(
            "/regulations/lookup", params={"species": "Red drum", "state": "NC"}, headers=headers
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["species"] == "Red drum"
    assert body["state"] == "NC"
    assert body["status"] == "legal"
    assert body["min_size"] == "18 in TL"
    assert body["slot"] == "18-27 in"
    assert "source_file" not in body


def test_lookup_unknown_species_state_combo(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    with patch("app.api.routes.regulations.lookup_regulation", return_value=None):
        resp = client.get(
            "/regulations/lookup",
            params={"species": "Not A Real Fish", "state": "NC"},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown"


def test_legal_catch_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/regulations/legal-catch", json={"species": "Red drum", "state": "NC", "length_in": 20}
    )
    assert resp.status_code == 401


def test_legal_catch_end_to_end_legal(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    fake_reg = {
        "min_size": "18 in TL",
        "slot": "18-27 in",
        "bag_limit": "1 per day",
        "season": "Open year-round",
    }
    with patch("app.api.routes.regulations.lookup_regulation", return_value=fake_reg):
        resp = client.post(
            "/regulations/legal-catch",
            json={"species": "Red drum", "state": "NC", "length_in": 22},
            headers=headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "legal"
    assert body["legal"] is True
    assert body["min_size_in"] == 18.0
    assert body["max_size_in"] == 27.0
    assert body["regulation"]["species"] == "Red drum"


def test_legal_catch_too_small(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    fake_reg = {"min_size": "18 in TL", "season": "Open year-round"}
    with patch("app.api.routes.regulations.lookup_regulation", return_value=fake_reg):
        resp = client.post(
            "/regulations/legal-catch",
            json={"species": "Red drum", "state": "NC", "length_in": 12},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "too_small"
    assert resp.json()["legal"] is False


def test_legal_catch_prohibited_species(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    fake_reg = {"min_size": "N/A", "season": "closed year-round", "notes": "federally protected"}
    with patch("app.api.routes.regulations.lookup_regulation", return_value=fake_reg):
        resp = client.post(
            "/regulations/legal-catch",
            json={"species": "Some Protected Fish", "state": "NC", "length_in": 40},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "cannot_target"
    assert resp.json()["legal"] is False


def test_legal_catch_rejects_absurd_length(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.post(
        "/regulations/legal-catch",
        json={"species": "Red drum", "state": "NC", "length_in": 5000},
        headers=headers,
    )
    assert resp.status_code == 422


def test_legal_catch_rejects_zero_or_negative_length(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.post(
        "/regulations/legal-catch",
        json={"species": "Red drum", "state": "NC", "length_in": 0},
        headers=headers,
    )
    assert resp.status_code == 422
