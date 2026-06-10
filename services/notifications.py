"""Opt-in fishing-condition notifications (email + web push).

A lightweight daily-digest style notifier: a background poller periodically
scans users who opted in (``notification_prefs.enabled``) and, for each of
their saved locations, checks whether today's personalized forecast meets the
user's minimum rating.  When it does — and the user hasn't already been
notified for that location today — an alert is sent over the channels they
enabled (email and/or web push).

Everything here is a safe no-op until a user opts in *and* a channel is
configured (SMTP for email, VAPID for push), so importing/starting the poller
has no outward effect in development.

The pure decision logic (:func:`evaluate_forecast`) is separated from the I/O
(:func:`run_notification_check`) so it can be unit-tested without a database,
network, or real send.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from locations import get_location
from storage.sqlite import (
    add_push_subscription,  # noqa: F401 (re-exported for convenience)
    get_push_subscriptions,
    iter_notification_candidates,
    record_notification,
    was_notified,
)
from utils import safe_zone as _safe_zone

logger = logging.getLogger(__name__)

# Ordinal ranking of the forecast verdict labels (higher = better fishing).
_RATING_ORDER: dict[str, int] = {
    "Poor": 1,
    "Challenging": 2,
    "Fair": 3,
    "Good": 4,
    "Excellent": 5,
}

# Activity-timeline tags that count as a genuinely good upcoming window.
_GOOD_TAGS = frozenset({"high", "prime"})

ForecastLoader = Callable[[str, dict[str, Any], dict[str, Any]], Optional[dict[str, Any]]]
EmailSender = Callable[[str, str, str, str], bool]
PushSender = Callable[[dict[str, str], str, str, str], bool]


# ---------------------------------------------------------------------------
# Pure decision logic
# ---------------------------------------------------------------------------


def _effective_verdict(forecast: dict[str, Any]) -> str:
    """The most user-specific verdict available on a forecast."""
    cond = forecast.get("conditions", {}) or {}
    return (
        cond.get("verdict_for_angler")
        or cond.get("verdict_for_type")
        or cond.get("verdict")
        or ""
    )


def _has_upcoming_good_window(
    forecast: dict[str, Any], now: datetime, lead_hours: int
) -> bool:
    """True if a high/prime activity hour falls within the next *lead_hours*."""
    timeline = forecast.get("activity_timeline") or []
    if not timeline:
        return False
    end_hour = now.hour + lead_hours
    for entry in timeline:
        h = entry.get("hour")
        if h is None:
            continue
        if now.hour < h <= end_hour and entry.get("tag") in _GOOD_TAGS:
            return True
    return False


def evaluate_forecast(
    forecast: dict[str, Any],
    prefs: dict[str, Any],
    now: datetime,
) -> Optional[dict[str, Any]]:
    """Decide whether *forecast* warrants an alert under *prefs*.

    Returns a small dict describing the alert (verdict, score, headline
    window) when it qualifies, or ``None`` when it does not.

    Rules:
      - The effective verdict must meet ``min_rating`` (default "Good").
      - When ``lead_hours`` > 0 there must additionally be a high/prime
        activity hour within that lead window, so the nudge is timely.
    """
    verdict = _effective_verdict(forecast)
    if not verdict:
        return None

    min_rating = prefs.get("min_rating") or "Good"
    if _RATING_ORDER.get(verdict, 0) < _RATING_ORDER.get(min_rating, 4):
        return None

    lead_hours = int(prefs.get("lead_hours") or 0)
    if lead_hours > 0 and not _has_upcoming_good_window(forecast, now, lead_hours):
        return None

    cond = forecast.get("conditions", {}) or {}
    score = cond.get("fishability_score_for_angler")
    if score is None:
        score = cond.get("fishability_score")

    best_times = forecast.get("best_times") or []
    headline = best_times[0] if best_times else {}

    return {
        "verdict": verdict,
        "score": score,
        "summary": cond.get("summary", ""),
        "window": headline.get("window", ""),
        "window_reason": headline.get("reason", ""),
        "best_times": best_times[:3],
    }


# ---------------------------------------------------------------------------
# Message rendering
# ---------------------------------------------------------------------------


def build_email(
    location_name: str, decision: dict[str, Any], manage_url: str = ""
) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for an alert email.

    *manage_url* (when given) is appended as a "manage alerts" link so the
    recipient can adjust or turn off notifications.
    """
    verdict = decision["verdict"]
    score = decision.get("score")
    score_str = f" ({score}/100)" if score is not None else ""
    subject = f"{verdict} fishing at {location_name} today"

    lines = [f"{verdict}{score_str} fishing conditions at {location_name} today."]
    if decision.get("summary"):
        lines.append("")
        lines.append(decision["summary"])
    if decision.get("best_times"):
        lines.append("")
        lines.append("Best windows:")
        for bt in decision["best_times"]:
            win = bt.get("window", "")
            reason = bt.get("reason", "")
            lines.append(f"  - {win}{(' — ' + reason) if reason else ''}")
    lines.append("")
    lines.append("Tight lines! — Surf & Pier Fishing Forecast")
    if manage_url:
        lines.append("")
        lines.append(f"Manage or turn off alerts: {manage_url}")
    text_body = "\n".join(lines)

    wins_html = "".join(
        f"<li><strong>{bt.get('window','')}</strong>"
        f"{(' — ' + bt.get('reason','')) if bt.get('reason') else ''}</li>"
        for bt in decision.get("best_times", [])
    )
    manage_html = (
        f'<p style="font-size:12px;color:#888">'
        f'<a href="{manage_url}">Manage or turn off alerts</a></p>'
        if manage_url
        else ""
    )
    html_body = (
        f"<h2>{verdict}{score_str} fishing at {location_name} today</h2>"
        + (f"<p>{decision['summary']}</p>" if decision.get("summary") else "")
        + (f"<p><strong>Best windows:</strong></p><ul>{wins_html}</ul>" if wins_html else "")
        + "<p>Tight lines! — Surf &amp; Pier Fishing Forecast</p>"
        + manage_html
    )
    return subject, text_body, html_body


