"""Shared HTTP client with timeouts, retries, and structured logging."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: tuple[float, float] = (3.05, 10.0)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}

# Shared session with a connection pool so TCP+TLS handshakes are reused
# across the 20+ external API calls made per forecast pipeline run.
# max_retries=0: we handle retries ourselves in get() below.
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)
_session.headers.update(
    {
        "User-Agent": "surf-pier-forecast/1.0 (+https://github.com/connnnerday/surf-pier-forecast)"
    }
)

# Short-lived response cache: prevents duplicate network calls when concurrent
# background refreshes for the same location overlap (each fires 20+ GET requests).
# Only 2xx responses are cached; TTL is short enough that stale data isn't a concern
# given the 4-hour forecast TTL.
_RESPONSE_CACHE: dict[tuple, tuple[float, int, bytes, Optional[str]]] = {}
_RESPONSE_CACHE_TTL = 600  # 10 minutes
_RESPONSE_CACHE_MAX = 128
_RESPONSE_CACHE_LOCK = threading.Lock()
_RESPONSE_CACHE_MAX_BYTES = 512 * 1024  # skip caching responses > 512 KB


def _cache_key(url: str, headers: Optional[dict[str, str]]) -> tuple:
    if not headers:
        return (url,)
    return (url, tuple(sorted(headers.items())))


def _cache_get(
    url: str, headers: Optional[dict[str, str]]
) -> Optional[requests.Response]:
    key = _cache_key(url, headers)
    with _RESPONSE_CACHE_LOCK:
        entry = _RESPONSE_CACHE.get(key)
    if entry is None:
        return None
    cached_at, status_code, content, encoding = entry
    if time.time() - cached_at > _RESPONSE_CACHE_TTL:
        with _RESPONSE_CACHE_LOCK:
            _RESPONSE_CACHE.pop(key, None)
        return None
    resp = requests.models.Response()
    resp.status_code = status_code
    resp._content = content  # type: ignore[attr-defined]
    resp.encoding = encoding
    return resp


def _cache_set(
    url: str, headers: Optional[dict[str, str]], response: requests.Response
) -> None:
    content = response.content
    if len(content) > _RESPONSE_CACHE_MAX_BYTES:
        return
    key = _cache_key(url, headers)
    with _RESPONSE_CACHE_LOCK:
        if len(_RESPONSE_CACHE) >= _RESPONSE_CACHE_MAX:
            oldest = min(_RESPONSE_CACHE, key=lambda k: _RESPONSE_CACHE[k][0])
            _RESPONSE_CACHE.pop(oldest, None)
        _RESPONSE_CACHE[key] = (
            time.time(),
            response.status_code,
            content,
            response.encoding,
        )


def get(
    url: str,
    *,
    endpoint: str,
    headers: Optional[dict[str, str]] = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = 2,
    backoff_s: float = 0.25,
    use_cache: bool = True,
) -> requests.Response:
    """GET with bounded timeout and retry/backoff for transient failures."""
    if retries < 0:
        raise ValueError(f"retries must be >= 0, got {retries}")

    if use_cache:
        cached = _cache_get(url, headers)
        if cached is not None:
            logger.debug("external_call.cache_hit endpoint=%s", endpoint)
            return cached

    for attempt in range(1, retries + 2):
        start = time.perf_counter()
        try:
            response = _session.get(url, headers=headers, timeout=timeout)
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            status = response.status_code

            if status in TRANSIENT_STATUS_CODES and attempt <= retries:
                logger.warning(
                    "external_call.retry endpoint=%s attempt=%s status=%s latency_ms=%s",
                    endpoint,
                    attempt,
                    status,
                    latency_ms,
                )
                time.sleep(backoff_s * (2 ** (attempt - 1)))
                continue

            logger.info(
                "external_call.done endpoint=%s success=%s status=%s latency_ms=%s attempt=%s",
                endpoint,
                status < 400,
                status,
                latency_ms,
                attempt,
            )
            if use_cache and 200 <= status < 300:
                _cache_set(url, headers, response)
            return response
        except requests.RequestException as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            if attempt <= retries:
                logger.warning(
                    "external_call.retry endpoint=%s attempt=%s error=%s latency_ms=%s",
                    endpoint,
                    attempt,
                    exc.__class__.__name__,
                    latency_ms,
                )
                time.sleep(backoff_s * (2 ** (attempt - 1)))
                continue

            logger.error(
                "external_call.failed endpoint=%s attempt=%s error=%s latency_ms=%s",
                endpoint,
                attempt,
                exc.__class__.__name__,
                latency_ms,
            )
            raise

    # Unreachable: the loop always returns or raises on the last attempt.
    raise AssertionError("http_client.get: loop exited without returning or raising")
