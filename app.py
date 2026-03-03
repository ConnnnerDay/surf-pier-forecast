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
from datetime import timedelta
from typing import Any, Dict

from flask import Flask, g, session
import werkzeug

from storage.sqlite import init_db, get_user
from web.feature_gates import tier_config
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
                g.account = None
                g.feature_gates = tier_config("free")
            else:
                g.account = g.user
                g.feature_gates = tier_config(g.user.get("tier", "free"))
        else:
            g.user = None
            g.account = None
            g.feature_gates = tier_config("free")

    @app.context_processor
    def _inject_user() -> Dict[str, Any]:
        """Make ``user`` available in every template."""
        return {
            "user": getattr(g, "user", None),
            "account": getattr(g, "account", None),
            "feature_gates": getattr(g, "feature_gates", tier_config("free")),
        }

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
