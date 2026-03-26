"""Shared helpers used by multiple web blueprints."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import g, request, session

from locations import get_location
from storage.sqlite import get_preferences

# Only trust X-Forwarded-For when running behind a known reverse proxy.
# Without TRUSTED_PROXY=1, reading X-Forwarded-For lets any client forge
# an arbitrary IP and trivially bypass IP-based rate limiting.
_TRUST_PROXY: bool = os.environ.get("TRUSTED_PROXY", "").strip() == "1"


def _client_ip() -> str:
    """Return the best-effort client IP for rate limiting.

    X-Forwarded-For is only honoured when TRUSTED_PROXY=1 is set.
    """
    if _TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def get_session_location() -> Optional[Dict[str, Any]]:
    """Return the location config from the user's session, or None.

    For logged-in users, falls back to their saved location preference
    if the session doesn't have a location set.
    """
    loc_id = session.get("location_id")
    if not loc_id and getattr(g, "user", None):
        prefs = get_preferences(g.user["id"])
        loc_id = prefs.get("location_id")
        if loc_id:
            session["location_id"] = loc_id
    if loc_id:
        return get_location(loc_id)
    return None
