"""Background forecast refresh queue (threaded, local/prototype friendly)."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional, Set, Tuple

from domain.forecast import generate_forecast
from locations import get_location
from storage.cache import save_forecast

logger = logging.getLogger(__name__)

QueueKey = Tuple[str, int]

_refresh_queue: "queue.Queue[QueueKey]" = queue.Queue()
_refresh_lock = threading.Lock()
_refreshing: Set[QueueKey] = set()
_enqueued: Set[QueueKey] = set()
_worker_started = False


def _norm_user_id(user_id: Optional[int]) -> int:
    return int(user_id or 0)


def refresh_forecast(location_id: str, user_id: Optional[int] = None) -> bool:
    """Generate and persist a fresh forecast for a location."""
    location = get_location(location_id)
    if location is None:
        logger.warning("refresh.invalid_location location_id=%s", location_id)
        return False

    forecast = generate_forecast(location)
    save_forecast(forecast, location_id, user_id=user_id)
    logger.info("refresh.completed location_id=%s user_id=%s", location_id, user_id or 0)
    return True


def _worker_loop() -> None:
    while True:
        key = _refresh_queue.get()
        location_id, normalized_uid = key
        with _refresh_lock:
            _enqueued.discard(key)
            _refreshing.add(key)
        try:
            refresh_forecast(location_id, user_id=normalized_uid or None)
        except Exception:
            logger.exception("refresh.failed location_id=%s user_id=%s", location_id, normalized_uid)
        finally:
            with _refresh_lock:
                _refreshing.discard(key)
            _refresh_queue.task_done()


def _ensure_worker_started() -> None:
    global _worker_started
    with _refresh_lock:
        if _worker_started:
            return
        worker = threading.Thread(target=_worker_loop, name="forecast-refresh-worker", daemon=True)
        worker.start()
        _worker_started = True


def enqueue_forecast_refresh(location_id: str, user_id: Optional[int] = None) -> bool:
    """Queue a refresh if one is not already queued/running for this location/user."""
    key = (location_id, _norm_user_id(user_id))
    _ensure_worker_started()
    with _refresh_lock:
        if key in _enqueued or key in _refreshing:
            return False
        _enqueued.add(key)
    _refresh_queue.put(key)
    logger.info("refresh.enqueued location_id=%s user_id=%s", key[0], key[1])
    return True


def is_refreshing(location_id: str, user_id: Optional[int] = None) -> bool:
    """Best-effort signal for UI/API polling."""
    key = (location_id, _norm_user_id(user_id))
    with _refresh_lock:
        return key in _enqueued or key in _refreshing
