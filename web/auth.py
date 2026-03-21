"""Authentication routes: login, register, logout, account."""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Dict, Tuple

import logging

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

logger = logging.getLogger(__name__)

from locations import get_location
from storage.db import (
    authenticate_user,
    bump_session_version,
    change_password,
    confirm_email,
    create_user,
    delete_user,
    get_all_user_photo_paths,
    get_email_verification_sent_at,
    get_preferences,
    get_recent_logs,
    get_user,
    get_user_by_email,
    get_user_by_verification_token,
    get_user_password_hash,
    save_preferences,
    set_email_verification_token,
    save_webauthn_credential,
    get_webauthn_credentials,
    get_webauthn_credential_by_id,
    update_webauthn_sign_count,
    delete_webauthn_credential,
)
from services.email import send_verification_email

bp = Blueprint("auth", __name__)

_LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
_LOGIN_RATE_LIMIT_WINDOW_S = 15 * 60

_REGISTER_RATE_LIMIT_MAX_ATTEMPTS = 5
_REGISTER_RATE_LIMIT_WINDOW_S = 60 * 60  # 1 hour

# Forecast force-refresh is expensive (external API calls + DB writes).
# Limit each IP to 4 force refreshes per 5-minute window.
_REFRESH_RATE_LIMIT_MAX_ATTEMPTS = 4
_REFRESH_RATE_LIMIT_WINDOW_S = 5 * 60

# Sensitive account-action rate limiting (password change, account delete).
#
# Even an authenticated attacker (via XSS, session hijack, or a shared
# computer) should not be able to rapidly brute-force the "current password"
# field on these forms.  Each IP is limited to 5 attempts per 15 minutes.
_ACCOUNT_ACTION_RATE_LIMIT_MAX_ATTEMPTS = 5
_ACCOUNT_ACTION_RATE_LIMIT_WINDOW_S = 15 * 60

_account_action_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_account_action_rate_limit_lock = threading.Lock()


def _account_action_is_rate_limited() -> bool:
    return _is_rate_limited(
        _account_action_rate_limit_store,
        _account_action_rate_limit_lock,
        _ACCOUNT_ACTION_RATE_LIMIT_MAX_ATTEMPTS,
        _ACCOUNT_ACTION_RATE_LIMIT_WINDOW_S,
    )


def _record_account_action_failure() -> None:
    _record_attempt(
        _account_action_rate_limit_store,
        _account_action_rate_limit_lock,
        _ACCOUNT_ACTION_RATE_LIMIT_WINDOW_S,
    )


def _clear_account_action_failures() -> None:
    _clear_attempts(_account_action_rate_limit_store, _account_action_rate_limit_lock)


# Per-username account lockout.
#
# IP-based rate limiting alone does not stop a distributed brute-force attack
# where many different IPs target the same account.  Tracking failures per
# username (case-folded) provides a second, independent layer: after
# _ACCOUNT_LOCKOUT_MAX_FAILURES consecutive wrong passwords for a given
# username the account is locked for _ACCOUNT_LOCKOUT_WINDOW_S seconds,
# regardless of how many source IPs are involved.
#
# On successful login the counter for that username is cleared.
_ACCOUNT_LOCKOUT_MAX_FAILURES = 10
_ACCOUNT_LOCKOUT_WINDOW_S = 30 * 60  # 30 minutes

# ---------------------------------------------------------------------------
# Server-side IP-keyed rate limiting for the login and registration endpoints.
#
# Keying on the client IP prevents circumvention by clearing cookies or making
# requests without a session.
#
# X-Forwarded-For is only trusted when TRUSTED_PROXY=1 is set in the
# environment (i.e. the app is explicitly deployed behind a reverse proxy).
# Without that flag, request.remote_addr is used directly, preventing an
# attacker from spoofing arbitrary IPs to bypass rate limits.
#
# Data structure: {ip: (window_start_ts, attempt_count)}
# A single lock guards all reads and writes.
# ---------------------------------------------------------------------------
_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_rate_limit_lock = threading.Lock()

_register_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_register_rate_limit_lock = threading.Lock()

_refresh_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_refresh_rate_limit_lock = threading.Lock()

# Resend-verification: 3 attempts per 30 minutes per IP.
_RESEND_RATE_LIMIT_MAX_ATTEMPTS = 3
_RESEND_RATE_LIMIT_WINDOW_S = 30 * 60

_resend_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_resend_rate_limit_lock = threading.Lock()

# Minimum seconds that must elapse between two verification emails for the
# same account (DB-level per-user throttle, independent of IP).
_RESEND_MIN_INTERVAL_S = 120  # 2 minutes

# Keyed by lowercase username rather than IP.
_account_lockout_store: Dict[str, Tuple[float, int]] = {}
_account_lockout_lock = threading.Lock()

# Only trust X-Forwarded-For when running behind a known reverse proxy.
_TRUST_PROXY = os.environ.get("TRUSTED_PROXY", "").strip() == "1"


def _client_ip() -> str:
    """Return the best-effort client IP.

    X-Forwarded-For is only honoured when the app is explicitly configured to
    run behind a trusted reverse proxy (``TRUSTED_PROXY=1``).  Without that
    flag, blindly reading X-Forwarded-For would let any client forge a
    different IP on every request and trivially bypass IP-based rate limiting.
    """
    if _TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # Take the left-most entry — the original client IP.
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


