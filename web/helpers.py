"""Shared helpers used by multiple web blueprints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from flask import g, session

from locations import get_location
from storage.db import get_preferences


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
