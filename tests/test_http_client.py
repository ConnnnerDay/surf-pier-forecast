"""Tests for services/http_client.py — shared retry/timeout/cache GET wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

import services.http_client as http_client


@pytest.fixture(autouse=True)
def _clear_response_cache():
    """Each test gets a clean response cache so hits don't bleed across tests."""
    http_client._RESPONSE_CACHE.clear()
    yield
    http_client._RESPONSE_CACHE.clear()


def _mock_response(status_code: int = 200, content: bytes = b"ok") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = content
    resp.encoding = "utf-8"
    return resp


class TestGetSuccess:
    def test_returns_response_on_first_success(self):
        resp = _mock_response(200)
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            result = http_client.get("https://example.com/a", endpoint="test", use_cache=False)
        assert result.status_code == 200
        mock_get.assert_called_once()

    def test_passes_through_headers_and_timeout(self):
        resp = _mock_response(200)
        headers = {"Accept": "application/json"}
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            http_client.get(
                "https://example.com/a",
                endpoint="test",
                headers=headers,
                timeout=(1.0, 2.0),
                use_cache=False,
            )
        _, kwargs = mock_get.call_args
        assert kwargs["headers"] == headers
        assert kwargs["timeout"] == (1.0, 2.0)

    def test_4xx_status_is_returned_without_retry(self):
        resp = _mock_response(404)
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            result = http_client.get("https://example.com/missing", endpoint="test", use_cache=False)
        assert result.status_code == 404
        mock_get.assert_called_once()


class TestGetValidation:
    def test_negative_retries_raises(self):
        with pytest.raises(ValueError):
            http_client.get("https://example.com/a", endpoint="test", retries=-1)


class TestRetryOnTransientStatus:
    def test_retries_on_503_then_succeeds(self):
        bad = _mock_response(503)
        good = _mock_response(200)
        with patch.object(http_client._session, "get", side_effect=[bad, good]) as mock_get, \
                patch.object(http_client.time, "sleep") as mock_sleep:
            result = http_client.get(
                "https://example.com/a", endpoint="test", retries=2, use_cache=False
            )
        assert result.status_code == 200
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once()

    def test_exhausts_retries_and_returns_last_transient_response(self):
        bad = _mock_response(500)
        with patch.object(http_client._session, "get", return_value=bad) as mock_get, \
                patch.object(http_client.time, "sleep"):
            result = http_client.get(
                "https://example.com/a", endpoint="test", retries=2, use_cache=False
            )
        assert result.status_code == 500
        # initial attempt + 2 retries = 3 calls
        assert mock_get.call_count == 3

    def test_zero_retries_does_not_retry_on_transient_status(self):
        bad = _mock_response(429)
        with patch.object(http_client._session, "get", return_value=bad) as mock_get:
            result = http_client.get(
                "https://example.com/a", endpoint="test", retries=0, use_cache=False
            )
        assert result.status_code == 429
        mock_get.assert_called_once()

    def test_backoff_uses_exponential_delay(self):
        bad = _mock_response(502)
        good = _mock_response(200)
        with patch.object(http_client._session, "get", side_effect=[bad, bad, good]) as mock_get, \
                patch.object(http_client.time, "sleep") as mock_sleep:
            http_client.get(
                "https://example.com/a",
                endpoint="test",
                retries=2,
                backoff_s=0.25,
                use_cache=False,
            )
        assert mock_get.call_count == 3
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [0.25, 0.5]


class TestRetryOnException:
    def test_retries_on_connection_error_then_succeeds(self):
        good = _mock_response(200)
        with patch.object(
            http_client._session,
            "get",
            side_effect=[requests.ConnectionError("boom"), good],
        ) as mock_get, patch.object(http_client.time, "sleep"):
            result = http_client.get(
                "https://example.com/a", endpoint="test", retries=2, use_cache=False
            )
        assert result.status_code == 200
        assert mock_get.call_count == 2

    def test_raises_after_exhausting_retries_on_exception(self):
        with patch.object(
            http_client._session, "get", side_effect=requests.Timeout("slow")
        ) as mock_get, patch.object(http_client.time, "sleep"):
            with pytest.raises(requests.Timeout):
                http_client.get(
                    "https://example.com/a", endpoint="test", retries=1, use_cache=False
                )
        assert mock_get.call_count == 2

    def test_zero_retries_raises_immediately_on_exception(self):
        with patch.object(
            http_client._session, "get", side_effect=requests.ConnectionError("boom")
        ) as mock_get:
            with pytest.raises(requests.ConnectionError):
                http_client.get(
                    "https://example.com/a", endpoint="test", retries=0, use_cache=False
                )
        mock_get.assert_called_once()