_PRUNE_EVERY = 200  # prune expired entries every N rate-limit checks
_prune_counter = 0


def _prune_store(store: Dict[str, Tuple[float, int]], window_s: float) -> None:
    """Remove entries whose rate-limit window has expired.

    Called periodically (every _PRUNE_EVERY checks) to keep the in-memory
    stores from growing without bound when the app is hit from many unique IPs.
    Must be called while holding the relevant lock.
    """
    now = time.time()
    expired = [ip for ip, (start, _) in store.items() if now - start > window_s]
    for ip in expired:
        del store[ip]


def _is_rate_limited(
    store: Dict[str, Tuple[float, int]],
    lock: threading.Lock,
    max_attempts: int,
    window_s: float,
) -> bool:
    """Return True if the current client IP has exceeded the given rate limit."""
    global _prune_counter
    ip = _client_ip()
    now = time.time()
    with lock:
        _prune_counter += 1
        if _prune_counter % _PRUNE_EVERY == 0:
            _prune_store(store, window_s)
        start, attempts = store.get(ip, (now, 0))
        if now - start > window_s:
            store[ip] = (now, 0)
            return False
        return attempts >= max_attempts


def _record_attempt(
    store: Dict[str, Tuple[float, int]],
    lock: threading.Lock,
    window_s: float,
) -> None:
    """Increment the attempt counter for the current client IP."""
    ip = _client_ip()
    now = time.time()
    with lock:
        start, attempts = store.get(ip, (now, 0))
        if now - start > window_s:
            store[ip] = (now, 1)
        else:
            store[ip] = (start, attempts + 1)


def _clear_attempts(
    store: Dict[str, Tuple[float, int]],
    lock: threading.Lock,
) -> None:
    """Clear the attempt counter for the current client IP."""
    ip = _client_ip()
    with lock:
        store.pop(ip, None)


def _login_is_rate_limited() -> bool:
    return _is_rate_limited(
        _rate_limit_store, _rate_limit_lock,
        _LOGIN_RATE_LIMIT_MAX_ATTEMPTS, _LOGIN_RATE_LIMIT_WINDOW_S,
    )


def _record_login_failure() -> None:
    _record_attempt(_rate_limit_store, _rate_limit_lock, _LOGIN_RATE_LIMIT_WINDOW_S)


def _clear_login_failures() -> None:
    _clear_attempts(_rate_limit_store, _rate_limit_lock)


def _register_is_rate_limited() -> bool:
    return _is_rate_limited(
        _register_rate_limit_store, _register_rate_limit_lock,
        _REGISTER_RATE_LIMIT_MAX_ATTEMPTS, _REGISTER_RATE_LIMIT_WINDOW_S,
    )


def _record_register_attempt() -> None:
    _record_attempt(
        _register_rate_limit_store, _register_rate_limit_lock,
        _REGISTER_RATE_LIMIT_WINDOW_S,
    )


def refresh_is_rate_limited() -> bool:
    """Return True if this IP has exceeded the forecast force-refresh rate limit."""
    return _is_rate_limited(
        _refresh_rate_limit_store, _refresh_rate_limit_lock,
        _REFRESH_RATE_LIMIT_MAX_ATTEMPTS, _REFRESH_RATE_LIMIT_WINDOW_S,
    )


def record_refresh_attempt() -> None:
    _record_attempt(
        _refresh_rate_limit_store, _refresh_rate_limit_lock,
        _REFRESH_RATE_LIMIT_WINDOW_S,
    )


# -- Per-username account lockout -------------------------------------------

_LOCKOUT_PRUNE_EVERY = 500  # prune expired lockout entries every N checks


def _prune_lockout_store() -> None:
    """Remove expired entries from the lockout store (call while holding the lock)."""
    now = time.time()
    expired = [k for k, (start, _) in _account_lockout_store.items()
               if now - start > _ACCOUNT_LOCKOUT_WINDOW_S]
    for k in expired:
        del _account_lockout_store[k]


_lockout_prune_counter = 0


def _account_is_locked(username: str) -> bool:
    """Return True if *username* has exceeded the per-account failure threshold."""
    global _lockout_prune_counter
    key = username.lower()
    now = time.time()
    with _account_lockout_lock:
        _lockout_prune_counter += 1
        if _lockout_prune_counter % _LOCKOUT_PRUNE_EVERY == 0:
            _prune_lockout_store()
        start, failures = _account_lockout_store.get(key, (now, 0))
        if now - start > _ACCOUNT_LOCKOUT_WINDOW_S:
            _account_lockout_store[key] = (now, 0)
            return False
        return failures >= _ACCOUNT_LOCKOUT_MAX_FAILURES


def _record_account_failure(username: str) -> None:
    """Increment the per-username failure counter."""
    key = username.lower()
    now = time.time()
    with _account_lockout_lock:
        start, failures = _account_lockout_store.get(key, (now, 0))
        if now - start > _ACCOUNT_LOCKOUT_WINDOW_S:
            _account_lockout_store[key] = (now, 1)
        else:
            _account_lockout_store[key] = (start, failures + 1)


