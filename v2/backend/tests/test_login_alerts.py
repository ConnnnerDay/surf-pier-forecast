from unittest.mock import patch

from fastapi.testclient import TestClient


def _signup(client: TestClient, email: str) -> None:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201


def test_login_from_new_device_sends_alert(client: TestClient, allowlisted_email: str) -> None:
    _signup(client, allowlisted_email)

    with patch("app.api.routes.auth._send_login_alert") as mock_alert:
        resp = client.post(
            "/auth/login",
            json={
                "email": allowlisted_email,
                "password": "GoodPass1",
                "device_label": "Chrome on macOS",
            },
        )
    assert resp.status_code == 200
    mock_alert.assert_called_once_with(allowlisted_email, "Chrome on macOS")


def test_login_from_known_device_does_not_alert_again(
    client: TestClient, allowlisted_email: str
) -> None:
    _signup(client, allowlisted_email)

    with patch("app.api.routes.auth._send_login_alert") as mock_alert:
        client.post(
            "/auth/login",
            json={
                "email": allowlisted_email,
                "password": "GoodPass1",
                "device_label": "Chrome on macOS",
            },
        )
        second = client.post(
            "/auth/login",
            json={
                "email": allowlisted_email,
                "password": "GoodPass1",
                "device_label": "Chrome on macOS",
            },
        )

    assert second.status_code == 200
    assert mock_alert.call_count == 1


def test_login_without_device_label_never_alerts(
    client: TestClient, allowlisted_email: str
) -> None:
    _signup(client, allowlisted_email)

    with patch("app.api.routes.auth._send_login_alert") as mock_alert:
        resp = client.post(
            "/auth/login", json={"email": allowlisted_email, "password": "GoodPass1"}
        )

    assert resp.status_code == 200
    mock_alert.assert_not_called()


def test_send_login_alert_is_a_safe_noop_without_smtp_configured() -> None:
    # services/email.py logs and returns False when SMTP isn't configured —
    # this should never raise, matching v1's "no SMTP = no-op" behavior
    # (see CLAUDE.md environment variables table).
    from app.api.routes.auth import _send_login_alert

    _send_login_alert("someone@example.com", "Safari on iOS")