class TestResponseCache:
    def test_second_call_with_cache_hits_cache_not_network(self):
        resp = _mock_response(200, content=b"cached-body")
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            first = http_client.get("https://example.com/cacheme", endpoint="test", use_cache=True)
            second = http_client.get("https://example.com/cacheme", endpoint="test", use_cache=True)
        assert mock_get.call_count == 1
        assert first.content == b"cached-body"
        assert second.content == b"cached-body"

    def test_use_cache_false_always_hits_network(self):
        resp = _mock_response(200)
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            http_client.get("https://example.com/nocache", endpoint="test", use_cache=False)
            http_client.get("https://example.com/nocache", endpoint="test", use_cache=False)
        assert mock_get.call_count == 2

    def test_non_2xx_response_is_not_cached(self):
        resp = _mock_response(500)
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            http_client.get(
                "https://example.com/err", endpoint="test", retries=0, use_cache=True
            )
            http_client.get(
                "https://example.com/err", endpoint="test", retries=0, use_cache=True
            )
        assert mock_get.call_count == 2

    def test_different_headers_produce_different_cache_keys(self):
        resp = _mock_response(200)
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            http_client.get(
                "https://example.com/h", endpoint="test", headers={"A": "1"}, use_cache=True
            )
            http_client.get(
                "https://example.com/h", endpoint="test", headers={"A": "2"}, use_cache=True
            )
        assert mock_get.call_count == 2

    def test_oversized_response_is_not_cached(self):
        big = _mock_response(200, content=b"x" * (http_client._RESPONSE_CACHE_MAX_BYTES + 1))
        with patch.object(http_client._session, "get", return_value=big) as mock_get:
            http_client.get("https://example.com/big", endpoint="test", use_cache=True)
            http_client.get("https://example.com/big", endpoint="test", use_cache=True)
        assert mock_get.call_count == 2

    def test_cache_expires_after_ttl(self):
        resp = _mock_response(200)
        with patch.object(http_client._session, "get", return_value=resp) as mock_get:
            http_client.get("https://example.com/ttl", endpoint="test", use_cache=True)
            # Simulate TTL expiry by back-dating the cache entry.
            key = http_client._cache_key("https://example.com/ttl", None)
            cached_at, status_code, content, encoding = http_client._RESPONSE_CACHE[key]
            http_client._RESPONSE_CACHE[key] = (
                cached_at - http_client._RESPONSE_CACHE_TTL - 1,
                status_code,
                content,
                encoding,
            )
            http_client.get("https://example.com/ttl", endpoint="test", use_cache=True)
        assert mock_get.call_count == 2

    def test_cache_evicts_oldest_when_full(self):
        resp = _mock_response(200)
        with patch.object(http_client._session, "get", return_value=resp):
            for i in range(http_client._RESPONSE_CACHE_MAX):
                http_client.get(f"https://example.com/{i}", endpoint="test", use_cache=True)
        assert len(http_client._RESPONSE_CACHE) == http_client._RESPONSE_CACHE_MAX
        with patch.object(http_client._session, "get", return_value=resp):
            http_client.get("https://example.com/overflow", endpoint="test", use_cache=True)
        assert len(http_client._RESPONSE_CACHE) == http_client._RESPONSE_CACHE_MAX


class TestCacheKey:
    def test_no_headers_key_is_url_tuple(self):
        assert http_client._cache_key("https://example.com", None) == ("https://example.com",)

    def test_headers_are_order_independent(self):
        key_a = http_client._cache_key("https://example.com", {"A": "1", "B": "2"})
        key_b = http_client._cache_key("https://example.com", {"B": "2", "A": "1"})
        assert key_a == key_b