def _clear_account_failures(username: str) -> None:
    """Clear the failure counter for *username* after a successful login."""
    key = username.lower()
    with _account_lockout_lock:
        _account_lockout_store.pop(key, None)


def _password_complexity_error(password: str) -> str:
    """Return an error message if the password fails complexity requirements, else ''."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return "Password must include at least one number."
    return ""


@bp.route("/welcome")
def landing() -> Any:
    """Public landing page for unauthenticated visitors."""
    if g.user is not None:
        return redirect(url_for("views.index"))
    return render_template("landing.html")


@bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    """Log-in page and form handler."""
    if request.method == "GET":
        if g.user is not None:
            return redirect(url_for("views.index"))
        return render_template("login.html", error=None)
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        return render_template(
            "login.html", error="Please enter both fields.", username=username
        )
    if _login_is_rate_limited():
        logger.warning("security.login_ip_rate_limited ip=%s", _client_ip())
        return render_template(
            "login.html",
            error="Too many attempts. Please wait a few minutes and try again.",
            username=username,
        )
    if _account_is_locked(username):
        logger.warning(
            "security.login_account_locked username=%r ip=%s",
            username,
            _client_ip(),
        )
        return render_template(
            "login.html",
            error="Too many failed attempts. Please try again in 30 minutes.",
            username=username,
        )
    user = authenticate_user(username, password)
    if user is None:
        _record_login_failure()
        _record_account_failure(username)
        logger.warning(
            "security.login_failed username=%r ip=%s",
            username,
            _client_ip(),
        )
        return render_template(
            "login.html", error="Invalid username or password.", username=username
        )
    _clear_login_failures()
    _clear_account_failures(username)
    logger.info("security.login_success user_id=%s ip=%s", user["id"], _client_ip())
    # Regenerate session to prevent session fixation: preserve the anonymous
    # location choice, then clear everything else before setting credentials.
    prior_location_id = session.get("location_id")
    session.clear()
    # Bump the session version so any existing sessions on other devices are
    # immediately invalidated the next time they hit _load_user().
    new_version = bump_session_version(user["id"])
    session["user_id"] = user["id"]
    session["session_version"] = new_version
    session.permanent = True
    # Issue a fresh CSRF token post-login so any token captured before
    # authentication is no longer valid for authenticated endpoints.
    session["csrf_token"] = secrets.token_urlsafe(24)
    # Restore saved location preference (DB preference wins over anonymous choice).
    prefs = get_preferences(user["id"])
    if prefs.get("location_id"):
        session["location_id"] = prefs["location_id"]
    elif user.get("default_location_id"):
        session["location_id"] = user["default_location_id"]
    elif prior_location_id:
        session["location_id"] = prior_location_id
    return redirect(url_for("views.index"))


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Allowlist of widely-distributed consumer email domains.
# Only these domains are accepted at registration to reduce spam, disposable
# address abuse, and accounts with unreachable mailboxes.
_ALLOWED_EMAIL_DOMAINS: frozenset[str] = frozenset({
    # ── Google ────────────────────────────────────────────────────────────────
    "gmail.com", "googlemail.com",

    # ── Microsoft (Outlook / Hotmail / Live / MSN) ────────────────────────────
    # Global + regional Outlook
    "outlook.com", "outlook.co.uk", "outlook.com.au", "outlook.fr",
    "outlook.de", "outlook.es", "outlook.it", "outlook.co.in",
    "outlook.com.br", "outlook.com.ar", "outlook.com.mx", "outlook.cl",
    "outlook.pt", "outlook.be", "outlook.nl", "outlook.at", "outlook.dk",
    "outlook.fi", "outlook.se", "outlook.no", "outlook.ie", "outlook.sg",
    "outlook.jp", "outlook.kr", "outlook.ph", "outlook.my",
    "outlook.co.nz", "outlook.co.za", "outlook.co.th", "outlook.com.vn",
    "outlook.com.ng", "outlook.com.pk", "outlook.com.co", "outlook.com.pe",
    "outlook.com.tr", "outlook.hr", "outlook.rs", "outlook.hu",
    "outlook.ro", "outlook.cz", "outlook.sk", "outlook.bg",
    "outlook.gr", "outlook.lv", "outlook.lt", "outlook.ee",
    "outlook.sa", "outlook.ae", "outlook.co.il",
    # Hotmail regional
    "hotmail.com", "hotmail.co.uk", "hotmail.fr", "hotmail.de",
    "hotmail.es", "hotmail.it", "hotmail.com.au", "hotmail.co.in",
    "hotmail.com.br", "hotmail.com.ar", "hotmail.com.mx", "hotmail.cl",
    "hotmail.pt", "hotmail.be", "hotmail.nl", "hotmail.gr",
    "hotmail.dk", "hotmail.fi", "hotmail.se", "hotmail.no",
    "hotmail.co.jp", "hotmail.rs", "hotmail.hr",
    "hotmail.co.nz", "hotmail.co.za", "hotmail.com.tr", "hotmail.com.vn",
    "hotmail.com.co", "hotmail.com.pe", "hotmail.hu", "hotmail.ro",
    "hotmail.cz", "hotmail.sk", "hotmail.bg", "hotmail.lv",
    "hotmail.lt", "hotmail.ee",
    # Live regional
    "live.com", "live.co.uk", "live.fr", "live.de", "live.com.au",
    "live.co.in", "live.it", "live.ca", "live.be", "live.nl",
    "live.at", "live.dk", "live.fi", "live.se", "live.no", "live.ie",
    "live.sg", "live.jp", "live.in", "live.cl",
    "live.com.ar", "live.com.mx", "live.com.pt",
    "live.co.nz", "live.co.za", "live.co.th", "live.com.vn",
    "live.com.tr", "live.ph", "live.my", "live.kr",
    "live.hu", "live.ro", "live.cz", "live.sk", "live.bg",
    "live.lv", "live.lt", "live.ee", "live.sa", "live.ae",
    "msn.com",

    # ── Yahoo / Oath ──────────────────────────────────────────────────────────
    "yahoo.com", "yahoo.co.uk", "yahoo.ca", "yahoo.com.au",
    "yahoo.fr", "yahoo.de", "yahoo.es", "yahoo.it", "yahoo.co.jp",
    "yahoo.co.in", "yahoo.com.br", "yahoo.com.ar", "yahoo.com.mx",
    "yahoo.com.hk", "yahoo.com.sg", "yahoo.com.ph", "yahoo.com.tw",
    "yahoo.com.my", "yahoo.com.vn", "yahoo.com.pe", "yahoo.com.co",
    "yahoo.com.pk", "yahoo.co.id", "yahoo.co.nz", "yahoo.co.za",
    "yahoo.co.th",
    "yahoo.gr", "yahoo.ro", "yahoo.hu", "yahoo.dk", "yahoo.se",
    "yahoo.no", "yahoo.fi", "yahoo.be", "yahoo.at", "yahoo.pt",
    "yahoo.nl", "yahoo.ie", "yahoo.in",
    "yahoo.pl", "yahoo.cz", "yahoo.sk", "yahoo.hr", "yahoo.rs",
    "yahoo.bg", "yahoo.lv", "yahoo.lt",
    "ymail.com",

    # ── Apple — standard + Hide My Email (Private Relay) ─────────────────────
    "icloud.com", "me.com", "mac.com",
    # Hide My Email / Private Relay (format: random@privaterelay.appleid.com)
    "privaterelay.appleid.com",

    # ── AOL / Verizon Media ───────────────────────────────────────────────────
    "aol.com", "aol.co.uk",

    # ── US ISP / cable email ──────────────────────────────────────────────────
    "comcast.net", "xfinity.com",
    "att.net", "sbcglobal.net", "bellsouth.net", "pacbell.net",
    "verizon.net",
    "cox.net",
    "charter.net", "spectrum.net",
    "earthlink.net",
    "windstream.net",
    "centurylink.net", "lumen.com",
    "mindspring.com",           # legacy EarthLink brand

    # ── Proton ────────────────────────────────────────────────────────────────
    "proton.me", "protonmail.com", "pm.me",

    # ── Tuta (formerly Tutanota) ──────────────────────────────────────────────
    "tuta.com", "tutanota.com", "tutanota.de", "tutamail.com", "tuta.io",

    # ── Zoho ──────────────────────────────────────────────────────────────────
    "zoho.com",

    # ── GMX / Web.de / Mail.com (United Internet) ────────────────────────────
    "gmx.com", "gmx.net", "gmx.de", "gmx.at", "gmx.ch",
    "web.de", "mail.com",

    # ── Fastmail ──────────────────────────────────────────────────────────────
    "fastmail.com", "fastmail.fm",

    # ── Yandex (Russia / CIS) ────────────────────────────────────────────────
    "yandex.com", "yandex.ru", "yandex.ua", "yandex.by",
    "yandex.kz", "yandex.com.tr", "ya.ru",

    # ── Mail.ru / VK (Russia) ─────────────────────────────────────────────────
    "mail.ru", "list.ru", "inbox.ru", "bk.ru", "internet.ru",

    # ── Rambler (Russia) ──────────────────────────────────────────────────────
    "rambler.ru", "lenta.ru", "ro.ru",

    # ── UKR.net (Ukraine) ────────────────────────────────────────────────────
    "ukr.net",

    # ── NetEase / 163 (China) ─────────────────────────────────────────────────
    "163.com", "126.com", "yeah.net",

    # ── QQ / Tencent (China) ──────────────────────────────────────────────────
    "qq.com", "foxmail.com",

    # ── Sina (China) ──────────────────────────────────────────────────────────
    "sina.com", "sina.cn",

    # ── Sohu (China) ──────────────────────────────────────────────────────────
    "sohu.com",

    # ── 21CN (China) ──────────────────────────────────────────────────────────
    "21cn.com",

    # ── Naver / Daum / Kakao / Nate (South Korea) ────────────────────────────
    "naver.com", "hanmail.net", "daum.net", "kakao.com", "nate.com",

    # ── Japanese carrier / ISP email ─────────────────────────────────────────
    "docomo.ne.jp", "softbank.ne.jp", "i.softbank.jp",
    "ezweb.ne.jp", "au.com",
    "biglobe.ne.jp", "nifty.com",

    # ── Rediffmail (India) ───────────────────────────────────────────────────
    "rediffmail.com", "indiatimes.com",

    # ── UK ISPs ──────────────────────────────────────────────────────────────
    "btinternet.com", "bt.com", "btopenworld.com",
    "sky.com", "skymail.com",
    "virginmedia.com", "virgin.net",
    "talktalk.net", "talktalk.co.uk",
    "ntlworld.com",
    "plusnet.com",
    "tiscali.co.uk",

    # ── German ISPs ──────────────────────────────────────────────────────────
    "t-online.de",
    "freenet.de",
    "arcor.de", "vodafone.de",
    "kabelbw.de",

    # ── French ISPs / portals ────────────────────────────────────────────────
    "orange.fr", "sfr.fr", "neuf.fr", "laposte.net",
    "free.fr", "wanadoo.fr",
    "bbox.fr", "bouyguestelecom.fr",
    "club-internet.fr",

    # ── Italian ISP / portals ────────────────────────────────────────────────
    "libero.it", "virgilio.it", "alice.it", "tiscali.it",
    "tim.it", "vodafone.it",

    # ── Dutch ISPs ───────────────────────────────────────────────────────────
    "ziggo.nl", "kpn.nl", "hetnet.nl", "planet.nl",
    "xs4all.nl", "casema.nl",

    # ── Belgian ISPs ────────────────────────────────────────────────────────
    "skynet.be", "telenet.be", "proximus.be",

    # ── Swedish / Norwegian / Danish / Finnish ISPs ───────────────────────────
    "telia.com", "swipnet.se", "tele2.se",
    "online.no", "telenor.no",
    "tdc.dk", "telenor.dk",
    "kolumbus.fi",

    # ── Polish portals (dominant in Poland) ──────────────────────────────────
    "wp.pl", "onet.pl", "interia.pl", "o2.pl", "gazeta.pl",

    # ── Czech portals ────────────────────────────────────────────────────────
    "seznam.cz", "centrum.cz", "email.cz", "volny.cz",

    # ── Hungarian portals ────────────────────────────────────────────────────
    "freemail.hu", "citromail.hu",

    # ── Australian ISPs ──────────────────────────────────────────────────────
    "bigpond.com", "bigpond.net.au",
    "optusnet.com.au", "iinet.net.au",
    "westnet.com.au", "internode.on.net",

    # ── New Zealand ISPs ─────────────────────────────────────────────────────
    "xtra.co.nz", "slingshot.co.nz",

    # ── South African ISPs ───────────────────────────────────────────────────
    "mweb.co.za", "webmail.co.za", "vodamail.co.za",

    # ── Canadian ISPs ────────────────────────────────────────────────────────
    "rogers.com", "shaw.ca", "bell.net", "sympatico.ca",
    "telus.net", "videotron.ca", "eastlink.ca",

    # ── Brazilian portals ────────────────────────────────────────────────────
    "uol.com.br", "bol.com.br", "terra.com.br", "ig.com.br",
    "r7.com", "msn.com.br",

    # ── Other Latin American portals ─────────────────────────────────────────
    "fibertel.com.ar",          # Argentina ISP
    "speedy.com.ar",            # Argentina ISP
    "telmex.net.mx",            # Mexico ISP

    # ── Email relay / alias services (like Apple Hide My Email) ──────────────
    "duck.com",                 # DuckDuckGo Email Protection
    "mozmail.com",              # Firefox Relay
    "simplelogin.io", "simplelogin.co", "slmail.me",  # SimpleLogin
    "anonaddy.com", "anonaddy.me",  # AnonAddy / addy.io

    # ── Other privacy-focused / reputable independent providers ──────────────
    "mailbox.org",              # Germany, privacy-first
    "posteo.de", "posteo.net",  # Germany, privacy-first
    "mailfence.com",            # Belgium, encrypted
    "runbox.com",               # Norway, privacy-first
    "startmail.com",            # Netherlands, privacy-first
    "disroot.org",              # Netherlands, open-source community
    "riseup.net",               # Privacy/activism
    "kolabnow.com",             # Switzerland, privacy
    "countermail.com",          # Sweden, encrypted
    "hushmail.com",             # Canada, encrypted
    "lavabit.com",              # Privacy-focused (relaunched)
    "cock.li",                  # Reputable independent provider
    "teknik.io",                # Privacy-focused
})


def _email_domain_allowed(email: str) -> bool:
    """Return True if the email's domain is in the accepted-provider list."""
    parts = email.rsplit("@", 1)
    if len(parts) != 2:
        return False
    return parts[1].lower() in _ALLOWED_EMAIL_DOMAINS


