"""Shared helpers used by multiple web blueprints."""

from __future__ import annotations

from typing import Any, Optional

from flask import g, session

from locations import get_location
from storage.sqlite import get_account_credentials, get_preferences


def get_prefs_cached(user_id: int) -> dict[str, Any]:
    """Return preferences for ``user_id``, caching the result on Flask ``g``.

    Within a single request, ``get_preferences`` hits SQLite every time it's
    called.  Because several helpers (before_request hook, session resolver,
    view body) all need the same row, we cache it on ``g`` so the DB is read
    at most once per request.  The cache is per-request by nature of ``g``.
    """
    cache: dict[int, dict[str, Any]] = getattr(g, "_prefs_cache", None)
    if cache is None:
        cache = {}
        g._prefs_cache = cache
    if user_id not in cache:
        cache[user_id] = get_preferences(user_id)
    return cache[user_id]


def get_account_credentials_cached(user_id: int) -> dict[str, Any]:
    """Return passkeys + social accounts for ``user_id``, cached on Flask ``g``.

    The account page and its error-rendering helpers all need the same two
    tables.  Caching on ``g`` means we open one DB connection per request
    regardless of how many times the helper is called (e.g. initial render
    plus an error path that re-renders the full page).
    """
    cache: dict[int, dict[str, Any]] = getattr(g, "_account_creds_cache", None)
    if cache is None:
        cache = {}
        g._account_creds_cache = cache
    if user_id not in cache:
        cache[user_id] = get_account_credentials(user_id)
    return cache[user_id]


def get_session_location() -> Optional[dict[str, Any]]:
    """Return the location config from the user's session, or None.

    For logged-in users, falls back to their saved location preference
    if the session doesn't have a location set.
    """
    loc_id = session.get("location_id")
    if not loc_id and getattr(g, "user", None):
        prefs = get_prefs_cached(g.user["id"])
        loc_id = prefs.get("location_id")
        if loc_id:
            session["location_id"] = loc_id
    if loc_id:
        return get_location(loc_id)
    return None
