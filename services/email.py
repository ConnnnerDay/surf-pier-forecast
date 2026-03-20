"""Email sending utility using stdlib smtplib.

Configure via environment variables:
    SMTP_HOST      - SMTP server hostname (required to send)
    SMTP_PORT      - Port (default: 587)
    SMTP_USER      - SMTP login username
    SMTP_PASS      - SMTP login password
    SMTP_FROM      - From address (defaults to SMTP_USER)
    SMTP_USE_TLS   - "1" to use STARTTLS (default), "0" to use plain SMTP,
                     "ssl" to use SMTP_SSL on the given port (e.g. 465)
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

_SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "").strip()
_SMTP_PASS = os.environ.get("SMTP_PASS", "")
_SMTP_FROM = os.environ.get("SMTP_FROM", _SMTP_USER).strip()
_SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1").strip().lower()


def _is_configured() -> bool:
    return bool(_SMTP_HOST and _SMTP_FROM)


def send_email(to: str, subject: str, body_text: str, body_html: str = "") -> bool:
    """Send an email. Returns True on success, False on failure.

    If SMTP is not configured, logs a warning and returns False.
    """
    if not _is_configured():
        logger.warning(
            "SMTP not configured (set SMTP_HOST and SMTP_FROM). "
            "Would have sent '%s' to %s",
            subject,
            to,
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _SMTP_FROM
    msg["To"] = to

    msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    try:
        if _SMTP_USE_TLS == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, context=ctx) as server:
                if _SMTP_USER and _SMTP_PASS:
                    server.login(_SMTP_USER, _SMTP_PASS)
                server.sendmail(_SMTP_FROM, [to], msg.as_string())
        elif _SMTP_USE_TLS == "0":
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
                if _SMTP_USER and _SMTP_PASS:
                    server.login(_SMTP_USER, _SMTP_PASS)
                server.sendmail(_SMTP_FROM, [to], msg.as_string())
        else:
            # Default: STARTTLS on port 587
            ctx = ssl.create_default_context()
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                if _SMTP_USER and _SMTP_PASS:
                    server.login(_SMTP_USER, _SMTP_PASS)
                server.sendmail(_SMTP_FROM, [to], msg.as_string())
        logger.info("Email sent: '%s' -> %s", subject, to)
        return True
    except Exception:
        logger.exception("Failed to send email '%s' to %s", subject, to)
        return False


def send_verification_email(
    to_email: str, username: str, token: str, base_url: str
) -> bool:
    """Send an account verification email with a one-time link."""
    verify_url = f"{base_url.rstrip('/')}/verify-email/{token}"
    subject = "Verify your Surf & Pier account"
    body_text = (
        f"Hi {username},\n\n"
        "Thanks for signing up for Surf & Pier Fishing Forecast!\n\n"
        "Please verify your email address by visiting the link below.\n"
        "This link expires in 24 hours.\n\n"
        f"  {verify_url}\n\n"
        "If you didn't create an account, you can safely ignore this email.\n\n"
        "— Surf & Pier"
    )
    body_html = f"""\
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:480px;margin:auto;padding:2rem;color:#222;">
  <h2 style="color:#0e5f78;">Surf &amp; Pier Fishing Forecast</h2>
  <p>Hi <strong>{username}</strong>,</p>
  <p>Thanks for signing up! Please verify your email address to activate your account.</p>
  <p style="margin:1.5rem 0;">
    <a href="{verify_url}"
       style="background:#0e5f78;color:#fff;padding:0.7rem 1.4rem;border-radius:6px;
              text-decoration:none;font-weight:600;">
      Verify Email Address
    </a>
  </p>
  <p style="font-size:0.85rem;color:#666;">
    Or copy this link into your browser:<br>
    <a href="{verify_url}" style="color:#0e5f78;">{verify_url}</a>
  </p>
  <p style="font-size:0.85rem;color:#666;">This link expires in 24 hours.</p>
  <p style="font-size:0.85rem;color:#999;">
    If you didn't create an account, you can safely ignore this email.
  </p>
</body>
</html>"""
    return send_email(to_email, subject, body_text, body_html)