@bp.route("/register", methods=["GET", "POST"])
def register() -> Any:
    """Registration page and form handler."""
    if request.method == "GET":
        if g.user is not None:
            return redirect(url_for("views.index"))
        return render_template("register.html", error=None)
    if _register_is_rate_limited():
        return render_template(
            "register.html",
            error="Too many registration attempts. Please try again later.",
        )
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    if not username or not email or not password:
        return render_template(
            "register.html", error="Please fill in all fields.",
            username=username, email=email,
        )
    if len(username) < 2 or len(username) > 30:
        return render_template(
            "register.html",
            error="Username must be 2-30 characters.",
            username=username, email=email,
        )
    if not re.match(r"^[A-Za-z0-9_-]+$", username):
        return render_template(
            "register.html",
            error="Username may only contain letters, numbers, underscores, and hyphens.",
            username=username, email=email,
        )
    if not _EMAIL_RE.match(email):
        return render_template(
            "register.html",
            error="Please enter a valid email address.",
            username=username, email=email,
        )
    if not _email_domain_allowed(email):
        return render_template(
            "register.html",
            error=(
                "Please use a major email provider such as Gmail, Outlook, "
                "Yahoo, iCloud, or ProtonMail."
            ),
            username=username, email=email,
        )
    if len(email) > 254:
        return render_template(
            "register.html",
            error="Email address is too long.",
            username=username, email=email,
        )
    if get_user_by_email(email):
        return render_template(
            "register.html",
            error="Registration could not be completed. Please check your details and try again.",
            username=username, email=email,
        )
    complexity_error = _password_complexity_error(password)
    if complexity_error:
        return render_template(
            "register.html", error=complexity_error, username=username, email=email,
        )
    if password != confirm:
        return render_template(
            "register.html", error="Passwords do not match.", username=username, email=email,
        )
    _record_register_attempt()
    user_id = create_user(username, password, email)
    if user_id is None:
        return render_template(
            "register.html", error="That username is already taken.",
            username=username, email=email,
        )
    # Send verification email (best-effort; account is created regardless).
    token = secrets.token_urlsafe(32)
    set_email_verification_token(user_id, token)
    base_url = request.host_url
    send_verification_email(email, username, token, base_url)
    # Regenerate session to prevent session fixation.
    loc_id = session.get("location_id")
    session.clear()
    session["user_id"] = user_id
    session["session_version"] = 0  # New user; session_version starts at 0
    session.permanent = True
    # Carry over current location if one is set
    if loc_id:
        session["location_id"] = loc_id
        save_preferences(user_id, location_id=loc_id, default_location_id=loc_id)
    return redirect(url_for("views.index"))


