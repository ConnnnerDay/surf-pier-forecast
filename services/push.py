"""Web Push (VAPID) delivery.

Disabled by default: pushes are a safe no-op until VAPID keys are configured
via environment variables.  Generate a key pair with::

    python -m py_vapid --gen   # or vapid --gen

and set:

    VAPID_PUBLIC_KEY    - URL-safe base64 application server public key
    VAPID_PRIVATE_KEY   - URL-safe base64 (or PEM) private key
    VAPID_SUBJECT       - "mailto:you@example.com" contact

``pywebpush`` is imported lazily inside :func:`send_push` so the application
(and the test suite) runs without the dependency installed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from storage.sqlite import delete_push_subscription

logger = logging.getLogger(__name__)

_VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
_VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
_VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "").strip()


def is_push_configured() -> bool:
    """True when VAPID keys are present so pushes can actually be sent."""
    return bool(_VAPID_PRIVATE_KEY and _VAPID_PUBLIC_KEY and _VAPID_SUBJECT)


def get_public_key() -> str:
    """The VAPID application-server public key the client subscribes with."""
    return _VAPID_PUBLIC_KEY


def send_push(subscription: dict[str, str], title: str, body: str, url: str) -> bool:
    """Send a single Web Push message. Returns True on success.

    Stale subscriptions (HTTP 404/410) are pruned from storage so a dead
    endpoint is not retried forever.  Any other failure is logged and returns
    False without raising.
    """
    if not is_push_configured():
        logger.warning(
            "Web push not configured (set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / "
            "VAPID_SUBJECT). Would have pushed '%s'",
            title,
        )
        return False

    endpoint = subscription.get("endpoint", "")
    if not endpoint:
        return False

    try:
        import json as _json

        from pywebpush import WebPushException, webpush
    except Exception:
        logger.warning("pywebpush is not installed; cannot send web push")
        return False

    sub_info: dict[str, Any] = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": subscription.get("p256dh", ""),
            "auth": subscription.get("auth", ""),
        },
    }
    payload = _json.dumps({"title": title, "body": body, "url": url})

    try:
        webpush(
            subscription_info=sub_info,
            data=payload,
            vapid_private_key=_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": _VAPID_SUBJECT},
            timeout=10,
        )
        return True
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            logger.info("push.subscription_gone endpoint=%s status=%s", endpoint, status)
            delete_push_subscription(endpoint)
        else:
            logger.warning("push.send_failed status=%s: %s", status, exc)
        return False
    except Exception:
        logger.exception("push.send_error endpoint=%s", endpoint)
        return False
