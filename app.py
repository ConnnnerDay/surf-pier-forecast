"""
Surf and Pier Fishing Forecast Application
----------------------------------------

Flask app that generates a 24-hour surf and pier fishing forecast for 100+
coastal locations.  Users select their location on first visit.  Fetches
marine conditions from the NWS API, water temperature from NOAA CO-OPS, and
buoy data from NDBC, then dynamically determines which species are likely
biting based on season, water temperature, and solunar conditions.  Rig
recommendations are matched to the active species.

Endpoints:
* ``/``              -- HTML dashboard (redirects to /setup if no location)
* ``/setup``         -- Location picker
* ``/f/<loc_id>``    -- Shareable forecast link
* ``/api/forecast``  -- Current forecast as JSON
* ``/api/refresh``   -- POST to regenerate forecast

No API keys required.  Data cached per-location to ``data/``.
"""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from typing import Any, Dict

from flask import Flask, abort, g, request, send_from_directory, session
import werkzeug

from storage.sqlite import init_db, get_user
from web.auth import bp as auth_bp
from web.api import bp as api_bp
from web.views import bp as views_bp

# Flask<3 test client expects werkzeug.__version__; Werkzeug 3 removed it.
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3"


def create_app() -> Flask:
    """Application factory."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-change-in-production")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB hard limit for file uploads

    _upload_folder = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(_upload_folder, exist_ok=True)
    app.config["UPLOAD_FOLDER"] = _upload_folder

    # Initialize user database
    init_db()

    # -- Request hooks -----------------------------------------------------

    @app.before_request
    def _load_user() -> None:
        """Populate g.user from the session on every request."""
        user_id = session.get("user_id")
        if user_id:
            g.user = get_user(user_id)
            if g.user is None:
                session.pop("user_id", None)
        else:
            g.user = None

    @app.before_request
    def _csrf_protect() -> None:
        """Require CSRF token for browser form POST requests."""
        if request.method != "POST":
            return
        if request.is_json:
            return
        if request.blueprint not in {"auth", "views", "api"}:
            return
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not sent or not expected or sent != expected:
            abort(400)

    def _get_csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(24)
            session["csrf_token"] = token
        return token

    @app.context_processor
    def _inject_user() -> Dict[str, Any]:
        """Make ``user`` available in every template."""
        return {
            "user": getattr(g, "user", None),
            "csrf_token": _get_csrf_token(),
        }

    # -- Service worker at root scope --------------------------------------

    @app.route("/sw.js")
    def service_worker() -> Any:
        """Serve the service worker from the root so its scope covers the whole app.

        A SW registered from /static/sw.js defaults to a scope of /static/ and
        cannot intercept navigations to / or API calls.  Serving it from /sw.js
        gives it the full-site scope it needs for offline support and cache
        strategies to work on mobile.
        """
        resp = send_from_directory(app.static_folder, "sw.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # -- Register blueprints -----------------------------------------------

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)

    return app


# Module-level app instance for backwards compatibility (``python app.py``,
# systemd service, etc.)
app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5757))
    app.run(host="0.0.0.0", port=port)