@bp.route("/verify-email/<token>")
def verify_email(token: str) -> Any:
    """Confirm a user's email address via the one-time token link."""
    user = get_user_by_verification_token(token)
    if user is None:
        return render_template(
            "verify_email.html",
            success=False,
            message="This verification link is invalid or has expired.",
        )
    if user["email_confirmed"]:
        return render_template(
            "verify_email.html",
            success=True,
            message="Your email is already verified.",
        )
    confirm_email(user["id"])
    # If the user is currently logged in, refresh g.user so templates reflect
    # the confirmed state immediately.
    if g.user and g.user["id"] == user["id"]:
        g.user["email_confirmed"] = True
    return render_template(
        "verify_email.html",
        success=True,
        message="Your email has been verified. Welcome to Surf & Pier!",
    )


@bp.route("/resend-verification", methods=["POST"])
def resend_verification() -> Any:
    """Resend the email verification link to the logged-in user.

    Protected by two independent throttles:
    - IP-based: 3 resends per 30 minutes per source IP.
    - Per-account: at most one resend every 2 minutes (checked via DB timestamp).
    """
    if g.user is None:
        return redirect(url_for("auth.login"))
    if g.user.get("email_confirmed"):
        return redirect(url_for("auth.account"))
    email = g.user.get("email")
    if not email:
        return redirect(url_for("auth.account"))

    # IP-based rate limit.
    if _is_rate_limited(
        _resend_rate_limit_store,
        _resend_rate_limit_lock,
        _RESEND_RATE_LIMIT_MAX_ATTEMPTS,
        _RESEND_RATE_LIMIT_WINDOW_S,
    ):
        return render_template(
            "verify_pending.html",
            error="Too many resend attempts. Please wait 30 minutes before trying again.",
        )

    # Per-account DB throttle: don't resend if a recent email was just sent.
    sent_at_raw = get_email_verification_sent_at(g.user["id"])
    if sent_at_raw:
        try:
            from datetime import timezone as _tz
            sent_at = datetime.fromisoformat(sent_at_raw).replace(tzinfo=_tz.utc)
            elapsed = (datetime.now(tz=_tz.utc) - sent_at).total_seconds()
            if elapsed < _RESEND_MIN_INTERVAL_S:
                wait = int(_RESEND_MIN_INTERVAL_S - elapsed)
                return render_template(
                    "verify_pending.html",
                    error=f"A verification email was just sent. Please wait {wait} seconds before requesting another.",
                )
        except Exception:
            pass

    _record_attempt(_resend_rate_limit_store, _resend_rate_limit_lock, _RESEND_RATE_LIMIT_WINDOW_S)
    token = secrets.token_urlsafe(32)
    set_email_verification_token(g.user["id"], token)
    base_url = request.host_url
    send_verification_email(email, g.user["username"], token, base_url)
    return redirect(url_for("auth.verify_pending", sent="1"))


