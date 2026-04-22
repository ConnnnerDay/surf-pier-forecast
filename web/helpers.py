"""Shared helpers used by multiple web blueprints."""

from __future__ import annotations

from typing import Any, Optional

from flask import g, session

from locations import get_location
from storage.sqlite import get_preferences


def get_preferences_cached(user_id: int) -> dict[str, Any]:
    """Return this user's preferences, cached on flask.g for the request lifetime.

    ``get_preferences`` opens a SQLite connection on every call.  Multiple
    layers (before_request hook, get_session_location, route handlers) each
    call it independently for the same user on every page load.  Caching the
    result on ``g`` (which is request-scoped) collapses those into a single
    DB round-trip without any cross-request sharing risk.
    """
    cache: dict[int, dict[str, Any]] = getattr(g, "_prefs_cache", None)
    if cache is None:
        g._prefs_cache = cache = {}
    if user_id not in cache:
        cache[user_id] = get_preferences(user_id)
    return cache[user_id]


def invalidate_preferences_cache(user_id: int) -> None:
    """Drop the cached preferences for *user_id* after a write.

    Call this immediately after ``save_preferences`` so the next read within
    the same request sees fresh data instead of the pre-write snapshot.
    """
    cache: dict | None = getattr(g, "_prefs_cache", None)
    if cache:
        cache.pop(user_id, None)


def get_session_location() -> Optional[dict[str, Any]]:
    """Return the location config from the user's session, or None.

    For logged-in users, falls back to their saved location preference
    if the session doesn't have a location set.
    """
    loc_id = session.get("location_id")
    if not loc_id and getattr(g, "user", None):
        prefs = get_preferences_cached(g.user["id"])
        loc_id = prefs.get("location_id")
        if loc_id:
            session["location_id"] = loc_id
    if loc_id:
        return get_location(loc_id)
    return None
