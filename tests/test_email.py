"""Tests for services/email.py — send_email SMTP code paths."""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from services import email as email_mod
from services.email import send_email


class _FakeSMTP:
    """Minimal SMTP double that records sendmail calls."""

    def __init__(self, *a, **kw):
        self.logged_in = False
        self.sent: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def ehlo(self):
        pass

    def starttls(self, **kw):
        pass

    def login(self, user, passwd):
        self.logged_in = True

    def sendmail(self, from_addr, to_addrs, msg_str):
        self.sent.append((from_addr, to_addrs, msg_str))


# ---------------------------------------------------------------------------
# Not-configured guard
# ---------------------------------------------------------------------------


class TestNotConfigured:
    def test_returns_false_when_host_missing(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        assert send_email("to@example.com", "Subj", "body") is False

    def test_returns_false_when_from_missing(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "")
        assert send_email("to@example.com", "Subj", "body") is False


# ---------------------------------------------------------------------------
# STARTTLS path (default)
# ---------------------------------------------------------------------------


class TestStarttlsPath:
    def setup_method(self):
        self._smtp = _FakeSMTP()

    def test_sends_via_starttls(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "1")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "")

        smtp_instance = self._smtp

        class _Ctx:
            def __init__(self, *a, **kw):
                pass

        with patch("smtplib.SMTP", return_value=smtp_instance.__enter__()):
            with patch.object(smtp_instance, "__enter__", return_value=smtp_instance):
                pass

        # Use a fresh instance tied to the monkeypatch
        class _Captured(_FakeSMTP):
            pass

        captured = _Captured()

        def _factory(*a, **kw):
            return captured

        monkeypatch.setattr(smtplib, "SMTP", _factory)
        result = send_email("to@example.com", "Hello", "plain body")
        assert result is True
        assert len(captured.sent) == 1

    def test_logins_when_credentials_set(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "1")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "user@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "secret")

        captured = _FakeSMTP()
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **kw: captured)
        send_email("to@example.com", "Subject", "body")
        assert captured.logged_in is True

    def test_attaches_html_when_provided(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "1")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "")

        captured = _FakeSMTP()
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **kw: captured)
        result = send_email("to@example.com", "Subject", "plain", "<b>html</b>")
        assert result is True
        raw = captured.sent[0][2]
        assert "html" in raw

    def test_returns_false_on_smtp_exception(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "1")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "")
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **kw: (_ for _ in ()).throw(smtplib.SMTPException("refused")))
        result = send_email("to@example.com", "Subject", "body")
        assert result is False


# ---------------------------------------------------------------------------
# SSL path (SMTP_SSL on port 465)
# ---------------------------------------------------------------------------


class TestSslPath:
    def test_sends_via_smtp_ssl(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "ssl")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "")

        captured = _FakeSMTP()
        with patch("smtplib.SMTP_SSL", lambda *a, **kw: captured):
            result = send_email("to@example.com", "Subj", "body")
        assert result is True
        assert len(captured.sent) == 1

    def test_ssl_logins_when_credentials_set(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "ssl")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "u")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "p")

        captured = _FakeSMTP()
        with patch("smtplib.SMTP_SSL", lambda *a, **kw: captured):
            send_email("to@example.com", "Subj", "body")
        assert captured.logged_in is True


# ---------------------------------------------------------------------------
# Plain SMTP path (no TLS)
# ---------------------------------------------------------------------------


class TestPlainSmtpPath:
    def test_sends_without_tls(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "0")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "")

        captured = _FakeSMTP()
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **kw: captured)
        result = send_email("to@example.com", "Subj", "body")
        assert result is True

    def test_plain_logins_when_credentials_set(self, monkeypatch):
        monkeypatch.setattr(email_mod, "_SMTP_HOST", "smtp.example.com")
        monkeypatch.setattr(email_mod, "_SMTP_FROM", "from@example.com")
        monkeypatch.setattr(email_mod, "_SMTP_USE_TLS", "0")
        monkeypatch.setattr(email_mod, "_SMTP_USER", "u")
        monkeypatch.setattr(email_mod, "_SMTP_PASS", "p")

        captured = _FakeSMTP()
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **kw: captured)
        send_email("to@example.com", "Subj", "body")
        assert captured.logged_in is True