@bp.route("/verify-pending")
def verify_pending() -> Any:
    """Holding page shown to logged-in users who have not yet verified their email."""
    if g.user is None:
        return redirect(url_for("auth.login"))
    if g.user.get("email_confirmed"):
        return redirect(url_for("views.index"))
    return render_template(
        "verify_pending.html",
        sent=request.args.get("sent") == "1",
    )


@bp.route("/logout", methods=["POST"])
def logout() -> Any:
    """Log out the current user."""
    session.clear()
    return redirect(url_for("auth.landing"))


@bp.route("/account")
def account() -> Any:
    """Account settings page for logged-in users."""
    if g.user is None:
        return redirect(url_for("auth.login"))
    prefs = get_preferences(g.user["id"])
    prefs.setdefault("notification_prefs", {})
    loc = None
    if prefs.get("location_id"):
        loc = get_location(prefs["location_id"])
    favorites = [get_location(loc_id) for loc_id in prefs.get("favorites", [])]
    favorites = [loc_obj for loc_obj in favorites if loc_obj]
    recent_logs = get_recent_logs(g.user["id"], limit=5)
    passkeys = get_webauthn_credentials(g.user["id"])
    return render_template(
        "account.html",
        prefs=prefs,
        saved_location=loc,
        recent_logs=recent_logs,
        favorite_locations=favorites,
        passkeys=passkeys,
    )


