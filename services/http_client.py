"""Shared HTTP client with timeouts, retries, and structured logging."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: Tuple[float, float] = (3.05, 10.0)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def get(
    url: str,
    *,
    endpoint: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = 2,
    backoff_s: float = 0.25,
) -> requests.Response:
    """GET with bounded timeout and retry/backoff for transient failures."""
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 2):
        start = time.perf_counter()
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
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
            last_error = exc
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

    if last_error:
        raise last_error
    raise RuntimeError("HTTP client failed unexpectedly")
