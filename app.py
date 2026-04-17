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

import gzip as _gzip
import hmac
import logging
import os
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse as _urlparse

# Load .env file in development when python-dotenv is installed.
# In production the environment is set by the systemd unit; this is a no-op.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from flask import (
    Flask,
    abort,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
import werkzeug

from storage.sqlite import init_db, get_user
from web.auth import bp as auth_bp
from web.api import bp as api_bp
from web.views import bp as views_bp
from web.geo_api import bp as geo_api_bp

# Flask<3 test client expects werkzeug.__version__; Werkzeug 3 removed it.
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3"  # type: ignore[attr-defined]


def _configure_logging() -> None:
    """Set up basic logging for development and production."""
    level = logging.DEBUG if os.environ.get("FLASK_DEBUG") == "1" else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=level,
    )


def create_app() -> Flask:
    """Application factory."""
    _configure_logging()

    app = Flask(__name__)

    secret_key = os.environ.get("SECRET_KEY", "")
    if not secret_key:
        # Persist a generated key so all gunicorn workers share the same secret.
        _key_file = os.path.join(os.path.dirname(__file__), "data", "secret_key")
        try:
            with open(_key_file, "r") as _f:
                secret_key = _f.read().strip()
        except FileNotFoundError:
            pass
        if not secret_key:
            secret_key = secrets.token_hex(32)
            os.makedirs(os.path.dirname(_key_file), exist_ok=True)
            try:
                with open(_key_file, "w") as _f:
                    _f.write(secret_key)
                os.chmod(_key_file, 0o600)
            except OSError:
                pass
            logging.warning(
                "SECRET_KEY not set — generated and saved to %s. "
                "Set the SECRET_KEY environment variable for production.",
                _key_file,
            )
    app.config["SECRET_KEY"] = secret_key
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=3)
    app.config["MAX_CONTENT_LENGTH"] = (
        16 * 1024 * 1024
    )  # 16 MB hard limit for file uploads

    # Session cookie hardening.
    # SECURE: only transmit the cookie over HTTPS.  Guarded by is_secure check
    # so the dev server still works over plain HTTP.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # SECURE flag is also enforced at the response-header level in
    # _set_security_headers; set it here too so Flask marks the cookie itself.
    if os.environ.get("SESSION_COOKIE_SECURE"):
        app.config["SESSION_COOKIE_SECURE"] = True

    # Store uploads in data/uploads/ (outside static/) so Flask's static file
    # handler never serves them directly.  Access is gated by the auth-checked
    # /uploads/<user_id>/<filename> route defined in web/views.py.
    _upload_folder = os.path.join(os.path.dirname(__file__), "data", "uploads")
    os.makedirs(_upload_folder, exist_ok=True)
    # Restrict uploads directory to the owning process so that other users on
    # a shared host cannot browse uploaded photos.
    try:
        os.chmod(_upload_folder, 0o700)
    except OSError:
        pass
    app.config["UPLOAD_FOLDER"] = _upload_folder

    # Warn operators when the app is deployed behind a reverse proxy but the
    # TRUSTED_PROXY flag has not been set.  Without it, rate limiting keys on
    # the proxy's IP (127.0.0.1) rather than the real client IP, making it
    # trivially bypassable.
    if not os.environ.get("TRUSTED_PROXY") and not app.debug:
        logging.getLogger(__name__).info(
            "TRUSTED_PROXY is not set.  If this app is behind a reverse proxy "
            "(nginx, Caddy, etc.) set TRUSTED_PROXY=1 so that IP-based rate "
            "limiting uses the real client IP from X-Forwarded-For."
        )

    # Initialize user database
    init_db()

    # -- Request hooks -----------------------------------------------------

    @app.before_request
    def _load_user() -> None:
        """Populate g.user from the session on every request.

        Also validates the session_version stored in the cookie against the
        database.  When a user logs in from a new device their session_version
        is incremented, which causes this check to clear any older sessions
        that are still in use on other browsers/devices.
        """
        user_id = session.get("user_id")
        if user_id:
            g.user = get_user(user_id)
            if g.user is None:
                session.clear()
            elif session.get("session_version") != g.user["session_version"]:
                # Session is stale — a newer login invalidated it.
                session.clear()
                g.user = None
        else:
            g.user = None

    # Endpoints that do NOT require a confirmed email address.
    # Everything else is gated so that unverified accounts cannot access the
    # application.  Using a whitelist (rather than a blacklist) is the safer
    # pattern: new routes are protected by default.
    _VERIFICATION_EXEMPT_ENDPOINTS: frozenset[str] = frozenset(
        {
            # Auth flows
            "auth.landing",
            "auth.login",
            "auth.register",
            "auth.logout",
            "auth.verify_email",
            "auth.resend_verification",
            "auth.verify_pending",
            # Account management (users must be able to see/manage their account
            # and change their password even before verifying).
            "auth.account",
            "auth.account_settings",
            "auth.change_password_route",
            "auth.delete_account_route",
            # WebAuthn / passkey flows (biometric login, passkey registration)
            "auth.webauthn_register_begin",
            "auth.webauthn_register_complete",
            "auth.webauthn_authenticate_begin",
            "auth.webauthn_authenticate_complete",
            "auth.webauthn_delete_credential",
            # Social login OAuth flows (Google, Apple)
            "auth.google_login",
            "auth.google_callback",
            "auth.apple_login",
            "auth.apple_callback",
            # Location + profile setup wizard (users need to complete onboarding).
            "views.setup",
            "views.setup_search",
            "views.setup_coords",
            "views.setup_select",
            "views.setup_favorite",
            "views.profile",
            # Profile API endpoints — called by the profile setup page, which is
            # itself exempt.  Without these, the profile save fetch fails silently
            # for users who haven't yet confirmed their email.
            "api.profile_v1",
            "api.preferences",
            # Static assets are served outside the blueprint system.
            "static",
        }
    )

    @app.before_request
    def _require_email_verification() -> None:
        """Redirect logged-in users with unconfirmed email away from protected routes.

        Users who have provided an email address but have not yet confirmed it
        are redirected to the "verify pending" page when they try to access any
        route that is not on the exemption whitelist above.

        Users created without an email address (legacy or anonymous accounts)
        are not affected — this gate only triggers when email is present but
        unconfirmed.
        """
        # Only applies to authenticated users who have an unconfirmed email.
        if g.user is None:
            return
        if g.user.get("email_confirmed"):
            return
        if not g.user.get("email"):
            # No email on file — skip enforcement (pre-email-requirement accounts).
            return

        endpoint = request.endpoint
        if endpoint is None or endpoint in _VERIFICATION_EXEMPT_ENDPOINTS:
            return

        return redirect(url_for("auth.verify_pending"))  # type: ignore[return-value]

    @app.before_request
    def _csrf_protect() -> None:
        """Require CSRF token for browser form POST requests.

        JSON API endpoints are exempt from the double-submit-cookie CSRF check
        because the session cookie is SameSite=Lax, which prevents browsers
        from sending it on cross-origin XHR/fetch POST requests.  As a belt-
        and-suspenders measure we also reject JSON state-changing requests that
        arrive without a same-origin Origin or Referer header, blocking the
        unlikely case where SameSite protection is not enforced (e.g. very old
        browsers or non-browser HTTP clients that have somehow obtained a cookie).
        """
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return

        if request.is_json:
            # For JSON API calls, verify that the request comes from this
            # origin.  Allow missing Origin/Referer only for same-host requests
            # (e.g. server-side tests or native mobile clients).
            origin = request.headers.get("Origin", "")
            referer = request.headers.get("Referer", "")
            host = request.host  # includes port if non-standard
            if origin:
                if _urlparse(origin).netloc != host:
                    abort(400)
            elif referer:
                if _urlparse(referer).netloc != host:
                    abort(400)
            # No Origin/Referer — allow (same-host curl, mobile app, tests).
            return

        if request.blueprint not in {"auth", "views", "api"}:
            return
        # OAuth provider callbacks arrive as cross-origin POSTs from the
        # provider's servers and therefore cannot carry our CSRF token.
        # CSRF for these flows is handled by the OAuth "state" parameter
        # that each callback handler validates against the session value.
        if request.endpoint in {"auth.apple_callback"}:
            return
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            abort(400)

    def _get_csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(24)
            session["csrf_token"] = token
        return token

    @app.context_processor
    def _inject_user() -> dict[str, Any]:
        """Make ``user``, CSRF token, and social-login flags available in every template."""
        return {
            "user": getattr(g, "user", None),
            "csrf_token": _get_csrf_token(),
            "google_login_enabled": bool(os.environ.get("GOOGLE_CLIENT_ID")),
            "apple_login_enabled": bool(os.environ.get("APPLE_CLIENT_ID")),
        }

    # -- Security response headers -----------------------------------------

    @app.after_request
    def _set_security_headers(response: Any) -> Any:
        """Attach defensive HTTP headers to every response."""
        # Prevent browsers from MIME-sniffing the content type.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Deny embedding in <iframe> / <frame> to block clickjacking.
        response.headers.setdefault("X-Frame-Options", "DENY")
        # Only send the bare origin as the Referer header on cross-origin requests.
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        # Opt out of FLoC / Topics API and limit sensitive permissions (privacy).
        response.headers.setdefault(
            "Permissions-Policy",
            # geolocation=self: required for setup-page auto-location.
            # camera=self: required for fishing-log in-app camera.
            # Both are restricted to same-origin — no cross-origin iframe can
            # use them.  microphone stays fully blocked (never needed).
            "interest-cohort=(), geolocation=self, microphone=(), camera=self",
        )
        # Content Security Policy — restrict resource origins to reduce XSS impact.
        # Google Fonts (style + font) and unpkg.com (Leaflet) are explicit allow-listed
        # CDNs used by the templates; all other external sources are blocked.
        # 'unsafe-inline' is required for the existing inline <script> and <style>
        # blocks; if those are ever moved to external files this can be tightened.
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com https://cdn.jsdelivr.net; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: blob: https://lh3.googleusercontent.com "
                "https://*.tile.openstreetmap.org https://*.openstreetmap.fr "
                "https://*.basemaps.cartocdn.com "
                "https://server.arcgisonline.com https://tiles.openseamap.org "
                "https://gibs.earthdata.nasa.gov; "
                "connect-src 'self'; "
                "worker-src 'self'; "
                "frame-ancestors 'none';"
            ),
        )
        # Block Adobe Flash/Acrobat from loading cross-domain policy files.
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        # Authenticated HTML responses must not be stored in any cache so that
        # the browser back-button cannot reveal them after the user logs out.
        if (
            getattr(g, "user", None) is not None
            and "text/html" in response.content_type
            and "Cache-Control" not in response.headers
        ):
            response.headers["Cache-Control"] = "no-store, private"
        # Enforce HTTPS for one year when served over TLS (safe no-op over plain HTTP).
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    @app.after_request
    def _gzip_response(response: Any) -> Any:
        """Compress JSON API responses when the client supports gzip.

        Structure and fishing-map payloads are repetitive JSON that typically
        compresses 85-90 %, cutting 80-200 KB responses down to 10-25 KB.
        Uses Python's built-in gzip (compresslevel=6) — no extra dependencies.
        Skips already-encoded, non-JSON, small (<500 B), or streaming responses.
        """
        if (
            response.direct_passthrough
            or response.status_code != 200
            or "Content-Encoding" in response.headers
            or "gzip" not in request.headers.get("Accept-Encoding", "")
            or not (response.content_type or "").startswith("application/json")
        ):
            return response
        data = response.get_data()
        if len(data) < 500:
            return response
        compressed = _gzip.compress(data, compresslevel=6)
        if len(compressed) >= len(data):
            return response
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = len(compressed)
        response.headers.pop("Content-MD5", None)
        return response

    # -- Error handlers ----------------------------------------------------
    # Explicit handlers prevent Flask from falling back to its built-in error
    # pages, which include the Werkzeug version string and (in debug mode) full
    # stack traces.  All errors return the same generic template so no internal
    # detail is accidentally exposed in production.

    @app.errorhandler(400)
    def _bad_request(exc: Any) -> Any:
        if request.accept_mimetypes.best == "application/json":
            from flask import jsonify

            return jsonify(
                {
                    "ok": False,
                    "error": {"code": "bad_request", "message": "Bad request"},
                }
            ), 400
        return render_template("error.html", message="Bad request."), 400

    @app.errorhandler(404)
    def _not_found(exc: Any) -> Any:
        if request.accept_mimetypes.best == "application/json":
            from flask import jsonify

            return jsonify(
                {"ok": False, "error": {"code": "not_found", "message": "Not found"}}
            ), 404
        return render_template("error.html", message="Page not found."), 404

    @app.errorhandler(405)
    def _method_not_allowed(exc: Any) -> Any:
        if request.accept_mimetypes.best == "application/json":
            from flask import jsonify

            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "method_not_allowed",
                        "message": "Method not allowed",
                    },
                }
            ), 405
        return render_template("error.html", message="Method not allowed."), 405

    @app.errorhandler(413)
    def _request_too_large(exc: Any) -> Any:
        if request.accept_mimetypes.best == "application/json":
            from flask import jsonify

            return jsonify(
                {
                    "ok": False,
                    "error": {"code": "too_large", "message": "Request too large"},
                }
            ), 413
        return render_template("error.html", message="Upload is too large."), 413

    @app.errorhandler(429)
    def _rate_limited(exc: Any) -> Any:
        if request.accept_mimetypes.best == "application/json":
            from flask import jsonify

            return jsonify(
                {
                    "ok": False,
                    "error": {"code": "rate_limited", "message": "Too many requests"},
                }
            ), 429
        return render_template(
            "error.html", message="Too many requests. Please slow down and try again."
        ), 429

    @app.errorhandler(500)
    def _internal_error(exc: Any) -> Any:
        logging.getLogger(__name__).exception("Unhandled exception")
        if request.accept_mimetypes.best == "application/json":
            from flask import jsonify

            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "server_error",
                        "message": "An unexpected error occurred",
                    },
                }
            ), 500
        return render_template(
            "error.html",
            message="An unexpected error occurred. Please try again later.",
        ), 500

    # -- Service worker at root scope --------------------------------------

    @app.route("/sw.js")
    def service_worker() -> Any:
        """Serve the service worker from the root so its scope covers the whole app.

        A SW registered from /static/sw.js defaults to a scope of /static/ and
        cannot intercept navigations to / or API calls.  Serving it from /sw.js
        gives it the full-site scope it needs for offline support and cache
        strategies to work on mobile.
        """
        resp = send_from_directory(app.static_folder or "static", "sw.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # -- Register blueprints -----------------------------------------------

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(geo_api_bp)  # geospatial data: OSM, GIBS, NE, Esri, HDX/FAO

    # Pre-warm the fishing-map response cache so the first real user after a
    # server restart gets a cache hit instead of waiting for the full scoring
    # loop.  Uses the test client (provides a proper request/g context) and
    # runs in a daemon thread so startup is not delayed.
    import threading as _threading

    def _prewarm_fishing_map_cache() -> None:
        import time as _time

        _time.sleep(2)  # let gunicorn workers and DB fully initialise first
        try:
            # Build all lazy indices before the first real request so none of
            # the O(n) setup work happens inside a user-facing request:
            #   _get_loc_species_all     → 101-location × 895-species presence map
            #   _get_species_lower_index → pre-lowercased names + category frozensets
            #   _get_all_species_names   → sorted name list for autocomplete
            from web.api import (
                _get_loc_species_all,
                _get_species_lower_index,
                _get_all_species_names,
            )

            _get_loc_species_all()
            _get_species_lower_index()
            _get_all_species_names()
            # Then warm the full response cache via a real request context
            with app.test_client() as _c:
                _c.get("/api/fishing-map")
            logging.getLogger(__name__).info("fishing-map cache pre-warmed")
        except Exception as _exc:
            logging.getLogger(__name__).debug(
                "fishing-map pre-warm failed (non-fatal): %s", _exc
            )

    _threading.Thread(target=_prewarm_fishing_map_cache, daemon=True).start()

    return app


# Module-level app instance for backwards compatibility (``python app.py``,
# systemd service, etc.)
app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5757))
    # threaded=True allows Flask's dev server to handle concurrent requests so
    # a slow Overpass / NOAA fetch on one thread never blocks filter API calls.
    app.run(host="0.0.0.0", port=port, threaded=True)