@bp.route("/account/settings", methods=["POST"])
def account_settings() -> Any:
    if g.user is None:
        return redirect(url_for("auth.login"))

    wind_units = request.form.get("wind_units", "knots")
    if wind_units not in {"knots", "mph"}:
        wind_units = "knots"
    temp_units = request.form.get("temp_units", "F")
    if temp_units not in {"F", "C"}:
        temp_units = "F"
    weekly_email = request.form.get("weekly_email") == "on"
    favorite_ids = [
        loc_id.strip()
        for loc_id in request.form.get("favorites_csv", "").split(",")
        if loc_id.strip()
    ]
    # Only keep favorites that resolve to real locations
    favorite_ids = [loc_id for loc_id in favorite_ids if get_location(loc_id)]
    default_location_id = request.form.get("default_location_id", "").strip() or None
    if default_location_id and not get_location(default_location_id):
        default_location_id = None

    save_preferences(
        g.user["id"],
        wind_units=wind_units,
        temp_units=temp_units,
        units=temp_units,
        notification_prefs={"weekly_email": weekly_email},
        favorites=favorite_ids,
        default_location_id=default_location_id,
    )
    if default_location_id:
        session["location_id"] = default_location_id
    return redirect(url_for("auth.account", saved="1"))


@bp.route("/account/change-password", methods=["POST"])
def change_password_route() -> Any:
    """Change the current user's password."""
    if g.user is None:
        return redirect(url_for("auth.login"))

    def _pw_error(msg: str) -> Any:
        prefs = get_preferences(g.user["id"])
        prefs.setdefault("notification_prefs", {})
        return render_template(
            "account.html",
            prefs=prefs,
            saved_location=None,
            recent_logs=[],
            favorite_locations=[],
            pw_error=msg,
        )

    if _account_action_is_rate_limited():
        return _pw_error("Too many attempts. Please wait before trying again.")

    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")

    # Verify the current password before allowing any change.
    stored_hash = get_user_password_hash(g.user["id"])
    if not stored_hash or not check_password_hash(stored_hash, current_pw):
        _record_account_action_failure()
        return _pw_error("Current password is incorrect.")

    complexity_error = _password_complexity_error(new_pw)
    if complexity_error:
        return _pw_error(complexity_error)

    if new_pw != confirm_pw:
        return _pw_error("New passwords do not match.")

    # change_password() atomically updates the hash and bumps session_version.
    # Store the new version in the session so the current browser stays logged in
    # while all other devices/sessions are immediately invalidated.
    new_version = change_password(g.user["id"], new_pw)
    session["session_version"] = new_version
    _clear_account_action_failures()
    return redirect(url_for("auth.account", saved="1"))


@bp.route("/account/delete", methods=["POST"])
def delete_account_route() -> Any:
    """Permanently delete the current user's account and all associated data."""
    if g.user is None:
        return redirect(url_for("auth.login"))

    def _del_error(msg: str) -> Any:
        prefs = get_preferences(g.user["id"])
        prefs.setdefault("notification_prefs", {})
        return render_template(
            "account.html",
            prefs=prefs,
            saved_location=None,
            recent_logs=[],
            favorite_locations=[],
            delete_error=msg,
        )

    if _account_action_is_rate_limited():
        return _del_error("Too many attempts. Please wait before trying again.")

    password = request.form.get("password", "")

    # Require password confirmation before destructive action.
    stored_hash = get_user_password_hash(g.user["id"])
    if not stored_hash or not check_password_hash(stored_hash, password):
        _record_account_action_failure()
        return _del_error("Incorrect password. Account not deleted.")

    user_id = g.user["id"]

    # Remove all uploaded photo files from disk before deleting the DB rows.
    upload_root = current_app.config.get("UPLOAD_FOLDER", "")
    if upload_root:
        upload_root_real = os.path.realpath(upload_root)
        for rel_path in get_all_user_photo_paths(user_id):
            if not rel_path:
                continue
            sub = rel_path[len("uploads/"):] if rel_path.startswith("uploads/") else rel_path
            abs_path = os.path.realpath(os.path.join(upload_root, sub))
            if abs_path.startswith(upload_root_real + os.sep):
                try:
                    os.remove(abs_path)
                except OSError:
                    logger.warning("Could not remove photo file during account deletion: %s", rel_path)

    delete_user(user_id)
    session.clear()
    return redirect(url_for("auth.landing"))


