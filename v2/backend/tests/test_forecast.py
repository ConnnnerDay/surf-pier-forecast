from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.forecast_cache import ForecastCache


def _signup_and_get_headers(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_forecast_requires_auth(client: TestClient) -> None:
    resp = client.get("/forecast/some-id")
    assert resp.status_code == 401


def test_forecast_404_for_unknown_location(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.get("/forecast/does-not-exist", headers=headers)
    assert resp.status_code == 404


def test_forecast_generation_wired_to_ported_v1_engine(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    loc_resp = client.post(
        "/locations",
        json={"label": "Wrightsville Beach", "lat": 34.2104, "lng": -77.7964},
        headers=headers,
    )
    assert loc_resp.status_code == 201
    location_id = loc_resp.json()["id"]

    fake_location = {"id": "pt_34.21_-77.80", "name": "Near Wrightsville Beach", "state": "NC"}
    fake_forecast = {
        "location_id": fake_location["id"],
        "location_name": fake_location["name"],
        "outlook": "Good",
        "species": [
            {
                "name": "Red drum",
                "regulation": {"min_size": "18 in", "source_file": "/srv/app/storage/regs.json"},
            }
        ],
    }

    with (
        patch(
            "app.api.routes.forecast.build_dynamic_location", return_value=fake_location
        ) as mock_build_loc,
        patch(
            "app.api.routes.forecast.generate_forecast", return_value=dict(fake_forecast)
        ) as mock_generate,
    ):
        resp = client.get(f"/forecast/{location_id}", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    # the route overrides these two with the user's own saved location,
    # not whatever the (mocked) v1 engine invented for the raw lat/lng
    assert body["location_id"] == location_id
    assert body["location_name"] == "Wrightsville Beach"
    assert body["outlook"] == "Good"

    # the server's local filesystem path never reaches the API response
    assert "source_file" not in body["species"][0]["regulation"]
    assert body["species"][0]["regulation"]["min_size"] == "18 in"

    mock_build_loc.assert_called_once_with(34.2104, -77.7964)
    assert mock_generate.call_count == 1
    _, kwargs = mock_generate.call_args
    assert kwargs["location"] == fake_location
    # signup creates an empty Profile row for every user, so this is the
    # "no preferences set yet" dict shape, not None
    assert kwargs["profile"] == {
        "fishing_types": None,
        "targets": None,
        "experience": "beginner",
        "max_wind_kt": None,
        "max_wave_ft": None,
    }


def _add_location(client: TestClient, headers: dict[str, str]) -> str:
    resp = client.post(
        "/locations",
        json={"label": "Wrightsville Beach", "lat": 34.2104, "lng": -77.7964},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_forecast_is_cached_within_ttl(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    location_id = _add_location(client, headers)

    with (
        patch("app.api.routes.forecast.build_dynamic_location", return_value={}),
        patch(
            "app.api.routes.forecast.generate_forecast",
            return_value={"outlook": "Good", "species": []},
        ) as mock_generate,
    ):
        first = client.get(f"/forecast/{location_id}", headers=headers)
        second = client.get(f"/forecast/{location_id}", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["outlook"] == "Good"
    # the second request was served from cache, not a second live call
    assert mock_generate.call_count == 1


def test_forecast_refresh_query_param_bypasses_cache(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    location_id = _add_location(client, headers)

    with (
        patch("app.api.routes.forecast.build_dynamic_location", return_value={}),
        patch(
            "app.api.routes.forecast.generate_forecast",
            return_value={"outlook": "Good", "species": []},
        ) as mock_generate,
    ):
        client.get(f"/forecast/{location_id}", headers=headers)
        client.get(f"/forecast/{location_id}?refresh=true", headers=headers)

    assert mock_generate.call_count == 2


def test_forecast_regenerates_once_cache_is_stale(
    client: TestClient, allowlisted_email: str, db_session: Session
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    location_id = _add_location(client, headers)

    with (
        patch("app.api.routes.forecast.build_dynamic_location", return_value={}),
        patch(
            "app.api.routes.forecast.generate_forecast",
            return_value={"outlook": "Good", "species": []},
        ) as mock_generate,
    ):
        client.get(f"/forecast/{location_id}", headers=headers)

        cached = db_session.get(ForecastCache, location_id)
        assert cached is not None
        cached.generated_at = datetime.now(UTC) - timedelta(hours=5)
        db_session.commit()

        client.get(f"/forecast/{location_id}", headers=headers)

    assert mock_generate.call_count == 2
