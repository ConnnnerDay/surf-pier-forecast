"""Tests for app.infra.snapshot_cache.SnapshotCache.

Covers the sprint's named acceptance matrix: fresh hit, stale hit, miss,
expiry, fallback-on-fetch-failure, and single-flight concurrency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.infra.snapshot_cache import SnapshotCache

_START = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _make_cache(
    clock: _FakeClock, *, fresh_ttl: float = 3600.0, stale_ttl: float = 7200.0
) -> SnapshotCache[str]:
    return SnapshotCache(
        fresh_ttl_seconds=fresh_ttl, stale_ttl_seconds=stale_ttl, clock=clock
    )


def _fetcher(*values: str) -> tuple[Callable[[], Awaitable[str]], list[int]]:
    """Returns a fetch callable yielding *values* in order, plus a
    call-count list (mutable box, since closures can't rebind an int).
    """
    calls = [0]
    iterator = iter(values)

    async def fetch() -> str:
        calls[0] += 1
        return next(iterator)

    return fetch, calls


def _failing_fetcher() -> tuple[Callable[[], Awaitable[str]], list[int]]:
    calls = [0]

    async def fetch() -> str:
        calls[0] += 1
        raise RuntimeError("upstream failure")

    return fetch, calls


class TestFreshHit:
    @pytest.mark.asyncio
    async def test_fresh_entry_returned_without_calling_fetch(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(60)
        fetch, calls = _fetcher("v2")
        result = await cache.get_or_refresh("loc-1", fetch)

        assert result.value == "v1"
        assert result.is_fresh is True
        assert result.is_fallback is False
        assert calls[0] == 0

    @pytest.mark.asyncio
    async def test_first_call_is_a_fresh_result(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        fetch, _ = _fetcher("v1")
        result = await cache.get_or_refresh("loc-1", fetch)
        assert result.value == "v1"
        assert result.is_fresh is True
        assert result.fetched_at == _START


class TestMiss:
    @pytest.mark.asyncio
    async def test_no_entry_calls_fetch_and_populates(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        fetch, calls = _fetcher("v1")
        result = await cache.get_or_refresh("loc-1", fetch)
        assert result.value == "v1"
        assert calls[0] == 1

    @pytest.mark.asyncio
    async def test_miss_with_failing_fetch_propagates(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        fetch, _ = _failing_fetcher()
        with pytest.raises(RuntimeError, match="upstream failure"):
            await cache.get_or_refresh("loc-1", fetch)


class TestStaleHit:
    @pytest.mark.asyncio
    async def test_stale_entry_refreshes_on_successful_fetch(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl=3600.0, stale_ttl=7200.0)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(3601.0)  # past fresh_ttl, still within stale_ttl
        fetch, calls = _fetcher("v2")
        result = await cache.get_or_refresh("loc-1", fetch)

        assert result.value == "v2"
        assert result.is_fresh is True
        assert result.is_fallback is False
        assert calls[0] == 1


class TestExpiry:
    @pytest.mark.asyncio
    async def test_expired_entry_still_refreshes_on_successful_fetch(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl=3600.0, stale_ttl=7200.0)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(7201.0)  # past stale_ttl entirely
        fetch, calls = _fetcher("v2")
        result = await cache.get_or_refresh("loc-1", fetch)

        assert result.value == "v2"
        assert result.is_fresh is True
        assert calls[0] == 1

    @pytest.mark.asyncio
    async def test_expired_entry_gives_no_fallback_on_fetch_failure(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl=3600.0, stale_ttl=7200.0)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(7201.0)
        fetch, _ = _failing_fetcher()
        with pytest.raises(RuntimeError, match="upstream failure"):
            await cache.get_or_refresh("loc-1", fetch)


class TestFallback:
    @pytest.mark.asyncio
    async def test_stale_entry_served_as_fallback_on_fetch_failure(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl=3600.0, stale_ttl=7200.0)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(3601.0)
        fetch, calls = _failing_fetcher()
        result = await cache.get_or_refresh("loc-1", fetch)

        assert result.value == "v1"
        assert result.is_fresh is False
        assert result.is_fallback is True
        assert calls[0] == 1

    @pytest.mark.asyncio
    async def test_fallback_preserves_original_fetched_at(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl=3600.0, stale_ttl=7200.0)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(4000.0)
        fetch, _ = _failing_fetcher()
        result = await cache.get_or_refresh("loc-1", fetch)
        assert result.fetched_at == _START

    @pytest.mark.asyncio
    async def test_subsequent_call_after_fallback_can_still_refresh(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl=3600.0, stale_ttl=7200.0)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(3601.0)
        failing_fetch, _ = _failing_fetcher()
        fallback_result = await cache.get_or_refresh("loc-1", failing_fetch)
        assert fallback_result.is_fallback is True

        succeeding_fetch, calls = _fetcher("v2")
        result = await cache.get_or_refresh("loc-1", succeeding_fetch)
        assert result.value == "v2"
        assert result.is_fresh is True
        assert calls[0] == 1


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_misses_for_same_key_single_flight(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        calls = [0]

        async def fetch() -> str:
            calls[0] += 1
            await asyncio.sleep(0.01)
            return "v1"

        results = await asyncio.gather(
            cache.get_or_refresh("loc-1", fetch),
            cache.get_or_refresh("loc-1", fetch),
            cache.get_or_refresh("loc-1", fetch),
        )

        assert calls[0] == 1
        assert all(r.value == "v1" for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_calls_for_different_keys_both_fetch(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        calls: dict[str, int] = {"loc-1": 0, "loc-2": 0}

        async def make_fetch(key: str, value: str) -> Callable[[], Awaitable[str]]:
            async def fetch() -> str:
                calls[key] += 1
                await asyncio.sleep(0.01)
                return value

            return fetch

        fetch_1 = await make_fetch("loc-1", "v1")
        fetch_2 = await make_fetch("loc-2", "v2")

        results = await asyncio.gather(
            cache.get_or_refresh("loc-1", fetch_1),
            cache.get_or_refresh("loc-2", fetch_2),
        )

        assert calls == {"loc-1": 1, "loc-2": 1}
        assert {r.value for r in results} == {"v1", "v2"}


class TestForceRefresh:
    @pytest.mark.asyncio
    async def test_bypasses_freshness_check_on_a_fresh_entry(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        # Entry is still well within fresh_ttl — get_or_refresh would
        # serve the cached value without calling fetch again.
        force_fetch, force_calls = _fetcher("v2")
        result = await cache.force_refresh("loc-1", force_fetch)

        assert force_calls[0] == 1
        assert result.value == "v2"
        assert result.is_fresh is True
        assert result.is_fallback is False

    @pytest.mark.asyncio
    async def test_populates_an_empty_cache_like_a_miss(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        fetch, calls = _fetcher("v1")

        result = await cache.force_refresh("loc-1", fetch)

        assert calls[0] == 1
        assert result.value == "v1"
        assert result.is_fresh is True

    @pytest.mark.asyncio
    async def test_updates_the_cache_for_subsequent_get_or_refresh_calls(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        force_fetch, _ = _fetcher("v2")
        await cache.force_refresh("loc-1", force_fetch)

        never_called, never_called_count = _fetcher("v3")
        subsequent = await cache.get_or_refresh("loc-1", never_called)
        assert subsequent.value == "v2"
        assert never_called_count[0] == 0

    @pytest.mark.asyncio
    async def test_fetch_failure_falls_back_to_existing_entry(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        failing, _ = _failing_fetcher()
        result = await cache.force_refresh("loc-1", failing)

        assert result.value == "v1"
        assert result.is_fresh is False
        assert result.is_fallback is True

    @pytest.mark.asyncio
    async def test_fetch_failure_with_no_existing_entry_propagates(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        failing, _ = _failing_fetcher()

        with pytest.raises(RuntimeError, match="upstream failure"):
            await cache.force_refresh("loc-1", failing)


class TestConstructorValidation:
    def test_stale_ttl_less_than_fresh_ttl_raises(self) -> None:
        with pytest.raises(ValueError, match="stale_ttl_seconds"):
            SnapshotCache(fresh_ttl_seconds=100.0, stale_ttl_seconds=50.0)

    def test_equal_ttls_allowed(self) -> None:
        SnapshotCache(fresh_ttl_seconds=100.0, stale_ttl_seconds=100.0)


class TestDefaults:
    @pytest.mark.asyncio
    async def test_default_fresh_ttl_is_four_hours(self) -> None:
        clock = _FakeClock(_START)
        cache: SnapshotCache[str] = SnapshotCache(clock=clock)
        populate, _ = _fetcher("v1")
        await cache.get_or_refresh("loc-1", populate)

        clock.advance(4 * 3600.0 - 1.0)
        still_fresh_fetch, still_fresh_calls = _fetcher("v2")
        still_fresh = await cache.get_or_refresh("loc-1", still_fresh_fetch)
        assert still_fresh.value == "v1"
        assert still_fresh_calls[0] == 0

        clock.advance(2.0)
        expired_fetch, expired_calls = _fetcher("v3")
        refreshed = await cache.get_or_refresh("loc-1", expired_fetch)
        assert refreshed.value == "v3"
        assert expired_calls[0] == 1