# ── WebAuthn / passkey (biometric) endpoints ──────────────────────────────────

def _webauthn_rp_id() -> str:
    return request.host.split(":")[0]


def _webauthn_origin() -> str:
    return request.scheme + "://" + request.host


@bp.route("/webauthn/register/begin")
def webauthn_register_begin() -> Any:
    """Return WebAuthn registration options for the logged-in user."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

    if not g.user:
        return jsonify({"error": "Not logged in"}), 401

    existing = get_webauthn_credentials(g.user["id"])
    options = generate_registration_options(
        rp_id=_webauthn_rp_id(),
        rp_name="Surf & Pier Fishing Forecast",
        user_id=str(g.user["id"]).encode(),
        user_name=g.user["username"],
        user_display_name=g.user["username"],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"]))
            for c in existing
        ],
    )
    session["webauthn_reg_challenge"] = bytes_to_base64url(options.challenge)
    session["webauthn_reg_origin"] = _webauthn_origin()
    return options_to_json(options), 200, {"Content-Type": "application/json"}


@bp.route("/webauthn/register/complete", methods=["POST"])
def webauthn_register_complete() -> Any:
    """Verify the registration response and store the new credential."""
    from webauthn import verify_registration_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

    if not g.user:
        return jsonify({"error": "Not logged in"}), 401

    challenge_b64 = session.pop("webauthn_reg_challenge", None)
    origin = session.pop("webauthn_reg_origin", None)
    if not challenge_b64 or not origin:
        return jsonify({"error": "No challenge in session"}), 400

    try:
        verified = verify_registration_response(
            credential=request.json,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=_webauthn_rp_id(),
            expected_origin=origin,
        )
    except Exception as exc:
        logger.warning("webauthn.register_failed user_id=%s: %s", g.user["id"], exc)
        return jsonify({"error": "Registration failed"}), 400

    save_webauthn_credential(
        user_id=g.user["id"],
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count,
    )
    logger.info("webauthn.register_complete user_id=%s ip=%s", g.user["id"], _client_ip())
    return jsonify({"ok": True})


@bp.route("/webauthn/authenticate/begin", methods=["POST"])
def webauthn_authenticate_begin() -> Any:
    """Return WebAuthn authentication options (discoverable credentials)."""
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    from webauthn.helpers import bytes_to_base64url

    options = generate_authentication_options(
        rp_id=_webauthn_rp_id(),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    session["webauthn_auth_challenge"] = bytes_to_base64url(options.challenge)
    session["webauthn_auth_origin"] = _webauthn_origin()
    return options_to_json(options), 200, {"Content-Type": "application/json"}


@bp.route("/webauthn/authenticate/complete", methods=["POST"])
def webauthn_authenticate_complete() -> Any:
    """Verify the authentication response and log the user in."""
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

    challenge_b64 = session.pop("webauthn_auth_challenge", None)
    origin = session.pop("webauthn_auth_origin", None)
    if not challenge_b64 or not origin:
        return jsonify({"error": "No challenge in session"}), 400

    data = request.json or {}
    credential_id = data.get("id", "")
    stored = get_webauthn_credential_by_id(credential_id)
    if not stored:
        return jsonify({"error": "Unknown credential"}), 400

    try:
        verified = verify_authentication_response(
            credential=data,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=_webauthn_rp_id(),
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(stored["public_key"]),
            credential_current_sign_count=stored["sign_count"],
        )
    except Exception as exc:
        logger.warning("webauthn.auth_failed credential_id=%s: %s", credential_id, exc)
        return jsonify({"error": "Authentication failed"}), 400

    update_webauthn_sign_count(credential_id, verified.new_sign_count)

    user = get_user(stored["user_id"])
    if not user:
        return jsonify({"error": "User not found"}), 400

    prior_location_id = session.get("location_id")
    session.clear()
    new_version = bump_session_version(user["id"])
    session["user_id"] = user["id"]
    session["session_version"] = new_version
    session.permanent = True
    session["csrf_token"] = secrets.token_urlsafe(24)
    prefs = get_preferences(user["id"])
    if prefs.get("location_id"):
        session["location_id"] = prefs["location_id"]
    elif user.get("default_location_id"):
        session["location_id"] = user["default_location_id"]
    elif prior_location_id:
        session["location_id"] = prior_location_id

    logger.info("webauthn.auth_complete user_id=%s ip=%s", user["id"], _client_ip())
    return jsonify({"ok": True, "redirect": url_for("views.index")})


@bp.route("/webauthn/credential/<credential_id>/delete", methods=["POST"])
def webauthn_delete_credential(credential_id: str) -> Any:
    """Remove a registered passkey from the logged-in user's account."""
    if not g.user:
        return jsonify({"error": "Not logged in"}), 401
    deleted = delete_webauthn_credential(credential_id, g.user["id"])
    return jsonify({"ok": deleted})
