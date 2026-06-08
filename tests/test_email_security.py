"""Security tests for the email service (header injection, HTML escaping)."""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Email header injection tests
# ---------------------------------------------------------------------------


def test_sanitize_header_strips_cr_lf():
    """_sanitize_header must remove CR and LF to prevent SMTP header injection."""
    from services.email import _sanitize_header

    assert (
        _sanitize_header("user@example.com\r\nBcc: evil@example.com")
        == "user@example.comBcc: evil@example.com"
    )
    assert (
        _sanitize_header("Subject: legit\r\nX-Injected: bad")
        == "Subject: legitX-Injected: bad"
    )
    assert _sanitize_header("clean@example.com") == "clean@example.com"


def test_send_email_strips_header_injection_from_to(monkeypatch):
    """send_email must sanitize the 'to' address before passing it to SMTP."""
    import smtplib
    from services import email as email_module

    captured: dict = {}

    class _FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def ehlo(self):
            pass

        def starttls(self, **kw):
            pass

        def login(self, *a):
            pass

        def sendmail(self, from_addr, to_addrs, msg_str):
            captured["to"] = to_addrs

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(email_module, "_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_module, "_SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr(email_module, "_SMTP_USE_TLS", "1")

    email_module.send_email(
        "victim@example.com\r\nBcc: attacker@example.com",
        "Hello",
        "body",
    )

    # The injected Bcc header must be stripped; 'to' must not contain newlines
    assert captured.get("to") is not None
    to_val = captured["to"][0]
    # After sanitization, the value must contain no newlines — that's what
    # prevents SMTP from interpreting the injected text as a separate header.
    assert "\r" not in to_val
    assert "\n" not in to_val


def test_send_email_strips_header_injection_from_subject(monkeypatch):
    """send_email must sanitize the subject before setting the MIME header."""
    from services import email as email_module

    built_msgs: list = []

    class _FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def ehlo(self):
            pass

        def starttls(self, **kw):
            pass

        def login(self, *a):
            pass

        def sendmail(self, from_addr, to_addrs, msg_str):
            built_msgs.append(msg_str)

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(email_module, "_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_module, "_SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr(email_module, "_SMTP_USE_TLS", "1")

    email_module.send_email(
        "user@example.com",
        "Legit subject\r\nX-Injected: malicious",
        "body",
    )

    assert built_msgs
    raw = built_msgs[0]
    # After sanitization the injected text is concatenated into the subject
    # value, NOT emitted as a standalone header line.  A standalone injected
    # header would appear as "\nX-Injected:" in the raw message.
    assert "\nX-Injected:" not in raw