# ---------------------------------------------------------------------------
# Orchestration (I/O)
# ---------------------------------------------------------------------------


def _user_location_ids(candidate: dict[str, Any]) -> list[str]:
    """Deduplicated list of a user's saved locations (favorites + default)."""
    ids: list[str] = []
    for loc_id in candidate.get("favorites") or []:
        if loc_id and loc_id not in ids:
            ids.append(loc_id)
    default = candidate.get("default_location_id")
    if default and default not in ids:
        ids.append(default)
    return ids


def _location_now(location: dict[str, Any], now: Optional[datetime]) -> datetime:
    """Current time in the location's timezone."""
    tz = _safe_zone(location.get("timezone", "America/New_York"))
    base = now or datetime.now(tz)
    # Re-express an aware/naive reference time in the location timezone.
    if base.tzinfo is None:
        return base.replace(tzinfo=tz)
    return base.astimezone(tz)


def _default_forecast_loader(
    location_id: str, location: dict[str, Any], profile: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Load a cached forecast (or generate one), personalized for the user."""
    # Imported lazily so unit tests can run without the heavy forecast stack.
    from domain.forecast import generate_forecast, personalize_forecast
    from storage.cache import load_cached_forecast, save_forecast

    forecast = load_cached_forecast(location_id, user_id=None, include_stale=True)
    if forecast is None:
        forecast = generate_forecast(location)
        save_forecast(forecast, location_id, user_id=None)
    if profile:
        try:
            forecast = personalize_forecast(forecast, profile, location)
        except Exception:
            logger.debug("notify.personalize_failed loc=%s", location_id, exc_info=True)
    return forecast


def run_notification_check(
    now: Optional[datetime] = None,
    *,
    forecast_loader: ForecastLoader = _default_forecast_loader,
    email_sender: Optional[EmailSender] = None,
    push_sender: Optional[PushSender] = None,
    site_url: str = "",
) -> dict[str, int]:
    """Scan opted-in users and send any due notifications.

    Side-effecting collaborators are injected so this is fully testable.
    Returns a small summary dict: {candidates, considered, sent}.
    """
    send_email_fn: EmailSender
    if email_sender is None:
        from services.email import send_email

        send_email_fn = send_email
    else:
        send_email_fn = email_sender

    push_fn: PushSender
    if push_sender is None:
        from services.push import send_push

        push_fn = send_push
    else:
        push_fn = push_sender

    candidates = iter_notification_candidates()
    considered = 0
    sent = 0

    for cand in candidates:
        prefs = cand.get("notification_prefs") or {}
        if not prefs.get("enabled"):
            continue
        uid = cand["user_id"]
        profile = cand.get("fishing_profile") or {}

        for loc_id in _user_location_ids(cand):
            location = get_location(loc_id)
            if not location:
                continue
            considered += 1
            local_now = _location_now(location, now)
            sent_date = local_now.strftime("%Y-%m-%d")
            if was_notified(uid, loc_id, sent_date):
                continue

            try:
                forecast = forecast_loader(loc_id, location, profile)
            except Exception:
                logger.warning(
                    "notify.forecast_failed user=%s loc=%s", uid, loc_id, exc_info=True
                )
                continue
            if not forecast:
                continue

            decision = evaluate_forecast(forecast, prefs, local_now)
            if not decision:
                continue

            loc_name = location.get("name", loc_id)
            channels: list[str] = []

            if prefs.get("email") and cand.get("email"):
                manage_url = f"{site_url}/account" if site_url else ""
                subject, text_body, html_body = build_email(
                    loc_name, decision, manage_url=manage_url
                )
                try:
                    if send_email_fn(cand["email"], subject, text_body, html_body):
                        channels.append("email")
                except Exception:
                    logger.warning("notify.email_failed user=%s", uid, exc_info=True)

            if prefs.get("push"):
                title = f"{decision['verdict']} fishing at {loc_name}"
                body = decision.get("window") or decision.get("summary") or ""
                url = f"{site_url}/f/{loc_id}" if site_url else f"/f/{loc_id}"
                pushed_any = False
                for sub in get_push_subscriptions(uid):
                    try:
                        if push_fn(sub, title, body, url):
                            pushed_any = True
                    except Exception:
                        logger.debug(
                            "notify.push_failed user=%s", uid, exc_info=True
                        )
                if pushed_any:
                    channels.append("push")

            if channels:
                record_notification(
                    uid, loc_id, sent_date, decision.get("window", ""), ",".join(channels)
                )
                sent += 1
                logger.info(
                    "notify.sent user=%s loc=%s channels=%s", uid, loc_id, channels
                )

    return {"candidates": len(candidates), "considered": considered, "sent": sent}


# ---------------------------------------------------------------------------
# Background poller
# ---------------------------------------------------------------------------

_poller_thread: Optional[threading.Thread] = None
_poller_lock = threading.Lock()

# Default: check every 15 minutes.  Set NOTIFICATION_POLL_INTERVAL=0 (or
# NOTIFICATIONS_ENABLED=0) to disable the background poller entirely.
_POLL_INTERVAL_S = int(os.environ.get("NOTIFICATION_POLL_INTERVAL", "900"))
_ENABLED = os.environ.get("NOTIFICATIONS_ENABLED", "1") != "0"
_SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")


def _poller_loop(interval_s: int) -> None:
    while True:
        time.sleep(interval_s)
        try:
            summary = run_notification_check(site_url=_SITE_URL)
            if summary["sent"]:
                logger.info("notify.poll_summary %s", summary)
        except Exception:
            logger.exception("notify.poll_failed")


def start_notification_poller() -> bool:
    """Start the background notification poller (idempotent).

    Returns True if a poller is running after the call, False if disabled.
    The poller is a cheap DB scan when no users have opted in, and never sends
    anything unless a channel is configured, so it is safe to always start.
    """
    global _poller_thread
    if not _ENABLED or _POLL_INTERVAL_S <= 0:
        logger.info("notify.poller_disabled")
        return False
    with _poller_lock:
        if _poller_thread is not None and _poller_thread.is_alive():
            return True
        t = threading.Thread(
            target=_poller_loop,
            args=(_POLL_INTERVAL_S,),
            name="notification-poller",
            daemon=True,
        )
        t.start()
        _poller_thread = t
        logger.info("notify.poller_started interval_s=%s", _POLL_INTERVAL_S)
    return True
