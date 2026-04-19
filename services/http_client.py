"""Shared HTTP client with timeouts, retries, and structured logging."""

from __future__ import annotations

import logging
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


def get(
    url: str,
    *,
    endpoint: str,
    headers: Optional[dict[str, str]] = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = 2,
    backoff_s: float = 0.25,
) -> requests.Response:
    """GET with bounded timeout and retry/backoff for transient failures."""
    if retries < 0:
        raise ValueError(f"retries must be >= 0, got {retries}")

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
