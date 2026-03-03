"""Access control decorators/middleware helpers for gated features."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import g, jsonify

from storage.sqlite import get_user_account
from web.feature_gates import tier_config
from web.schemas import error_envelope


def require_login_json(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if g.user is None:
            return jsonify(error_envelope("unauthorized", "Not logged in")), 401
        return func(*args, **kwargs)

    return wrapper


def require_feature(feature: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if g.user is None:
                return jsonify(error_envelope("unauthorized", "Not logged in")), 401
            acct = get_user_account(g.user["id"])
            tier = (acct or {}).get("tier", "free")
            allowed = bool(tier_config(tier).get(feature, False))
            if not allowed:
                return jsonify(
                    error_envelope(
                        "feature_locked",
                        f"Feature '{feature}' is available on paid tier.",
                        details={"feature": feature, "tier": tier},
                    )
                ), 403
            return func(*args, **kwargs)

        return wrapper

    return decorator
